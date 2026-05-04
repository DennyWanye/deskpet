## 1. Foundations & contracts (Stage 0 — must finish before any parallel work)

- [ ] 1.1 Create new package skeletons: `backend/deskpet/agent/tool_use/`, `backend/deskpet/skills/parser/`, `backend/deskpet/skills/marketplace/`, `backend/deskpet/permissions/`, `backend/deskpet/plugins/`
- [ ] 1.2 Add dependencies to `backend/pyproject.toml`: `pyyaml>=6.0`, `watchdog>=4.0`, `httpx>=0.27`, `jsonschema>=4.20`
- [ ] 1.3 Add frontend dependencies to `frontend/package.json`: nothing new mandatory; verify `react@18` + existing IPC client
- [ ] 1.4 Define shared TypeScript types in `frontend/src/types/skillPlatform.ts` (PermissionRequest, ToolUseEvent, SkillMeta, PluginManifest)
- [ ] 1.5 Define shared Python types in `backend/deskpet/types/skill_platform.py` mirroring 1.4
- [ ] 1.6 Update `config.toml.example` with new sections: `[permissions]`, `[permissions.deny]`, `[plugins]`, `[marketplace]`, `[agent_loop]`
- [ ] 1.7 Write contract tests in `backend/tests/contracts/test_ipc_v2.py` defining all new IPC message shapes (these tests will fail until handlers exist; they pin the contract)

## 2. Stage A: Tool use protocol & OS tools (TDD)

### 2.1 ToolRegistry extension (RED)
- [ ] 2.1.1 Write failing test `tests/unit/test_tool_registry_v2.py::test_extended_fields_default` — register tool without new fields, expect defaults
- [ ] 2.1.2 Write failing test `test_to_openai_schema_shape` — assert `[{type:function, function:{name, description, parameters}}]`
- [ ] 2.1.3 Write failing test `test_to_anthropic_schema_shape`
- [ ] 2.1.4 Write failing test `test_namespace_collision_raises`
- [ ] 2.1.5 Write failing test `test_filter_by_permission_category`
- [ ] 2.1.6 Write failing test `test_execute_tool_calls_permission_gate`
- [ ] 2.1.7 Write failing test `test_handler_exception_caught`

### 2.2 ToolRegistry implementation (GREEN)
- [ ] 2.2.1 Extend `ToolSpec` dataclass in `backend/deskpet/agent/tool_registry.py` with `description_for_llm`, `input_schema_json`, `permission_category`, `source`, `dangerous`
- [ ] 2.2.2 Add `to_openai_schema(names=None, filter_categories=None)` method
- [ ] 2.2.3 Add `to_anthropic_schema(names=None)` method
- [ ] 2.2.4 Add `to_ollama_schema()` alias
- [ ] 2.2.5 Add `execute_tool(name, params, session_id)` async wrapper that awaits `PermissionGate.check`, runs handler with timeout, returns `{ok, result|error}` envelope
- [ ] 2.2.6 Implement plugin name auto-prefix `<plugin>:<tool>` and conflict detection
- [ ] 2.2.7 Run 2.1 tests until all GREEN

### 2.3 PermissionGate (RED → GREEN)
- [ ] 2.3.1 Write failing tests `tests/unit/test_permission_gate.py` covering all 6 scenarios in `specs/permission-gate/spec.md` (default-allow, prompt, deny, cache, timeout, deny-rule precedence)
- [ ] 2.3.2 Implement `backend/deskpet/permissions/gate.py` with `PermissionDecision` dataclass and `PermissionGate.check(category, params, session_id)` async method
- [ ] 2.3.3 Implement deny-pattern loader from `config.toml::[permissions.deny]`
- [ ] 2.3.4 Implement session-scoped allow cache (key = `(session_id, category, params_hash)`)
- [ ] 2.3.5 Wire `permission_request` / `permission_response` IPC via `ControlWebSocket` (new handler in `backend/deskpet/server/ws_handlers.py`)
- [ ] 2.3.6 Implement 60s timeout via `asyncio.wait_for`
- [ ] 2.3.7 Implement sensitive-path upgrade for `read_file` → `read_file_sensitive`
- [ ] 2.3.8 Run 2.3.1 tests GREEN

### 2.4 OS tools (RED → GREEN, parallelizable per tool)
- [ ] 2.4.1 [PARALLEL-A] Write tests + implement `read_file` tool in `backend/deskpet/tools/os_tools/read_file.py`
- [ ] 2.4.2 [PARALLEL-A] Write tests + implement `write_file`
- [ ] 2.4.3 [PARALLEL-A] Write tests + implement `edit_file`
- [ ] 2.4.4 [PARALLEL-B] Write tests + implement `list_directory`
- [ ] 2.4.5 [PARALLEL-B] Write tests + implement `run_shell` (with deny-pattern + timeout)
- [ ] 2.4.6 [PARALLEL-B] Write tests + implement `web_fetch` (with scheme guard)
- [ ] 2.4.7 [PARALLEL-C] Write tests + implement `desktop_create_file` (cross-platform desktop resolution)
- [ ] 2.4.8 Register all 7 OS tools at backend startup with `permission_category` set per spec
- [ ] 2.4.9 Run integration test `test_os_tools_e2e` — happy path for each tool with mocked permission allow

### 2.5 Tool-use loop in agent (RED → GREEN)
- [ ] 2.5.1 Write failing tests `tests/unit/test_agent_loop_tool_use.py` covering all 4 scenarios in `specs/agent-loop/spec.md` (single call, multi-turn, max-turns, cancel)
- [ ] 2.5.2 Write failing tests for streaming event ordering (`tool_use_request` before `tool_use_result`)
- [ ] 2.5.3 Write failing tests for regex fallback compat
- [ ] 2.5.4 Implement `backend/deskpet/agent/tool_use_loop.py::run_tool_use_loop(messages, registry, session_id, provider)` — loops until LLM returns final text or hits 25-turn / 5-min budget
- [ ] 2.5.5 Modify `agent.chat_stream` in `backend/deskpet/agent/loop.py` to dispatch on `tool_use_protocol` config: `auto`/`openai_tool_calls`/`anthropic_blocks`/`regex` (legacy fallback)
- [ ] 2.5.6 Update OpenAI provider to pass `tools=registry.to_openai_schema(...)` and parse `tool_calls` response field
- [ ] 2.5.7 Update Anthropic provider to pass `tools=registry.to_anthropic_schema(...)` and parse `content[].type=='tool_use'` blocks
- [ ] 2.5.8 Verify Ollama provider works via OpenAI-compat path
- [ ] 2.5.9 Run all Stage A tests GREEN; verify existing 679 tests still pass

### 2.6 Frontend permission popup
- [ ] 2.6.1 Write component test for `PermissionPopup` (Vitest + RTL) covering all 3 buttons and ESC behavior
- [ ] 2.6.2 Implement `frontend/src/components/PermissionPopup.tsx` with category-specific styling per spec table
- [ ] 2.6.3 Wire IPC: subscribe to `permission_request`, render modal, send `permission_response` on click
- [ ] 2.6.4 Add modal-overlay backdrop blocking chat input
- [ ] 2.6.5 Manual UI smoke test via Tauri dev: trigger `desktop_create_file`, see popup, click Yes, see file created

### 2.7 Stage A integration smoke
- [ ] 2.7.1 Write `scripts/e2e_stage_a.py` — start backend, send chat "create todo.txt on my desktop with content milk", auto-approve permission via test-only IPC, assert file exists
- [ ] 2.7.2 Run `pytest backend/tests/` — must be ≥ 679 + new tests, all green
- [ ] 2.7.3 Manual real-test: start full app, ask via chat, watch popup appear, click Yes, verify desktop file
- [ ] 2.7.4 Capture screenshot evidence per MEMORY.md "Real Test 真实测试" rule

## 3. Stage B: SKILL.md parser & dual loader (TDD)

### 3.1 Parser (RED → GREEN)
- [ ] 3.1.1 Write failing tests `tests/unit/test_skill_md_parser.py` for all 6 scenarios in `specs/skill-md-parser/spec.md`
- [ ] 3.1.2 Implement `backend/deskpet/skills/parser/parse_skill_md.py` with PyYAML frontmatter parsing
- [ ] 3.1.3 Implement frontmatter v1 fields (name/description/when_to_use/argument-hint/disable-model-invocation/user-invocable/allowed-tools/paths/context/hooks/version)
- [ ] 3.1.4 Implement variable substitution (`${CLAUDE_SKILL_DIR}`, `${CLAUDE_SESSION_ID}`, `$ARGUMENTS`, `$N`)
- [ ] 3.1.5 Implement inline shell injection `` !`cmd` `` with timeout + cwd=skill_dir + error inlining
- [ ] 3.1.6 Implement `allowed-tools` string parser (paren-aware split)
- [ ] 3.1.7 Run 3.1.1 tests GREEN

### 3.2 SkillLoader dispatch + hot-reload (RED → GREEN)
- [ ] 3.2.1 Write failing tests `tests/unit/test_skill_loader_v2.py` for dual-format dispatch, location priority, hot-reload
- [ ] 3.2.2 Modify `backend/deskpet/skills/loader.py` to detect format (frontmatter vs legacy) and dispatch
- [ ] 3.2.3 Add 4 search roots: bundled, user, project, plugin (priority order resolved at list-time)
- [ ] 3.2.4 Add `watchdog` Observer (PollingObserver fallback) with 1s debounce
- [ ] 3.2.5 Emit `skill_list_changed` IPC event on reload
- [ ] 3.2.6 Add metadata expansion (source, overrides, etc.) to `list_skills()`
- [ ] 3.2.7 Implement skill execution context (`SkillExecutionContext` with skill_dir, session_id, args)
- [ ] 3.2.8 Run 3.2.1 tests GREEN; verify legacy `deskpet/skills/builtin/*` still loads

### 3.3 Stage B integration smoke
- [ ] 3.3.1 Drop a sample `%APPDATA%/deskpet/skills/sample-greeting/SKILL.md` with frontmatter
- [ ] 3.3.2 Run `scripts/e2e_stage_b.py` — assert SkillLoader picks it up and frontmatter fields are parsed
- [ ] 3.3.3 Edit the SKILL.md, assert hot-reload picks up change within 2s
- [ ] 3.3.4 Run full test suite — must stay green

## 4. Stage C: Marketplace UI + safety (TDD)

### 4.1 Marketplace backend (RED → GREEN)
- [ ] 4.1.1 Write failing tests `tests/unit/test_marketplace_ipc.py` for all 4 IPC handlers
- [ ] 4.1.2 Implement `backend/deskpet/skills/marketplace/registry_client.py` (fetch + 1h cache)
- [ ] 4.1.3 Implement `backend/deskpet/skills/marketplace/installer.py` with git-clone (3 URL forms)
- [ ] 4.1.4 Implement manifest safety check (allow-list validator, denylist tool blocker)
- [ ] 4.1.5 Implement staging-dir → confirm → finalize flow
- [ ] 4.1.6 Wire 4 IPC handlers in `ws_handlers.py`: `skill_marketplace_list`, `skill_list_installed`, `skill_install_from_url`, `skill_uninstall`
- [ ] 4.1.7 Run 4.1.1 tests GREEN

### 4.2 Marketplace UI
- [ ] 4.2.1 Component test for `SkillStorePanel` (3 tabs)
- [ ] 4.2.2 Implement `frontend/src/components/SkillStorePanel.tsx` with Installed/Marketplace/Add-by-URL tabs
- [ ] 4.2.3 Render skills list with name, description, install button
- [ ] 4.2.4 Implement install confirmation modal (highlights sensitive permission categories in red)
- [ ] 4.2.5 Implement uninstall confirmation
- [ ] 4.2.6 Add entrypoint button in `SettingsPanel.tsx`
- [ ] 4.2.7 Manual UI smoke: open panel, install a known-safe skill from a real GitHub URL, verify it appears in Installed tab

### 4.3 Stage C integration smoke
- [ ] 4.3.1 Stand up a local mock registry.json server (or commit fixture)
- [ ] 4.3.2 Run `scripts/e2e_stage_c.py` — list, install (mock), uninstall round-trip
- [ ] 4.3.3 Verify hot-reload picks up newly-installed skill without app restart
- [ ] 4.3.4 Real-test screenshot evidence

## 5. Stage D: Plugin system (TDD)

### 5.1 PluginManager (RED → GREEN)
- [ ] 5.1.1 Write failing tests `tests/unit/test_plugin_manager.py` for all 6 requirements in `specs/plugin-system/spec.md`
- [ ] 5.1.2 Implement `backend/deskpet/plugins/manager.py` with manifest loader, semver validation
- [ ] 5.1.3 Implement plugin skill loading (delegates to SkillLoader with `source="plugin:<name>"`)
- [ ] 5.1.4 Implement plugin MCP server registration (merge into MCPManager config, plugin overrides + warns)
- [ ] 5.1.5 Implement enable/disable IPC handlers + config persistence
- [ ] 5.1.6 Run 5.1.1 tests GREEN

### 5.2 Scaffold generator
- [ ] 5.2.1 Implement `scripts/scaffold_plugin.py <name>` generating `plugin.json`, `README.md`, `skills/example/SKILL.md`
- [ ] 5.2.2 Smoke test: scaffold a plugin, drop into `%APPDATA%/deskpet/plugins/`, enable via IPC, assert skill appears

### 5.3 Stage D integration smoke
- [ ] 5.3.1 Run `scripts/e2e_stage_d.py` — scaffold a plugin with one skill + one mock MCP server, enable, verify both work
- [ ] 5.3.2 Disable plugin, verify clean unload
- [ ] 5.3.3 Real-test screenshot evidence

## 6. System-wide hardening & docs

- [ ] 6.1 Run full pytest suite — assert ≥ 679 + new tests, all green
- [ ] 6.2 Run frontend Vitest suite — all green
- [ ] 6.3 Run `tsc --noEmit` and Python `ruff check` — clean
- [ ] 6.4 Update `README.md` with skill platform overview
- [ ] 6.5 Update `docs/PERMISSIONS.md` with category table + deny-pattern examples
- [ ] 6.6 Update `docs/SKILLS.md` with SKILL.md format reference + variable substitution + examples
- [ ] 6.7 Update `docs/PLUGINS.md` with manifest format + scaffold + install instructions
- [ ] 6.8 Add `CHANGELOG.md` entry for this release
- [ ] 6.9 Verify subprocess BGE-M3 (P4-S19) still works under new agent loop

## 7. Final acceptance

- [ ] 7.1 Manual real-test: from cold start, ask "create todo.txt with '吃饭买菜' on my desktop" — popup appears, click Yes, file created on real Desktop with correct UTF-8 content
- [ ] 7.2 Manual real-test: install a known community skill from GitHub via marketplace UI, invoke it via chat, verify it works
- [ ] 7.3 Manual real-test: scaffold a plugin, enable, exercise its skill via chat
- [ ] 7.4 Capture all real-test screenshots and append to `docs/EVIDENCE/skill-platform-v1.md`
- [ ] 7.5 Run `openspec validate deskpet-skill-platform --strict` — must pass
- [ ] 7.6 Commit + push; ready for `/opsx:archive`
