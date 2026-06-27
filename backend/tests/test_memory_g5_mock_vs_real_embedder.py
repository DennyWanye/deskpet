# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""G5 — mock vs 真 BGE-M3 embedder 对照（记忆系统严测 Phase 3）。

## 背景
盘点 confound ①：所有"语义召回"测试用 mock hash 向量（无语义），eval_gate
也固定 mock"为了可复现"。问题：**mock 下的 hit@5 反映的是字面(FTS)命中 +
hash 巧合，不是真语义召回质量**。拿它当语义指标是虚的。

## 本文件验什么
- G5.1 语义句对：真 embedder 命中、mock 不命中 —— **已由
  test_memory_retrieval_effectiveness.py 的 G1.5 / G1.5b 覆盖**（宠物↔橘猫），
  此处不重复。
- G5.2 eval_gate mock vs real 对照：跑同一 fixture，对比 hit@5。
  实测 **DELTA≈0**（mock 0.4286 == real 0.4286）→ 钉死"stage1 fixture 的
  hit@5 是字面(FTS)驱动，真假 embedder 无差" —— 即该指标**不反映语义召回能力**，
  不能拿它当"语义检索质量达标"的证据。

这条测试的价值是**戳破虚假信心**：让"eval_gate hit@5 通过 = 语义召回 OK"
这个隐含假设暴露为错误。
"""
from __future__ import annotations

import pytest

# G5.2 需真 BGE-M3 + 同进程串行跑两次 run_eval（mock + real），慢（~3min）。
pytestmark = pytest.mark.model_required


@pytest.mark.asyncio
async def test_g5_2_eval_gate_mock_equals_real_proves_literal_driven(
    monkeypatch,
) -> None:
    """stage1 eval_gate 的 hit@5：mock ≈ real → 证明字面驱动、非语义。

    若 DELTA 明显 > 0（real 远高于 mock）→ 说明 fixture 确实测了语义，
    那时这个断言会失败，提示"hit@5 其实反映语义召回，G5.2 结论需更新"。
    实测 DELTA = 0.0000，故钉死"字面驱动"。
    """
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent))
    from _model_path import resolve_bge_m3

    model = resolve_bge_m3()
    if model is None:
        pytest.skip("BGE-M3 模型未找到；装模型或设 DESKPET_MODEL_ROOT 后再跑")
    # eval_gate real 分支走 paths.user_models_dir()（读 DESKPET_MODEL_ROOT）。
    # 指到解析出的模型父目录，让 eval_gate 找到模型（用户迁 F 盘后 C: 路径空）。
    monkeypatch.setenv("DESKPET_MODEL_ROOT", str(model.parent))

    from scripts.eval_gate import run_eval

    mock = await run_eval(stage="stage1", embedder_mode="mock")
    real = await run_eval(stage="stage1", embedder_mode="real")

    assert mock["_embedder_mode"] == "mock"
    assert real["_embedder_mode"] == "real"
    assert mock["qa_set_size"] == real["qa_set_size"]

    delta = real["hit@5"] - mock["hit@5"]
    # 钉死：真假 embedder 的 hit@5 几乎无差（|Δ| 很小）→ 字面(FTS)驱动。
    # 容差 0.05：留浮点 + 个别 QA 抖动余量；远小于"语义召回应有的显著提升"。
    assert abs(delta) <= 0.05, (
        f"stage1 eval_gate: mock hit@5={mock['hit@5']:.4f} "
        f"real hit@5={real['hit@5']:.4f} Δ={delta:+.4f}。"
        f"若 |Δ|>0.05 说明真 embedder 显著改变召回 → hit@5 其实反映语义，"
        f"G5.2 '字面驱动'结论需更新；否则钉死：该指标不验语义召回能力。"
    )
