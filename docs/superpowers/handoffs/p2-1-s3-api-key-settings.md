# P2-1-S3 Handoff — API Key + SettingsPanel

**Branch:** `feat/p2-1-s3-api-key-settings`
**Worktree:** `G:/projects/deskpet-s3`
**Slice spec:** `docs/superpowers/specs/2026-04-15-p2-1-finale-design.md` §1.3 / §2.1 / §3
**Plan:** `docs/superpowers/plans/2026-04-15-p2-1-s3-api-key-settings.md`

## Goal

Cloud LLM 的 API key 从 `config.toml` 里搬到 **Windows Credential Manager**
（跨平台同样适配 Keychain / Secret Service）。新建 SettingsPanel 暴露 3 个
section：云端账号 / 路由策略 / 今日使用（S8 会填真实数据，S3 先占位）。
Tauri 启动 backend 时读 keychain，把 key 作为 `DESKPET_CLOUD_API_KEY` env
注入 Python 子进程；Python 不再读 TOML plaintext。

## Commits

| SHA (短) | Title |
|---|---|
| `0608263` | feat(deps): add keyring crate for Credential Manager |
| `aaaa282` | feat(secrets): Tauri commands for cloud API key via Credential Manager |
| `f833eb5` | feat(process): inject DESKPET_CLOUD_API_KEY env into backend |
| `9eed308` | feat(backend): read cloud apiKey from env, warn on plaintext TOML |
| `6d6fe4b` | feat(ws): control channel provider_test_connection handler |
| `692374d` | feat(ui): SettingsPanel with cloud profile / strategy / budget sections |

## Files

**Rust (Tauri)**
- `tauri-app/src-tauri/Cargo.toml` — `keyring = "3"` with platform-native features.
- `tauri-app/src-tauri/src/secrets.rs` — 4 commands: `set/get/delete/has_cloud_api_key`.
- `tauri-app/src-tauri/src/lib.rs` — module registration + invoke_handler.
- `tauri-app/src-tauri/src/process_manager.rs` — `spawn_once` reads keychain,
  injects `DESKPET_CLOUD_API_KEY` env before `Command::spawn`.

**Python backend**
- `backend/config.py` — `resolve_cloud_api_key()` helper (env → `str | None`);
  `load_config` warns when `[llm.cloud].api_key` is plaintext real value.
- `backend/main.py` — cloud provider only constructed when `_resolve_cloud_api_key()`
  returns non-None; `provider_test_connection` dispatch delegates to the new module.
- `backend/provider_test_connection.py` — standalone async handler so unit
  tests don't need the full app bootstrap.
- `backend/tests/test_cloud_api_key_resolve.py` — 5 tests (resolver + warning).
- `backend/tests/test_provider_test_connection.py` — 4 tests (happy / unhealthy /
  validation / exception-passthrough).

**Frontend**
- `tauri-app/src/bindings/secrets.ts` — typed wrappers for the 4 Tauri commands.
- `tauri-app/src/types/messages.ts` — `ProviderTestConnectionRequest/Result`
  + **`DailyBudgetStatus` cross-slice contract** (see below).
- `tauri-app/src/components/SettingsPanel.tsx` — overlay panel, 3 sections,
  exports module-level `fetchDailyBudget()` stub.
- `tauri-app/src/App.tsx` — `⚙` toggle button next to `🗂`, mounts the panel
  and forwards `getChannel` + `lastMessage`.

## Cross-slice contract (S3 ↔ S8)

`DailyBudgetStatus` is **frozen** — S8 must return this exact shape from the
real control-WS call:

```ts
{
  spent_today_cny: number
  daily_budget_cny: number
  remaining_cny: number
  percent_used: number   // 0..100, precomputed
}
```

S8 rebase point: replace the body of
`SettingsPanel.tsx::fetchDailyBudget()` with a real control-WS roundtrip
(keep the export name + signature; the UI renders all 4 fields + 刷新
button already).

## Threat model

- **apiKey never lands in SQLite or TOML.** Path is: UI input → Rust
  command → `keyring::Entry::set_password` → OS credential store. Read
  back only inside Rust process; flows to Python via env (single-hop).
- **Plaintext warning for migrations.** `load_config` warns (not errors)
  when an old TOML still lists a real key, so users aren't locked out
  but are told exactly where to click.
- **测试连接 requires a freshly-typed key.** The UI refuses to use the
  saved key when the user clicks 测试连接 — so the key never travels
  from the keyring back to the renderer process even momentarily. Only
  the candidate the user types into the form goes to the backend.
- **No plaintext echo.** Rust command errors use `format!` but never
  include the key string.

## Cross-platform

`keyring` v3 with `windows-native + apple-native + sync-secret-service`
features — each `cfg` only compiles the relevant backend, so Linux
without `libdbus` still builds (it'll error at runtime with a clear
NoEntry → "Secret Service not available" message, which the UI already
degrades gracefully on via the `hasCloudApiKey` catch block).

## Migration for existing users

If a user carried over a `config.toml` from P2-1-S1 with a real
`[llm.cloud].api_key`:

1. Backend boots → `logger.warning` fires: "config [llm.cloud].api_key
   is plaintext — IGNORED. Cloud API key now lives in the OS keyring
   (set via SettingsPanel → 云端账号)."
2. User opens `⚙` → enters key → clicks 测试连接 → on success clicks 保存.
3. Rust writes to Credential Manager; on next Tauri restart the key is
   injected via env.
4. User is expected to delete the plaintext line from `config.toml`
   manually (we don't touch their file).

## Out of scope (deferred)

- **Persisting strategy / daily budget to backend.** S6 owns
  strategy-switching via control WS; S8 owns the BudgetHook wire-up to
  the HybridRouter. SettingsPanel keeps those values in local state for
  S3 — the TODO comment in `handleSave` names both slices.
- **Multi-profile keys** (e.g. "dashscope work" + "aliyun personal") —
  deferred to Phase 3. Service/username constants in `secrets.rs` are
  hardcoded (`deskpet-cloud-llm` / `default`); a comment warns against
  renaming without a migration read.
- **Real daily-budget control-WS message + handler** — S8.

## Manual E2E status

**Ran by agent:**
- `cargo build` (debug) — green
- `cd backend && uv run pytest tests/test_cloud_api_key_resolve.py
  tests/test_config.py tests/test_provider_test_connection.py` — 13/13 pass
- `npx tsc --noEmit` — 0 errors
- `npm run build` — clean (vite)

**Must be run by a human before release:**

| 场景 | 期望 |
|---|---|
| `npm run tauri dev` → 右上角 `⚙` | SettingsPanel 弹出 |
| 在 apiKey 输入 `sk-foo` → 保存 → 关 → 重开 | placeholder 显示「已配置（输入新值替换）」|
| baseUrl = `http://localhost:9999/v1` + 填任意 apiKey → 测试连接 | 显示「失败:…」|
| baseUrl = `http://localhost:11434/v1` + apiKey = `ollama` + model = `gemma4:e4b`（本地 ollama 在跑）→ 测试连接 | 显示「连接成功 (http://localhost:11434/v1/models)」|
| 保存 sk-foo 后重启 Tauri → backend 日志 | 应看到 cloud_llm 正常构造（若 config.toml 仍配了 `[llm.cloud]`）；或者不配 `[llm.cloud]` 时 `cloud_llm_skipped` |
| 清除已保存 → 再次重开 panel | placeholder 回到「未配置」|

Agent 环境没法做真实 keyring 写入（需要 Windows 用户 session + UI 交互），
所以以上人工清单需要 P2-1 release 前由人跑一遍。

## Known dependencies not installed in dev venv

`test_memory_api.py` / `test_providers.py` / `test_e2e_*.py` 在当前 backend
venv 里失败，原因是 `faster_whisper` 未装。**这是 pre-existing 环境问题**
—— 在 `master` 分支上可复现。与本 slice 无关，不阻塞合入。

## Next steps

- **S6** (strategy control via WS) 会加 `strategy_change` 消息；
  SettingsPanel 的 `handleSave` 里有 TODO 锚点可接。
- **S8** (BudgetHook + daily ledger) 替换 `fetchDailyBudget` 实现；
  `DailyBudgetStatus` 字段不变。
- **P2-1 finale** 合 S3 + S6 + S7 + S8 后跑端到端人工测试清单。
