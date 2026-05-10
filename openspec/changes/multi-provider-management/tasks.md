# Tasks (TDD-ordered)

每个 phase 严格 **red → green → refactor**：先写失败的测试，再写最少代码让它绿。Phase 之间必须实测验证（pytest + vitest 全绿，0 regression）才算闭环。

Live E2E（需要重启 deskpet）放在最后单独一个 phase，可以延后到第二天做。

## Phase 0 — SessionDB migration + 数据层（独立，无依赖）

### 0.1 写 migration SQL

- [ ] 0.1 创建 `backend/deskpet/memory/migrations/006_p5s2_code_session_provider.sql` —— 新表 `code_session_provider (base_session_id PK, provider_id TEXT, preferred_model TEXT, updated_at REAL)` + `PRAGMA user_version = 14`

### 0.2 SessionDB 方法

- [ ] 0.2 写测试 `tests/test_p5s2_code_session_provider_db.py::test_set_then_get_binding` —— set + get 往返
- [ ] 0.3 测试 `test_get_unbound_returns_null` —— 没设置过的 sid 返回 `{provider_id: None, preferred_model: None}`
- [ ] 0.4 测试 `test_clear_binding_deletes_row` —— set 全 None 删行
- [ ] 0.5 测试 `test_migration_idempotent` —— 跑两次 migration 不出错
- [ ] 0.6 在 `backend/deskpet/memory/session_db.py` 加 `get_code_session_provider_binding(sid)` + `set_code_session_provider_binding(sid, provider_id, preferred_model)` → 测试绿
- [ ] 0.7 整合测试 `test_migration_v13_to_v14_works` —— 模拟现有 v13 db 跑 migration 升 v14，老表不丢数据

### 0.3 验收

- [ ] 0.8 pytest 这 6 个测试全绿；不能掉之前 baseline (1048)

---

## Phase 1 — LLMProviderRegistry + config.toml schema + migration

### 1.1 Registry 数据结构

- [ ] 1.1 写测试 `tests/test_p5s2_provider_registry.py::test_add_provider_persists` —— add → list 回来 + toml 写入
- [ ] 1.2 测试 `test_remove_provider` —— remove → list 少一个 + keychain entry 删掉（mock keychain）
- [ ] 1.3 测试 `test_reorder_changes_chain` —— reorder → get_chain() 顺序变
- [ ] 1.4 测试 `test_set_enabled_false_removes_from_chain` —— disable → list 还有但 chain 没了
- [ ] 1.5 测试 `test_get_chain_empty_raises_no_provider_configured`
- [ ] 1.6 测试 `test_get_chain_filters_disabled`
- [ ] 1.7 测试 `test_get_chain_stable_on_equal_priority` —— priority 相同时按 insertion order
- [ ] 1.8 测试 `test_list_providers_redacts_api_key` —— 任何 list call 返回 api_key="********"，没明文
- [ ] 1.9 测试 `test_add_provider_unique_id_validation` —— 重复 id raise
- [ ] 1.10 测试 `test_add_provider_kebab_case_validation` —— 不合法 id raise

### 1.2 实现

- [ ] 1.11 创建 `backend/llm/provider_registry.py` —— `LLMProviderRegistry` 类 + `ProviderEntry` dataclass + `NoProviderConfiguredError` exception → 测试绿
- [ ] 1.12 实现 `_persist_to_toml()` —— 用 `tomli_w` (or hand-write，已有 helper)，原子写（tmp + rename）
- [ ] 1.13 实现 `_keychain_save(provider_id, api_key)` / `_keychain_load(provider_id)` —— 用 keyring 库，已存在依赖

### 1.3 Migration

- [ ] 1.14 测试 `test_migrate_legacy_llm_local_to_providers` —— 老 toml 自动转
- [ ] 1.15 测试 `test_migration_idempotent` —— 已有 providers 的 toml 不变
- [ ] 1.16 测试 `test_migration_handles_missing_keychain_key` —— keychain 空时仍创建 entry + warning
- [ ] 1.17 实现 `_migrate_legacy_provider_config()` 在 `backend/main.py` lifespan 头部 → 测试绿

### 1.4 验收

- [ ] 1.18 pytest registry + migration 13 个测试全绿
- [ ] 1.19 evidence: `evidence/1.18-registry-tests.md` —— pytest 输出 + 简述 migration 逻辑

---

## Phase 2 — IPC handlers (settings_providers_*)

### 2.1 5 个 settings_providers_* 消息

- [ ] 2.1 测试 `tests/test_p5s2_ipc_providers.py::test_list_request_returns_sanitized` —— api_key=********
- [ ] 2.2 测试 `test_add_validates_uniqueness` —— 重复 id 返回 error 消息
- [ ] 2.3 测试 `test_add_validates_required_fields` —— 缺 base_url 返回 error
- [ ] 2.4 测试 `test_update_partial_patch` —— 只改 priority，其它字段不变
- [ ] 2.5 测试 `test_update_api_key_writes_keychain` —— 改 key 时 keychain 更新；不传 key 时 keychain 不变
- [ ] 2.6 测试 `test_remove_cleanup` —— 删 provider 后 keychain entry + code_session_provider rows 都清
- [ ] 2.7 测试 `test_reorder_validates_complete_set` —— ordered_ids 缺 id 报错
- [ ] 2.8 测试 `test_providers_changed_broadcasts_to_all_conns` —— 2 个 mock ws 都收到
- [ ] 2.9 在 `backend/main.py` ws handler 中加 5 个 case → 测试绿

### 2.2 2 个 code_session_set_* 消息

- [ ] 2.10 测试 `test_set_provider_binding_persists` —— ws send → SessionDB 行
- [ ] 2.11 测试 `test_set_provider_null_clears_binding`
- [ ] 2.12 测试 `test_set_model_alone_keeps_chain_global` —— 只 set model 不 set provider
- [ ] 2.13 添加 `code_session_set_provider` + `code_session_set_model` ws handler → 测试绿

### 2.3 验收

- [ ] 2.14 pytest 13 个 IPC 测试全绿
- [ ] 2.15 evidence: `evidence/2.14-ipc-tests.md`

---

## Phase 3 — AgentLoop chain support + per-session resolution

### 3.1 Chain 调用

- [ ] 3.1 测试 `tests/test_p5s2_agent_loop_provider_chain.py::test_first_provider_succeeds_others_unused`
- [ ] 3.2 测试 `test_transient_error_falls_to_next_provider`
- [ ] 3.3 测试 `test_permanent_error_does_NOT_fall_to_next_provider` —— P5-S2 Phase 2 永久错误不跨 provider 重试
- [ ] 3.4 测试 `test_all_providers_fail_emits_all_providers_failed_error_event`
- [ ] 3.5 测试 `test_empty_chain_emits_no_provider_configured_immediately`
- [ ] 3.6 测试 `test_provider_chain_fallback_ws_event_emitted` —— 切 provider 时 emit 诊断 event
- [ ] 3.7 修改 `agent_loop.py` 的 LLM 调用处 —— 接受 `provider_chain` 参数 + chain walking 逻辑 → 测试绿

### 3.2 Per-session 解析

- [ ] 3.8 测试 `tests/test_p5s2_session_provider_resolution.py::test_pinned_session_returns_single_chain`
- [ ] 3.9 测试 `test_unbound_session_returns_global_chain`
- [ ] 3.10 测试 `test_preferred_model_only_overrides_model_field`
- [ ] 3.11 测试 `test_pinned_to_deleted_provider_falls_back_to_chain`
- [ ] 3.12 测试 `test_companion_session_skips_db_lookup`
- [ ] 3.13 实现 `resolve_provider_for_session(base_sid)` helper（在 `main.py` 或新 `backend/llm/resolution.py`）→ 测试绿

### 3.3 backwards compat

- [ ] 3.14 测试 `test_legacy_single_provider_callers_still_work` —— 老 `chat_with_fallback(provider=X)` 调用仍跑通
- [ ] 3.15 wrap legacy callers 到 chain 形式 → 测试绿

### 3.4 验收

- [ ] 3.16 pytest 15 个 agent_loop chain 测试全绿
- [ ] 3.17 evidence: `evidence/3.16-chain-tests.md`

---

## Phase 4 — Frontend Settings panel + 拖拽

### 4.1 依赖 + 基础渲染

- [ ] 4.1 `npm install @dnd-kit/core @dnd-kit/sortable @dnd-kit/utilities` (in tauri-app/) — 如果还没装
- [ ] 4.2 vitest `tauri-app/src/components/SettingsProviders.test.tsx::test_renders_provider_list` —— 给 mock providers 渲染列表
- [ ] 4.3 vitest `test_redacted_api_key_shown_as_stars` —— api_key 字段显示 ********
- [ ] 4.4 创建 `tauri-app/src/components/SettingsProviders.tsx` —— 列表 + 头部添加按钮 + 每行删除按钮 + 启用开关 → 测试绿

### 4.2 拖拽排序

- [ ] 4.5 vitest `test_drag_reorder_emits_ws_message` —— mock @dnd-kit; 模拟 sortable end → ws send `settings_providers_reorder`
- [ ] 4.6 vitest `test_keyboard_reorder_works` —— 用 keyboard sensor 模拟 ↑↓
- [ ] 4.7 集成 @dnd-kit + SortableContext → 测试绿

### 4.3 添加 / 编辑 modal

- [ ] 4.8 vitest `test_add_modal_validates_required_fields_clientside`
- [ ] 4.9 vitest `test_edit_modal_pre_fills_existing_values_except_api_key`
- [ ] 4.10 vitest `test_save_emits_correct_ws_message`
- [ ] 4.11 创建 `tauri-app/src/components/AddProviderModal.tsx` → 测试绿

### 4.4 接到 SettingsPanel

- [ ] 4.12 修改 `tauri-app/src/components/SettingsPanel.tsx` 加 "LLM Providers" 区段 mount `<SettingsProviders />`
- [ ] 4.13 vitest `test_providers_changed_event_re_renders_settings`
- [ ] 4.14 修改 `tauri-app/src/code-panel/ws.ts` 派发 4 个新 events 到 store

### 4.5 验收

- [ ] 4.15 vitest 全绿（baseline 54 → 65+）+ tsc --noEmit 0 errors
- [ ] 4.16 evidence: `evidence/4.15-settings-ui.md`（vitest 输出 + 关键截图思路；live 截图 Phase 6 做）

---

## Phase 5 — Code panel per-session provider 下拉

### 5.1 卡片 dropdown

- [ ] 5.1 vitest `tauri-app/src/code-panel/SessionGridView.test.tsx::test_card_renders_provider_dropdown` —— mock card props 含 provider_id
- [ ] 5.2 vitest `test_default_dropdown_value_is_global_chain` —— 没绑定时显示"Global Chain"
- [ ] 5.3 vitest `test_select_provider_emits_ws_set_message` —— 选 provider 发 ws
- [ ] 5.4 vitest `test_pinned_session_shows_lock_icon` —— provider_id 非 null 显示 🔒
- [ ] 5.5 vitest `test_provider_removed_falls_card_to_global_with_toast`
- [ ] 5.6 修改 `SessionGridView.tsx` 卡片头部加 dropdown → 测试绿

### 5.2 prefer_model modal (optional sub-action)

- [ ] 5.7 vitest `test_change_model_modal_works`
- [ ] 5.8 简单 modal "改 model" 输入 string + emit `code_session_set_model` → 测试绿

### 5.3 sessionsStore 字段

- [ ] 5.9 vitest `test_session_state_includes_provider_binding_fields`
- [ ] 5.10 加 `provider_id?: string|null` + `preferred_model?: string|null` 到 Session type → 测试绿
- [ ] 5.11 vitest `test_code_sessions_list_response_populates_binding_fields`
- [ ] 5.12 修改 `ws.ts` 把 binding fields 写进 store → 测试绿

### 5.4 验收

- [ ] 5.13 vitest 全绿 (54 → 75+) + tsc 0 errors
- [ ] 5.14 evidence: `evidence/5.13-code-panel-ui.md`

---

## Phase 6 — Live E2E (需要重启 deskpet — 第二天做)

⚠️ 这个 phase 必须重启 deskpet。如果 deskpet 正在跑（用户在测稳定性），**延后到第二天**。

### 6.1 启动 + Settings 添加 provider

- [ ] 6.1 用户登记: backend 运行中（用户允许重启）
- [ ] 6.2 重启 deskpet（带 DESKPET_BACKEND_DIR）
- [ ] 6.3 computer-use 截图: 老的 `[llm.local]` 配置自动迁移成 `[[llm.providers]]` 第一项
- [ ] 6.4 打开 settings → LLM Providers → 添加第二个 provider (任意 OpenAI-compat endpoint，可以用 ollama 本地)
- [ ] 6.5 截图: 列表里 2 个 provider，能拖拽改顺序

### 6.2 Code 模式 per-session 切换

- [ ] 6.6 进 code 模式，找一个项目卡片
- [ ] 6.7 dropdown 选第二个 provider
- [ ] 6.8 发条简单消息（"读 README.md"），观察 log: `provider_used` event 显示用了哪个 provider
- [ ] 6.9 截图: 卡片显示 🔒 锁图标，确认 pinned
- [ ] 6.10 dropdown 选 "Global Chain" → 锁消失，回到 chain 模式

### 6.3 Fallback chain 触发

- [ ] 6.11 故意把 priority=1 的 provider 配错 base_url 让它 fail
- [ ] 6.12 发消息观察 log: `provider_chain_fallback from=A to=B reason=ConnectError`
- [ ] 6.13 截图: 前端 ChatBubble 上有小角标 "via B"
- [ ] 6.14 修复 priority=1 的配置

### 6.4 evidence

- [ ] 6.15 写 `evidence/6.X-live-e2e.md` 包含所有截图 + log 摘要

---

## Phase 7 — 最终验证 + archive

- [ ] 7.1 跑全套 pytest（baseline 1048 → 应 1090+，0 regression）
- [ ] 7.2 跑全套 vitest（baseline 54 → 应 75+，0 regression）
- [ ] 7.3 npx tsc --noEmit 0 errors
- [ ] 7.4 git status 干净
- [ ] 7.5 `openspec archive multi-provider-management`

---

## 依赖图

```
Phase 0 (SessionDB) ──┐
Phase 1 (Registry) ───┼─→ Phase 2 (IPC) ─→ Phase 3 (AgentLoop chain) ──┐
                      │                                                  │
                      └─────────────────→ Phase 4 (Settings UI) ───┐    │
                                          Phase 5 (Code Panel UI) ─┴────┴─→ Phase 6 (Live E2E) → Phase 7 (Archive)
```

可并行批次（用 /opsx:oneshot）：

- **Batch 1**: Phase 0 + Phase 1（独立）
- **Batch 2**: Phase 2（依赖 1）
- **Batch 3**: Phase 3（依赖 2）
- **Batch 4**: Phase 4 + Phase 5（依赖 2/3 的 IPC schema）
- **Batch 5**: Phase 6（live E2E，需 deskpet 重启）
- **Batch 6**: Phase 7 (archive)

预估 wallclock：~6-8h（Batch 1-4 并行 + 串行批次 overhead）

## 不做的（明确）

- ❌ 不 ping provider /models 探活
- ❌ 不引入 OpenAI/Anthropic 原生 SDK
- ❌ 不做 token 计费 per-provider 隔离
- ❌ 不做 provider preset import / 模板市场
- ❌ 不做流量分配 / A/B 测试
