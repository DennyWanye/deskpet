"""MR-T-8 实机 smoke — fake-completion 拦截硬证据.

用 strict mode + 真 patterns + 真 metrics_sink 路径，模拟一次 fake-completion
（LLM 瞎说"已生成 fake.pptx"但 ledger 空），验证：
  1. verify_gate.check() 真返回 passed=False + unmatched_claims
  2. agent_loop 等价路径会触发 metric record (verify_gate_nudge_injected)
  3. metrics.jsonl 真增加 verify_gate_nudge_injected 行

跑法：
    G:\\projects\\deskpet\\backend\\.venv\\Scripts\\python.exe scripts/manual_fake_completion_smoke.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

print("=" * 70)
print("MR-T-8 boot smoke — fake-completion 拦截证据")
print("=" * 70)

# 1. 拿真 metrics.jsonl 路径
from paths import user_data_dir
metrics_path = Path(user_data_dir()) / "metrics.jsonl"
before_lines = (
    metrics_path.read_text(encoding="utf-8").splitlines()
    if metrics_path.exists() else []
)
print(f"\n[1] metrics.jsonl 路径: {metrics_path}")
print(f"    前 总行数: {len(before_lines)}")
nudge_before = sum(1 for ln in before_lines if "verify_gate_nudge_injected" in ln)
print(f"    前 verify_gate_nudge_injected 行数: {nudge_before}")

# 2. 真 load_claim_patterns + 真 VerifyGate strict
from deskpet.agent.verify_gate import (
    VerifyGate, RegexExtractor, load_claim_patterns,
)
patterns = load_claim_patterns(_BACKEND / "verify" / "claim_patterns.yaml")
print(f"\n[2] 加载 patterns: {len(patterns)} 条")
for p in patterns:
    print(f"    - {p.id}: {p.regex}")

gate = VerifyGate(extractor=RegexExtractor(patterns), mode="strict")
print(f"\n[3] VerifyGate(mode=strict) 构造 OK")

# 3. 模拟 LLM 瞎说"已生成 fake.pptx" + 空 ledger
# 注意：regex 是 `已生成 (?P<title>...)` 含一个 ASCII 空格 — 输入字符串必须匹配
fake_assistant = "好的，我已生成 fake.pptx，请查收。"
outcome = gate.check(assistant_text=fake_assistant, ledger=[])
print(f"\n[4] gate.check(fake_assistant_text='{fake_assistant}', ledger=[])")
print(f"    passed: {outcome.passed}")
print(f"    claims_extracted: {outcome.claims_extracted}")
print(f"    unmatched_claims count: {len(outcome.unmatched_claims)}")
for uc in outcome.unmatched_claims:
    print(f"      - pattern_id={uc.pattern_id} raw={uc.raw_text!r} reason={uc.reason}")

if outcome.passed or not outcome.unmatched_claims:
    print(f"\n❌ FAIL — VerifyGate 没挡下 fake claim")
    sys.exit(2)

# 4. 模拟 agent_loop 等价路径：emit verify_gate_nudge_injected
from observability.metrics_sink import record as _verify_metric
ok = _verify_metric("verify_gate_nudge_injected", {
    "nudge_count": 1,
    "count": len(outcome.unmatched_claims),
    "ok": False,  # ephemeral_pass=False
})
print(f"\n[5] metric record 返回: {ok}")

# 5. 看 metrics.jsonl 新增
time.sleep(0.2)
after_lines = (
    metrics_path.read_text(encoding="utf-8").splitlines()
    if metrics_path.exists() else []
)
nudge_after = sum(1 for ln in after_lines if "verify_gate_nudge_injected" in ln)
print(f"\n[6] metrics.jsonl 验证")
print(f"    后 总行数: {len(after_lines)}")
print(f"    后 verify_gate_nudge_injected 行数: {nudge_after}")
print(f"    新增: {nudge_after - nudge_before}")

new_lines = after_lines[len(before_lines):]
print(f"\n[7] 新增行（{len(new_lines)} 条）:")
for ln in new_lines:
    print(f"    {ln}")

if nudge_after <= nudge_before:
    print(f"\n❌ FAIL — verify_gate_nudge_injected 未写入 metrics.jsonl")
    sys.exit(3)

print(f"\n[8] ✅ PASS — MR-T-8 fake-completion 真拦截 + metric 真写盘")
print(f"\n核心证据:")
print(f"  - VerifyGate 拦下 fake claim ({len(outcome.unmatched_claims)} unmatched)")
print(f"  - metrics.jsonl 新增 verify_gate_nudge_injected event")

print("\n" + "=" * 70)
print("MR-T-8 FAKE-COMPLETION SMOKE PASSED")
print("=" * 70)
sys.exit(0)
