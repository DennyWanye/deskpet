# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""记忆系统性能基线（严测 Phase 4，P2 — 与 G6 同期收尾）。

## 目的
spec §4 Phase 4 = "G6 边界 + **性能基线**"。本文件给检索 / 写入热路径建立
**延迟基线 + 回归护栏**。

## 设计原则（重要）
- **不是微基准（micro-benchmark）**：CI 机器算力波动大，断言用极宽松上限
  （1s，对 500 行 LIKE 扫描有 ~100x 余量），只抓**算法级退化**（O(n²) /
  误退化成全表扫 / 漏索引导致的爆炸），不抓毫秒级抖动。
- **打印实测值**：每条 print 出 median 延迟 + 命中数，作为人读基线（CI log 可查），
  数值漂移靠人看，红线只在退化时触发。
- **全 mock / 进程内**：mock embedder 是 md5 哈希（`_encode_mock` → `np.stack`，
  无子进程），500 次 encode 也快 → 不挂 `model_required`，正常 CI 跑。
- **median of 5**：先 warmup 一次（吃掉冷启动 / 首次连接开销），再取 5 次中位数，
  降偶发抖动。
"""
from __future__ import annotations

import asyncio
import statistics
import time
from pathlib import Path

import pytest
import pytest_asyncio

import deskpet.memory.memory_v2_schema as _schema
from deskpet.memory.facts import FactsStore
from deskpet.memory.workspace import WorkspaceMemoryStore

# 基线语料规模（代表性中等负载；够暴露算法退化，又不拖慢 CI）。
N_FACTS = 500
N_WS = 500
N_VEC = 300  # 向量暴力扫描语料（带 embedding）
# 宽松上限：回归护栏，非微基准。500 行 LIKE 实测应在个位数 ms，1s = ~100x 余量。
CEILING_S = 1.0


@pytest.fixture(autouse=True)
def _reset_schema_cache() -> None:
    """重置 memory_v2_schema 模块级缓存 + 重建 _lock（见 G6 同款，跨 loop 隔离）。"""
    _schema._reset_cache_for_tests()
    _schema._lock = asyncio.Lock()
    yield
    _schema._reset_cache_for_tests()


def _median_latency(samples: list[float]) -> float:
    return statistics.median(samples)


# ----------------------------------------------------------------------
# PERF.1 — facts.search（分词 OR LIKE 路）延迟基线
# ----------------------------------------------------------------------
@pytest.mark.asyncio
async def test_perf_facts_search_baseline(tmp_path: Path) -> None:
    """N=500 facts 下 facts.search（分词路）中位延迟 —— 防退化护栏。"""
    fs = FactsStore(tmp_path / "f.db", embedder=None)
    for i in range(N_FACTS):
        await fs.upsert(
            category="preference", subject="user", key=f"k{i}",
            value=f"用户偏好 项目{i} 喜欢喝茶 和咖啡 编号{i}", confidence=0.8,
            source_msg_id=i, evidence=f"evidence {i}",
        )
    await fs.search("喝茶 咖啡 偏好", limit=20)  # warmup
    samples: list[float] = []
    hits = []
    for _ in range(5):
        t0 = time.perf_counter()
        hits = await fs.search("喝茶 咖啡 偏好", limit=20)
        samples.append(time.perf_counter() - t0)
    median = _median_latency(samples)
    print(
        f"\n[PERF] facts.search(分词) N={N_FACTS} "
        f"median={median*1000:.1f}ms hits={len(hits)}"
    )
    assert hits, "基线 query 应命中（分词 OR LIKE 起效）"
    assert median < CEILING_S, (
        f"facts.search 退化: {median*1000:.0f}ms > {CEILING_S*1000:.0f}ms "
        f"上限（N={N_FACTS}）。疑似算法退化（全表扫 / O(n²)）。"
    )


# ----------------------------------------------------------------------
# PERF.2 — workspace.recall（分词 OR LIKE 路）延迟基线
# ----------------------------------------------------------------------
@pytest.mark.asyncio
async def test_perf_workspace_recall_baseline(tmp_path: Path) -> None:
    """N=500 workspace 条目下 recall 中位延迟 —— 防退化护栏。"""
    ws = WorkspaceMemoryStore(tmp_path / "ws.db")
    for i in range(N_WS):
        await ws.record_action(
            session_id="s1", path=f"src/components/Widget{i}.tsx",
            action="write", content=f"export const Widget{i} = () => null",
        )
    await ws.recall("Widget", session_id="s1")  # warmup
    samples: list[float] = []
    hits = []
    for _ in range(5):
        t0 = time.perf_counter()
        hits = await ws.recall("Widget", session_id="s1")
        samples.append(time.perf_counter() - t0)
    median = _median_latency(samples)
    print(
        f"\n[PERF] workspace.recall(分词) N={N_WS} "
        f"median={median*1000:.1f}ms hits={len(hits)}"
    )
    assert hits, "基线 query 'Widget' 应命中 path"
    assert median < CEILING_S, (
        f"workspace.recall 退化: {median*1000:.0f}ms > {CEILING_S*1000:.0f}ms "
        f"上限（N={N_WS}）。"
    )


# ----------------------------------------------------------------------
# PERF.3 — facts.vector_search（暴力余弦扫描）延迟基线
# ----------------------------------------------------------------------
@pytest.mark.asyncio
async def test_perf_facts_vector_search_baseline(tmp_path: Path) -> None:
    """N=300 带 embedding 的 facts 下 vector_search 暴力余弦中位延迟。

    向量召回是 O(N) 全量余弦扫描（无 ANN 索引），这条钉死它在中等语料下
    仍在可接受范围 —— 防止将来误把维度 / 拷贝开销放大成 O(N²)。

    注：`_embed_fact` 对 `is_mock()` embedder 显式返回 None（mock 向量无意义，
    facts.py:250）→ 不能用 mock embedder 填 embedding。这里用**非 mock 的
    确定性 fake embedder**（不定义 is_mock → 真写 embedding 列），进程内、快。
    暴力扫描延迟只与 N + 维度有关，与向量语义无关 → fake 向量完全够测。
    """
    import hashlib

    import numpy as np

    class _FakeVecEmbedder:
        """非 mock 确定性 embedder：md5 种子 → 稳定 1024 维 float32 向量。"""

        dim = 1024

        async def encode(self, texts: list[str]) -> np.ndarray:
            rows = []
            for t in texts:
                seed = int.from_bytes(
                    hashlib.md5(t.encode("utf-8")).digest()[:4], "little"
                )
                rng = np.random.default_rng(seed)
                rows.append(rng.standard_normal(self.dim))
            return np.stack(rows).astype(np.float32)

    e = _FakeVecEmbedder()
    fs = FactsStore(tmp_path / "f.db", embedder=e)
    for i in range(N_VEC):
        await fs.upsert(
            category="profile", subject="user", key=f"k{i}",
            value=f"事实条目 {i} 关于宠物和爱好", confidence=0.8,
            source_msg_id=i, evidence=f"e{i}",
        )
    qvec = await e.encode(["宠物 爱好"])
    await fs.vector_search(qvec[0], limit=20)  # warmup
    samples: list[float] = []
    rows = []
    for _ in range(5):
        t0 = time.perf_counter()
        rows = await fs.vector_search(qvec[0], limit=20)
        samples.append(time.perf_counter() - t0)
    median = _median_latency(samples)
    print(
        f"\n[PERF] facts.vector_search(暴力余弦) N={N_VEC} "
        f"median={median*1000:.1f}ms rows={len(rows)}"
    )
    # 暴力扫描返回全量打分行（rank 语义，非排除），应非空
    assert rows, "vector_search 应返回打分行（暴力扫描全量）"
    assert median < CEILING_S, (
        f"vector_search 退化: {median*1000:.0f}ms > {CEILING_S*1000:.0f}ms "
        f"上限（N={N_VEC}）。疑似 O(N²) / 多余拷贝。"
    )


# ----------------------------------------------------------------------
# PERF.4 — facts 写入吞吐基线（ingest throughput）
# ----------------------------------------------------------------------
@pytest.mark.asyncio
async def test_perf_facts_ingest_throughput_baseline(tmp_path: Path) -> None:
    """N=200 facts upsert（无 embedder）总耗时 —— 写入吞吐基线。

    每次 upsert 开新 aiosqlite 连接（设计如此），这条记录该模式下的吞吐，
    给将来"是否需要连接池"的决策留基线数字。宽松上限只防爆炸式退化。
    """
    fs = FactsStore(tmp_path / "f.db", embedder=None)
    n = 200
    t0 = time.perf_counter()
    for i in range(n):
        await fs.upsert(
            category="preference", subject="user", key=f"k{i}",
            value=f"value {i}", confidence=0.8,
            source_msg_id=i, evidence="x",
        )
    elapsed = time.perf_counter() - t0
    rate = n / elapsed if elapsed > 0 else float("inf")
    print(
        f"\n[PERF] facts.upsert ingest N={n} "
        f"total={elapsed*1000:.0f}ms rate={rate:.0f}/s"
    )
    # 极宽松：200 条 < 20s（>10/s）。只抓爆炸式退化，非吞吐优化目标。
    assert elapsed < 20.0, (
        f"facts ingest 退化: {n} 条耗 {elapsed:.1f}s（rate={rate:.0f}/s）。"
    )
