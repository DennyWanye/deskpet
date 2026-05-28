# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""MR-T-1 实机 boot smoke — verify_gate_init 真写 metrics.jsonl 硬证据.

用途：用户 goal "必须有 boot smoke + metrics.jsonl 真出现 verify_* event 才算
WI-T2.1 完成" 的最硬可控证据 — 不用启 Tauri UI / 不用 LLM credentials，
但走的是 100% 生产代码路径：
  - 真 backend.config.load_config()
  - 真 main.build_agent(...) 工厂
  - 真 observability.metrics_sink.record()
  - 真 %APPDATA%\\deskpet\\metrics.jsonl 写入

跟 backend 启动时区别：build_agent 在 chat handler 里调（不是 module init），
所以光启 backend 进程不会触发 — 必须真发 chat 或手动调本脚本。

跑法：
    G:\\projects\\deskpet\\backend\\.venv\\Scripts\\python.exe scripts/manual_verify_smoke.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

# 加入 backend 路径让 main 能被 import
_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

print("=" * 70)
print("MR-T-1 boot smoke — verify_gate_init 真接电证据")
print("=" * 70)

# 1. 真 load_config —— 读 %APPDATA%\deskpet\config.toml
from config import load_config

cfg = load_config()
print(f"\n[1] load_config() OK")
print(f"    verify_gate_mode (config.toml 真值): {cfg.tools.verifier.verify_gate_mode}")
print(f"    claim_patterns_file: {cfg.tools.verifier.claim_patterns_file}")

# 2. 强制 shadow mode（不动 config.toml，仅本进程内）— 触发 build_agent 真接电路径
cfg.tools.verifier.verify_gate_mode = "shadow"
cfg.tools.verifier.emit_receipts = True
print(f"\n[2] 临时改 verify_gate_mode → shadow（仅本进程）")

# 3. 看 metrics.jsonl 当前末尾
from paths import user_data_dir
metrics_path = Path(user_data_dir()) / "metrics.jsonl"
print(f"\n[3] metrics.jsonl 路径: {metrics_path}")
print(f"    本次 smoke 前 size: {metrics_path.stat().st_size if metrics_path.exists() else 0} bytes")
before_lines = (
    metrics_path.read_text(encoding="utf-8").splitlines()
    if metrics_path.exists() else []
)
print(f"    本次 smoke 前 总行数: {len(before_lines)}")
verify_before = sum(1 for ln in before_lines if "verify_" in ln)
print(f"    本次 smoke 前 verify_* 行数: {verify_before}")

# 4. 真调 build_agent —— mock 掉非 verifier 依赖，但 verify_gate / metrics_sink 走真
print(f"\n[4] 调真 main.build_agent(...) 工厂")
from main import build_agent

mock_rs = MagicMock(name="ReceiptStore")
mock_rs.load_session.return_value = []

agent = build_agent(
    cfg,
    llm_registry=MagicMock(name="llm_registry"),
    tool_registry=MagicMock(name="tool_registry"),
    context_manager=MagicMock(name="context_manager"),
    receipt_store_getter=lambda: mock_rs,
)

print(f"    agent.verify_gate: {agent.verify_gate}")
print(f"    agent.verify_gate.mode: {getattr(agent.verify_gate, 'mode', 'N/A')}")
print(f"    agent.verify_gate.extractor: {type(agent.verify_gate.extractor).__name__}")
print(f"    patterns loaded: {len(agent.verify_gate.extractor.patterns)}")
print(f"    agent.receipt_store: {agent.receipt_store}")
print(f"    agent.max_verify_nudges: {agent.max_verify_nudges}")

# 5. flush metrics + diff
time.sleep(0.2)  # MetricsSink 是 immediate write，但给一点 OS flush 余量
after_lines = (
    metrics_path.read_text(encoding="utf-8").splitlines()
    if metrics_path.exists() else []
)
print(f"\n[5] metrics.jsonl 写入验证")
print(f"    smoke 后 总行数: {len(after_lines)}")
verify_after = sum(1 for ln in after_lines if "verify_" in ln)
print(f"    smoke 后 verify_* 行数: {verify_after}")
print(f"    本次 smoke 新增 verify_* 行数: {verify_after - verify_before}")

# 6. 打印新增行
new_lines = after_lines[len(before_lines):]
if new_lines:
    print(f"\n[6] 新增 metrics 行（{len(new_lines)} 条）:")
    for i, ln in enumerate(new_lines, 1):
        print(f"    {i}. {ln}")
else:
    print(f"\n[6] ❌ 无新增行 — verify_gate_init metric 没被 emit")
    sys.exit(1)

# 7. 硬断言
verify_new = [ln for ln in new_lines if "verify_" in ln]
if not verify_new:
    print(f"\n[7] ❌ FAIL — 新增行中没有 verify_* event")
    sys.exit(2)
print(f"\n[7] ✅ PASS — verify_* event 真写入 metrics.jsonl ({len(verify_new)} 条)")
print(f"\n核心证据行:")
for ln in verify_new:
    print(f"    {ln}")

print("\n" + "=" * 70)
print("MR-T-1 BOOT SMOKE PASSED — verify_gate 真接电生产路径")
print("=" * 70)
