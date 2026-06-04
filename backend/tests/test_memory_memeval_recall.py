# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""DeskPet-MemEval v0 — 字面 vs 改写召回对照评测（2026-06-02 审计 #3）。

## 为什么要这个
G5 已证明 `eval_gate` 的 hit@5 是**字面(FTS)驱动**（mock embedder == real
embedder，Δ=0）—— 那个指标**不反映语义召回能力**，FTS 字面命中会让它虚高。
业界（LongMemEval/LoCoMo）防自欺的核心手段：**同一事实用"字面同词"和"语义改写"
各问一遍，对比 recall**。改写版 recall 暴跌 → 证明召回在吃 FTS 字面红利、dense
路没真正工作。

## 本评测做什么
~18 条双语 fact，每条配两个 query：
- `q_literal`：与 value **有词/子串重叠** → FTS/LIKE 也能命中。
- `q_paraphrase`：与 value **零词重叠**（换说法 / 跨语言）→ **只有 dense 向量
  召回能命中**。
全部 fact 互为干扰项灌进一个**真 BGE-M3** 的 FactsStore，对每个 query 跑
`vector_search` 取 top-5，算 ground-truth 是否命中（Recall@5）。

断言（回归护栏 + 反自欺）：
- 字面 Recall@5 高（sanity：召回栈通畅）。
- **改写 Recall@5 ≥ 阈值** —— 真 BGE-M3 下改写应也能召回（dense 真工作）。
  若暴跌 → 暴露语义召回弱（量化 / 模型 / 维度），是必须报告的真问题。
- 改写 Recall > 0（否则 dense 完全失效＝致命）。

挂 `model_required`：需真 BGE-M3。模型路径用 `_model_path.resolve_bge_m3()`
稳健解析（用户把数据迁到 F 盘后 C: 路径会空，见该 helper）。找不到模型 →
`pytest.skip`（**绝不**退回 mock 哈希向量假装通过——那正是 G5 戳破的自欺）。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

import deskpet.memory.memory_v2_schema as _schema
from deskpet.memory.embedder import Embedder
from deskpet.memory.facts import FactsStore

# tests/ 下同级 helper。手动把本文件目录加进 sys.path —— 不依赖 pytest 的
# import 模式是否把 tests/ 插进路径（实测 bare import 会 ModuleNotFoundError）。
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _model_path import resolve_bge_m3  # noqa: E402

pytestmark = pytest.mark.model_required

# 评测集：每项 = (key, value, q_literal, q_paraphrase)。设计要点：
#   - q_literal 必与 value 有共同子串（FTS/LIKE 能命中）
#   - q_paraphrase 必与 value 零共同词（换说法/跨语言 → 只 dense 能命中）
DATASET = [
    ("pet_cat",   "我家养了一只叫旺财的橘猫",                  "旺财",        "主人的宠物是什么"),
    ("job",       "用户在一家游戏公司做后端工程师",            "后端工程师",   "他的职业"),
    ("drink",     "用户最爱喝乌龙茶",                          "乌龙茶",      "主人平时爱喝的饮品"),
    ("allergy",   "用户对花生严重过敏",                        "花生",        "不能碰的食物"),
    ("birthday",  "用户的生日是三月十五号",                    "三月十五",    "出生那天的日期"),
    ("city",      "The user lives in Seattle by the lake",     "Seattle",     "用户住在哪个城市"),
    ("dog",       "用户还养了一只金毛犬叫 Lucky",              "金毛",        "他家的狗"),
    ("sport",     "用户假期喜欢去攀岩",                        "攀岩",        "休息日的户外运动"),
    ("music",     "用户单曲循环周杰伦的歌",                    "周杰伦",      "他常听的歌手"),
    ("car",       "用户开一辆蓝色的特斯拉 Model 3",            "特斯拉",      "主人的座驾"),
    ("language",  "用户母语是粤语，也会普通话",                "粤语",        "他从小说的方言"),
    ("fear",      "用户特别怕打雷",                            "打雷",        "让他害怕的天气现象"),
    ("study",     "用户在读计算机硕士",                        "计算机硕士",   "正在攻读的学位"),
    ("food_like", "用户无辣不欢，最爱川菜",                    "川菜",        "偏好的菜系口味"),
    ("family",    "用户有一个上小学的女儿",                    "女儿",        "家里的孩子"),
    ("travel",    "用户去年去了趟冰岛看极光",                  "冰岛",        "去过的旅行目的地"),
    ("plant",     "The user keeps a small succulent on the desk", "succulent", "桌上养的植物"),
    ("schedule",  "用户习惯凌晨两点才睡",                      "凌晨两点",    "他通常的作息时间"),
]

_K = 5
# 阈值：首跑后据实测略低于观测值设回归护栏。改写阈值是反自欺关键指标。
_LITERAL_RECALL_MIN = 0.80
_PARAPHRASE_RECALL_MIN = 0.55


@pytest.fixture(autouse=True)
def _reset_schema_cache():
    _schema._reset_cache_for_tests()
    _schema._lock = asyncio.Lock()
    yield
    _schema._reset_cache_for_tests()


async def _recall_at_k(
    fs: FactsStore, embedder: Embedder, query: str, target_key: str, k: int
) -> bool:
    qv = await embedder.encode([query])
    rows = await fs.vector_search(qv[0], limit=k)
    return any(r.get("key") == target_key for r in rows[:k])


def _mean(xs: list[bool]) -> float:
    return sum(1 for x in xs if x) / len(xs) if xs else 0.0


@pytest.mark.asyncio
async def test_memeval_literal_vs_paraphrase_recall(tmp_path: Path) -> None:
    """字面 vs 改写 Recall@5 对照（单测自包含：一次 warmup + seed + 全断言）。"""
    model = resolve_bge_m3()
    if model is None:
        pytest.skip(
            "BGE-M3 模型未在任何已知位置找到（C:/F: 等）；设 DESKPET_BGE_M3_DIR "
            "或装模型后再跑。绝不退回 mock 假装语义召回。"
        )

    e = Embedder(model_path=model, use_mock_when_missing=False)
    await e.warmup()
    assert not e.is_mock(), f"应加载真 BGE-M3（{model}），不是 mock"
    try:
        fs = FactsStore(tmp_path / "memeval.db", embedder=e)
        for i, (key, value, _ql, _qp) in enumerate(DATASET):
            await fs.upsert(
                category="profile", subject="user", key=key, value=value,
                confidence=0.9, source_msg_id=i, evidence=value,
            )

        lit_hits = [
            await _recall_at_k(fs, e, ql, key, _K)
            for (key, _v, ql, _qp) in DATASET
        ]
        par_hits = [
            await _recall_at_k(fs, e, qp, key, _K)
            for (key, _v, _ql, qp) in DATASET
        ]
        lit, par = _mean(lit_hits), _mean(par_hits)
        lit_miss = [DATASET[i][0] for i, h in enumerate(lit_hits) if not h]
        par_miss = [DATASET[i][0] for i, h in enumerate(par_hits) if not h]

        print(
            f"\n[MemEval] model={model}\n"
            f"[MemEval] 字面  Recall@{_K} = {lit:.3f} ({sum(lit_hits)}/{len(lit_hits)}) "
            f"miss={lit_miss}\n"
            f"[MemEval] 改写  Recall@{_K} = {par:.3f} ({sum(par_hits)}/{len(par_hits)}) "
            f"miss={par_miss}\n"
            f"[MemEval] gap(字面-改写) = {lit - par:+.3f}  "
            f"(gap 越小 dense 越接近字面能力；大 gap = 严重依赖字面命中)"
        )

        # sanity：召回栈通畅
        assert lit >= _LITERAL_RECALL_MIN, (
            f"字面 Recall@{_K}={lit:.3f} < {_LITERAL_RECALL_MIN} —— 召回栈可能坏了"
        )
        # 反自欺关键：改写（零词重叠）下 dense 真工作
        assert par >= _PARAPHRASE_RECALL_MIN, (
            f"改写 Recall@{_K}={par:.3f} < {_PARAPHRASE_RECALL_MIN} —— dense 语义召回弱。"
            f"未命中 {par_miss}。这是真问题（量化/模型/维度），不是调阈值能掩盖的。"
        )
        # 致命下限：改写召回为 0 等于 dense 完全没用
        assert par > 0.0, "改写 Recall 为 0 → dense 语义召回完全失效（致命）"
    finally:
        await e.close()
