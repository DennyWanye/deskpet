# Tasks — Companion-Session Context Isolation & Capability Gate

> 单 sprint，3 个独立子系统（D1 retriever / D2 capability_gate / D3 write-scope）+ 回归。勾选规则：单测绿 + 复现回归测试绿 + 终验 real E2E 截图存 evidence/。

## Phase 1 — Memory recall session-affinity (D1)

- [ ] 1.1 写 `backend/tests/test_retriever_session_affinity.py`：same-session=1.0；companion←code 项目类=decay；companion←code 人物类=0.8；code←code=0.5；`decay=1.0` 退回旧行为（TDD 先行）
- [ ] 1.2 实现 `_session_affinity(mem_row, cur_sid, cur_kind, decay) -> float` 纯函数
- [ ] 1.3 retriever RRF 融合阶段乘 affinity；读 `config.toml [companion].memory_cross_session_decay`
- [ ] 1.4 项目类 vs 人物类判定（is_summary / tool_calls / code- 前缀 + 路径特征）单测
- [ ] 1.5 不回归：现有 retriever 测试全绿（同 session 召回行为不变）

## Phase 2 — Capability gate (D2)

- [ ] 2.1 写 `backend/tests/test_capability_gate.py`：图像/视频/语音/3D 生成请求 + 无对应工具 → REFUSE；正常 code/chat 请求 → PASS；新增图像工具后自动 PASS（不写死黑名单）
- [ ] 2.2 实现 `backend/agent/capability_gate.py`：rule-first `classify_request`，歧义走 haiku 兜底，REFUSE 带 reason+alt 文案
- [ ] 2.3 chat handler loop 入口接 capability_gate；REFUSE 直接回文案不进 loop
- [ ] 2.4 `capability_gate_enabled=false` 退回单测

## Phase 3 — Companion write-scope (D3)

- [ ] 3.1 写 `backend/tests/test_companion_write_scope.py`：companion session 写 workspace 内=OK，写 `/path/to/deskpet\...`=拒绝带提示；code session 不受影响
- [ ] 3.2 复用 `code_mode/state.py` session-kind 判定（companion vs code）
- [ ] 3.3 chat handler：companion session 注入 write-scope 到 write_file/edit_file/mcp_filesystem_*/run_shell-mkdir 的 path 校验
- [ ] 3.4 `write_scope_enforced=false` 退回单测

## Phase 4 — 回归 + config + 验收

- [ ] 4.1 `config.toml` 新增 `[companion]` 段（3 开关 + 注释）
- [ ] 4.2 **复现回归测试** `backend/tests/test_regression_2026_05_16_vpn_hijack.py`：mock "code-tyfbt62t VPN" 高 salience 记忆 + `default` session "帮我生成海报图片" → 断言 (a) VPN 记忆 affinity≤decay (b) capability_gate REFUSE (c) 0 个 write_file/mkdir tool_call
- [ ] 4.3 全量 `cd backend && .venv/Scripts/python.exe -m pytest tests/ -q` 绿，0 回归
- [ ] 4.4 终验 real E2E：单实例 deskpet，`default` session 真发"帮我画张海报图"，computer-use 截图证明 graceful refuse + 无文件被建 + backend.log 无 mkdir/write_file（[feedback_real_test]）
- [ ] 4.5 evidence 存 `openspec/changes/2026-05-16-companion-context-isolation/evidence/`
- [ ] 4.6 `openspec validate 2026-05-16-companion-context-isolation` 通过
