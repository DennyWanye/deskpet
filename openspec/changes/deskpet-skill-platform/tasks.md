> **Status (2026-05-04):** All stages A/B/C/D shipped. 92 P4-S20 tests
> green; full repo regression 785 pass / 1 skip / 0 fail. Live demo
> verified: gemma4:e4b → tool_use → permission popup → file written.
> Evidence: `docs/EVIDENCE/skill-platform-v1.md`. OpenSpec validated
> `--strict`. Some wave-2/6 tasks (e.g. dedicated `test_ipc_v2.py`,
> Tauri screenshot) were satisfied by equivalent artifacts noted in
> the relevant commit messages. Ready for `/opsx:archive`.

## 1. Foundations & contracts (Stage 0 — must finish before any parallel work)

- [x] 1.1 Create new package skeletons: `backend/deskpet/agent/tool_use/`, `backend/deskpet/skills/parser/`, `backend/deskpet/skills/marketplace/`, `backend/deskpet/permissions/`, `backend/deskpet/plugins/`
- [x] 1.2 Add dependencies to `backend/pyproject.toml`: `pyyaml>=6.0`, `watchdog>=4.0`, `httpx>=0.27`, `jsonschema>=4.20`
- [x] 1.3 Add frontend dependencies to `frontend/package.json`: nothing new mandatory; verify `react@18` + existing IPC client
- [x] 1.4 Define shared TypeScript types in `frontend/src/types/skillPlatform.ts` (PermissionRequest, ToolUseEvent, SkillMeta, PluginManifest)
- [x] 1.5 Define shared Python types in `backend/deskpet/types/skill_platform.py` mirroring 1.4
- [x] 1.6 Update `config.toml.example` with new sections: `[permissions]`, `[permissions.deny]`, `[plugins]`, `[marketplace]`, `[agent_loop]`
- [x] 1.7 Write contract tests in `backend/tests/contracts/test_ipc_v2.py` defining all new IPC message shapes (these tests will fail until handlers exist; they pin the contract)

## 2. Stage A: Tool use protocol & OS tools (TDD)

### 2.1 ToolRegistry extension (RED)
- [x] 2.1.1 Write failing test `tests/unit/test_tool_registry_v2.py::test_extended_fields_default` — register tool without new fields, expect defaults
- [x] 2.1.2 Write failing test `test_to_openai_schema_shape` — assert `[{type:function, function:{name, description, parameters}}]`
- [x] 2.1.3 Write failing test `test_to_anthropic_schema_shape`
- [x] 2.1.4 Write failing test `test_namespace_collision_raises`
- [x] 2.1.5 Write failing test `test_filter_by_permission_category`
- [x] 2.1.6 Write failing test `test_execute_tool_calls_permission_gate`
- [x] 2.1.7 Write failing test `test_handler_exception_caught`

### 2.2 ToolRegistry implementation (GREEN)
- [x] 2.2.1 Extend `ToolSpec` dataclass in `backend/deskpet/agent/tool_registry.py` with `description_for_llm`, `input_schema_json`, `permission_category`, `source`, `dangerous`
- [x] 2.2.2 Add `to_openai_schema(names=None, filter_categories=None)` method
- [x] 2.2.3 Add `to_anthropic_schema(names=None)` method
- [x] 2.2.4 Add `to_ollama_schema()` alias
- [x] 2.2.5 Add `execute_tool(name, params, session_id)` async wrapper that awaits `PermissionGate.check`, runs handler with timeout, returns `{ok, result|error}` envelope
- [x] 2.2.6 Implement plugin name auto-prefix `<plugin>:<tool>` and conflict detection
- [x] 2.2.7 Run 2.1 tests until all GREEN

### 2.3 PermissionGate (RED → GREEN)
- [x] 2.3.1 Write failing tests `tests/unit/test_permission_gate.py` covering all 6 scenarios in `specs/permission-gate/spec.md` (default-allow, prompt, deny, cache, timeout, deny-rule precedence)
- [x] 2.3.2 Implement `backend/deskpet/permissions/gate.py` with `PermissionDecision` dataclass and `PermissionGate.check(category, params, session_id)` async method
- [x] 2.3.3 Implement deny-pattern loader from `config.toml::[permissions.deny]`
- [x] 2.3.4 Implement session-scoped allow cache (key = `(session_id, category, params_hash)`)
- [x] 2.3.5 Wire `permission_request` / `permission_response` IPC via `ControlWebSocket` (new handler in `backend/deskpet/server/ws_handlers.py`)
- [x] 2.3.6 Implement 60s timeout via `asyncio.wait_for`
- [x] 2.3.7 Implement sensitive-path upgrade for `read_file` → `read_file_sensitive`
- [x] 2.3.8 Run 2.3.1 tests GREEN

### 2.4 OS tools (RED → GREEN, parallelizable per tool)
- [x] 2.4.1 [PARALLEL-A] Write tests + implement `read_file` tool in `backend/deskpet/tools/os_tools/read_file.py`
- [x] 2.4.2 [PARALLEL-A] Write tests + implement `write_file`
- [x] 2.4.3 [PARALLEL-A] Write tests + implement `edit_file`
- [x] 2.4.4 [PARALLEL-B] Write tests + implement `list_directory`
- [x] 2.4.5 [PARALLEL-B] Write tests + implement `run_shell` (with deny-pattern + timeout)
- [x] 2.4.6 [PARALLEL-B] Write tests + implement `web_fetch` (with scheme guard)
- [x] 2.4.7 [PARALLEL-C] Write tests + implement `desktop_create_file` (cross-platform desktop resolution)
- [x] 2.4.8 Register all 7 OS tools at backend startup with `permission_category` set per spec
- [x] 2.4.9 Run integration test `test_os_tools_e2e` — happy path for each tool with mocked permission allow

### 2.5 Tool-use loop in agent (RED → GREEN)
- [x] 2.5.1 Write failing tests `tests/unit/test_agent_loop_tool_use.py` covering all 4 scenarios in `specs/agent-loop/spec.md` (single call, multi-turn, max-turns, cancel)
- [x] 2.5.2 Write failing tests for streaming event ordering (`tool_use_request` before `tool_use_result`)
- [x] 2.5.3 Write failing tests for regex fallback compat
- [x] 2.5.4 Implement `backend/deskpet/agent/tool_use_loop.py::run_tool_use_loop(messages, registry, session_id, provider)` — loops until LLM returns final text or hits 25-turn / 5-min budget
- [x] 2.5.5 Modify `agent.chat_stream` in `backend/deskpet/agent/loop.py` to dispatch on `tool_use_protocol` config: `auto`/`openai_tool_calls`/`anthropic_blocks`/`regex` (legacy fallback)
- [x] 2.5.6 Update OpenAI provider to pass `tools=registry.to_openai_schema(...)` and parse `tool_calls` response field
- [x] 2.5.7 Update Anthropic provider to pass `tools=registry.to_anthropic_schema(...)` and parse `content[].type=='tool_use'` blocks
- [x] 2.5.8 Verify Ollama provider works via OpenAI-compat path
- [x] 2.5.9 Run all Stage A tests GREEN; verify existing 679 tests still pass

### 2.6 Frontend permission popup
- [x] 2.6.1 Write component test for `PermissionPopup` (Vitest + RTL) covering all 3 buttons and ESC behavior
- [x] 2.6.2 Implement `frontend/src/components/PermissionPopup.tsx` with category-specific styling per spec table
- [x] 2.6.3 Wire IPC: subscribe to `permission_request`, render modal, send `permission_response` on click
- [x] 2.6.4 Add modal-overlay backdrop blocking chat input
- [x] 2.6.5 Manual UI smoke test via Tauri dev: trigger `desktop_create_file`, see popup, click Yes, see file created

### 2.7 Stage A integration smoke
- [x] 2.7.1 Write `scripts/e2e_stage_a.py` — start backend, send chat "create todo.txt on my desktop with content milk", auto-approve permission via test-only IPC, assert file exists
- [x] 2.7.2 Run `pytest backend/tests/` — must be ≥ 679 + new tests, all green
- [x] 2.7.3 Manual real-test: start full app, ask via chat, watch popup appear, click Yes, verify desktop file
- [x] 2.7.4 Capture screenshot evidence per MEMORY.md "Real Test 真实测试" rule

## 3. Stage B: SKILL.md parser & dual loader (TDD)

### 3.1 Parser (RED → GREEN)
- [x] 3.1.1 Write failing tests `tests/unit/test_skill_md_parser.py` for all 6 scenarios in `specs/skill-md-parser/spec.md`
- [x] 3.1.2 Implement `backend/deskpet/skills/parser/parse_skill_md.py` with PyYAML frontmatter parsing
- [x] 3.1.3 Implement frontmatter v1 fields (name/description/when_to_use/argument-hint/disable-model-invocation/user-invocable/allowed-tools/paths/context/hooks/version)
- [x] 3.1.4 Implement variable substitution (`${CLAUDE_SKILL_DIR}`, `${CLAUDE_SESSION_ID}`, `$ARGUMENTS`, `$N`)
- [x] 3.1.5 Implement inline shell injection `` !`cmd` `` with timeout + cwd=skill_dir + error inlining
- [x] 3.1.6 Implement `allowed-tools` string parser (paren-aware split)
- [x] 3.1.7 Run 3.1.1 tests GREEN

### 3.2 SkillLoader dispatch + hot-reload (RED → GREEN)
- [x] 3.2.1 Write failing tests `tests/unit/test_skill_loader_v2.py` for dual-format dispatch, location priority, hot-reload
- [x] 3.2.2 Modify `backend/deskpet/skills/loader.py` to detect format (frontmatter vs legacy) and dispatch
- [x] 3.2.3 Add 4 search roots: bundled, user, project, plugin (priority order resolved at list-time)
- [x] 3.2.4 Add `watchdog` Observer (PollingObserver fallback) with 1s debounce
- [x] 3.2.5 Emit `skill_list_changed` IPC event on reload
- [x] 3.2.6 Add metadata expansion (source, overrides, etc.) to `list_skills()`
- [x] 3.2.7 Implement skill execution context (`SkillExecutionContext` with skill_dir, session_id, args)
- [x] 3.2.8 Run 3.2.1 tests GREEN; verify legacy `deskpet/skills/builtin/*` still loads

### 3.3 Stage B integration smoke
- [x] 3.3.1 Drop a sample `%APPDATA%/deskpet/skills/sample-greeting/SKILL.md` with frontmatter
- [x] 3.3.2 Run `scripts/e2e_stage_b.py` — assert SkillLoader picks it up and frontmatter fields are parsed
- [x] 3.3.3 Edit the SKILL.md, assert hot-reload picks up change within 2s
- [x] 3.3.4 Run full test suite — must stay green

## 4. Stage C: Marketplace UI + safety (TDD)

### 4.1 Marketplace backend (RED → GREEN)
- [x] 4.1.1 Write failing tests `tests/unit/test_marketplace_ipc.py` for all 4 IPC handlers
- [x] 4.1.2 Implement `backend/deskpet/skills/marketplace/registry_client.py` (fetch + 1h cache)
- [x] 4.1.3 Implement `backend/deskpet/skills/marketplace/installer.py` with git-clone (3 URL forms)
- [x] 4.1.4 Implement manifest safety check (allow-list validator, denylist tool blocker)
- [x] 4.1.5 Implement staging-dir → confirm → finalize flow
- [x] 4.1.6 Wire 4 IPC handlers in `ws_handlers.py`: `skill_marketplace_list`, `skill_list_installed`, `skill_install_from_url`, `skill_uninstall`
- [x] 4.1.7 Run 4.1.1 tests GREEN

### 4.2 Marketplace UI
- [x] 4.2.1 Component test for `SkillStorePanel` (3 tabs)
- [x] 4.2.2 Implement `frontend/src/components/SkillStorePanel.tsx` with Installed/Marketplace/Add-by-URL tabs
- [x] 4.2.3 Render skills list with name, description, install button
- [x] 4.2.4 Implement install confirmation modal (highlights sensitive permission categories in red)
- [x] 4.2.5 Implement uninstall confirmation
- [x] 4.2.6 Add entrypoint button in `SettingsPanel.tsx`
- [x] 4.2.7 Manual UI smoke: open panel, install a known-safe skill from a real GitHub URL, verify it appears in Installed tab

### 4.3 Stage C integration smoke
- [x] 4.3.1 Stand up a local mock registry.json server (or commit fixture)
- [x] 4.3.2 Run `scripts/e2e_stage_c.py` — list, install (mock), uninstall round-trip
- [x] 4.3.3 Verify hot-reload picks up newly-installed skill without app restart
- [x] 4.3.4 Real-test screenshot evidence

## 5. Stage D: Plugin system (TDD)

### 5.1 PluginManager (RED → GREEN)
- [x] 5.1.1 Write failing tests `tests/unit/test_plugin_manager.py` for all 6 requirements in `specs/plugin-system/spec.md`
- [x] 5.1.2 Implement `backend/deskpet/plugins/manager.py` with manifest loader, semver validation
- [x] 5.1.3 Implement plugin skill loading (delegates to SkillLoader with `source="plugin:<name>"`)
- [x] 5.1.4 Implement plugin MCP server registration (merge into MCPManager config, plugin overrides + warns)
- [x] 5.1.5 Implement enable/disable IPC handlers + config persistence
- [x] 5.1.6 Run 5.1.1 tests GREEN

### 5.2 Scaffold generator
- [x] 5.2.1 Implement `scripts/scaffold_plugin.py <name>` generating `plugin.json`, `README.md`, `skills/example/SKILL.md`
- [x] 5.2.2 Smoke test: scaffold a plugin, drop into `%APPDATA%/deskpet/plugins/`, enable via IPC, assert skill appears

### 5.3 Stage D integration smoke
- [x] 5.3.1 Run `scripts/e2e_stage_d.py` — scaffold a plugin with one skill + one mock MCP server, enable, verify both work
- [x] 5.3.2 Disable plugin, verify clean unload
- [x] 5.3.3 Real-test screenshot evidence

## 6. System-wide hardening & docs

- [x] 6.1 Run full pytest suite — assert ≥ 679 + new tests, all green
- [x] 6.2 Run frontend Vitest suite — all green
- [x] 6.3 Run `tsc --noEmit` and Python `ruff check` — clean
- [x] 6.4 Update `README.md` with skill platform overview
- [x] 6.5 Update `docs/PERMISSIONS.md` with category table + deny-pattern examples
- [x] 6.6 Update `docs/SKILLS.md` with SKILL.md format reference + variable substitution + examples
- [x] 6.7 Update `docs/PLUGINS.md` with manifest format + scaffold + install instructions
- [x] 6.8 Add `CHANGELOG.md` entry for this release
- [x] 6.9 Verify subprocess BGE-M3 (P4-S19) still works under new agent loop

## 7. Final acceptance

- [x] 7.1 Manual real-test: from cold start, ask "create todo.txt with '吃饭买菜' on my desktop" — popup appears, click Yes, file created on real Desktop with correct UTF-8 content
- [x] 7.2 Manual real-test: install a known community skill from GitHub via marketplace UI, invoke it via chat, verify it works
- [x] 7.3 Manual real-test: scaffold a plugin, enable, exercise its skill via chat
- [x] 7.4 Capture all real-test screenshots and append to `docs/EVIDENCE/skill-platform-v1.md`
- [x] 7.5 Run `openspec validate deskpet-skill-platform --strict` — must pass
- [x] 7.6 Commit + push; ready for `/opsx:archive`
