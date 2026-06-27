# Evidence: P2P3 capability-gate + companion-write-scope

**When**: 2026-05-16 UTC+8
**Who**: subagent (Phase 2 §D2 + Phase 3 §D3, coalesced — both touch backend/main.py)
**What we tested**: 无能力请求在进 agent loop 前 graceful refuse；companion session 写盘限定 workspace 根；两个 feature flag 各自 rollback。单测层全覆盖（含 2026-05-16 复现场景）。

## Steps

1. STRICT TDD — 先写 `backend/tests/test_capability_gate.py`（22 cases）跑出 RED（`ModuleNotFoundError: No module named 'agent.capability_gate'`），再实现 `backend/agent/capability_gate.py` 转 GREEN。
2. 同法 `backend/tests/test_companion_write_scope.py`（20 cases）先 RED（`No module named 'agent.write_scope'`），再实现 `backend/agent/write_scope.py` + 在 write_file/edit_file/run_shell 注入 `_write_scope_root` 校验。
3. chat handler（`backend/main.py` `_run_chat`）loop 入口前接 capability_gate：REFUSE → 落库一条 assistant + 推 `chat_v2_final`，直接 `return` 不进 loop；sentinel（`<<auto_resume>>` 等）不过门。
4. chat handler `else`（非 code session = companion）分支注入 `_write_scope_root`（默认 `<user_data_dir>/workspace`）；`write_scope_enforced=false` 时 `set_session_context(_sid, None)` 显式回退。
5. 跑两个目标测试文件 + 受影响的 `test_deskpet_tools_file.py` 回归 + 全量后端套件。

## Observation

- 目标测试（verbose 末尾）：
  ```
  test_companion_write_scope.py::test_write_file_inside_scope_root_succeeds PASSED [ 90%]
  test_companion_write_scope.py::test_write_file_no_scope_root_legacy_free_write PASSED [ 92%]
  test_companion_write_scope.py::test_edit_file_honors_injected_scope_root PASSED [ 95%]
  test_companion_write_scope.py::test_run_shell_honors_injected_scope_root_for_mkdir PASSED [ 97%]
  test_companion_write_scope.py::test_run_shell_no_mkdir_unaffected PASSED [100%]
  ============================= 42 passed in 0.92s ==============================
  ```
- 全量后端套件（排除 vector_worker 计时 flaky，按 AGENTS.md 约定）：
  ```
  ........................................................................ [ 90%]
  ........................................................................ [ 95%]
  ...........................................................s             [100%]
  1346 passed, 10 skipped, 4 deselected in 67.47s (0:01:07)
  ```
  基线 953 → 现 1346（含本批 +42 新测 + 此前 phase 累积），无回归。

## Conclusion

- ✅ Scenario "Image-generation request with no image tool is refused"：`classify_request("你能帮我生成一个海报图片嘛？", available_tools=[无图像工具])` → `Verdict.REFUSE`，reason 含"图"，带 alternative；chat handler 不进 loop、零 write_file/mkdir tool_call。
- ✅ Scenario "Normal code request passes the gate"：`重构 server/db.js 的连接池` 等 → `PASS`。
- ✅ Scenario "Gate auto-adapts when a capability is added"：available_tools 含 `generate_image` / `mcp_media_generate_video` → 同样的图像/视频请求 `PASS`（live 读 ToolRegistry，无硬编码黑名单）。
- ✅ Scenario "Gate disabled restores legacy behavior"：`enabled=False`（`[companion].capability_gate_enabled=false`）→ 永远 `PASS`。
- ✅ Scenario "Companion session blocked from writing into a code repo"：`write_file({path:"/path/to/deskpet\\backend\\vpn-cli\\...", _write_scope_root:<ws>})` → `{ok:false}`，文案 = spec 原文，文件未创建。
- ✅ Scenario "Companion session may write inside workspace"：`<ws>/notes.md` 写入成功。
- ✅ Scenario "Code session unaffected"：code session 走 `if _in_code_mode` 分支只注入 `_project_root`，从不注入 `_write_scope_root` → `scope_root=None` → 不拦（test_write_file_no_scope_root_legacy_free_write 等价覆盖）。
- ✅ Scenario "write_scope_enforced=false restores legacy"：handler `else` 分支 `set_session_context(_sid, None)`；`write_scope_check(path, scope_root=None)` 永远返回 None。
- ✅ run_shell 只拦写盘类命令（mkdir/touch/cp/tee/重定向）越界；读类（echo/ls/cat）不受影响（test_run_shell_no_mkdir_unaffected）。
- Deviations:
  - `desktop_create_file` 未纳入 write-scope。理由：design §D3 显式列出的受限工具是 `write_file/edit_file/mcp_filesystem_*/run_shell mkdir`，不含 desktop_create_file；该工具语义就是"在桌面建文件"（单层文件名、已禁路径分隔符 `..`，天然安全），强行 scope 到 workspace 会破坏其唯一用途且违反 no-sandbox 原则。
  - `mcp_filesystem_*` 工具由 MCP server 进程执行（非本进程 handler），其 path 校验路径与 builtin os_tools 不同；本批通过 `execute_tool` 注入 `_write_scope_root` 已覆盖 builtin 写盘工具，MCP filesystem server 自身根目录已是 `<user_data_dir>/workspace`（main.py 启动时 mkdir 该目录并作为 server root），即 MCP filesystem 工具天然就被它自己的 server root 限制在 workspace 内 —— 与本 change 目标一致，无需额外注入。
- Followup:
  - 终验 real E2E（`default` session 真发"帮我画张海报"，截图证明 graceful refuse 且无文件被建）属 lead/Phase 终验范畴，本 subagent 不启动 dev server（按 Sprint Contract 硬规则）。
  - `[companion]` config 段（capability_gate_enabled / write_scope_enforced / workspace_root）由 Phase 4 owns；本实现以安全默认读取（`[companion]` 缺失时 enabled=true / enforced=true）。
