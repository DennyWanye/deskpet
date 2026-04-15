# P2-1-S8 Billing + Budget — HANDOFF

**Branch:** `feat/p2-1-s8-billing-budget`
**Slice goal:** Persist every LLM chat_stream call's usage/cost in SQLite, gate cloud calls behind a daily budget, and surface over-budget events to the UI.

## Commits (oldest → newest)

| SHA | Summary |
|---|---|
| 3538e51 | `feat(billing): BillingLedger SQLite + BudgetHook contract (P2-1-S8)` |
| a7ded5c | `feat(billing): load [billing] config section (P2-1-S8)` |
| 43cd580 | `feat(providers): capture last_usage from OpenAI stream chunks (P2-1-S8)` |
| 1f8a029 | `feat(router): invoke BudgetHook before cloud call (P2-1-S8)` |
| 5b89ec4 | `feat(main): wire BillingLedger + budget_status WS handler (P2-1-S8)` |
| 0192fe6 | `feat(ui): budget toast hook + SettingsPanel budget widget (P2-1-S8)` |

## Data model: `calls` table

`backend/data/billing.db` (same directory as `memory.db`).

```
CREATE TABLE calls (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  ts_utc           TEXT     NOT NULL,  -- ISO-8601 UTC
  ts_date          TEXT     NOT NULL,  -- YYYY-MM-DD UTC, indexed
  provider         TEXT     NOT NULL,  -- "local" | "cloud"
  model            TEXT     NOT NULL,  -- model id from the provider
  prompt_tokens    INTEGER  NOT NULL,
  completion_tokens INTEGER NOT NULL,
  cost_cny         REAL     NOT NULL   -- 0.0 for local
);
CREATE INDEX calls_ts_date_idx ON calls(ts_date);
```

### Daily rollover
"Today" = UTC calendar date of the call's write time. No cron job — `spent_today_cny()` just SUMs rows where `ts_date = today()`.

## Pricing table

`config.toml` → `[billing.pricing]` maps `model_id → price_cny_per_1M_tokens` (prompt+completion combined). Unknown models fall back to `unknown_model_price_cny_per_m_tokens` (default 20.0 CNY/M — intentionally pessimistic).

Add a new cloud model:
```toml
[billing.pricing]
"my-new-model" = 3.5
```
No code change needed — BillingLedger reads the dict at construct time.

## BudgetHook semantics

`backend/router/types.py` defines:
```python
BudgetHook = Callable[[BudgetContext], Awaitable[BudgetDecision]]
```

`BillingLedger.create_hook()` implements:
- `ctx.route == "local"` → always `allow=True` (local is free).
- `ctx.route == "cloud"` → `allow=False` once `spent_today_cny >= daily_budget_cny`.

HybridRouter invokes the hook once per cloud attempt (including `force_cloud=True`), **before** health-check/circuit-breaker. Denial raises `LLMUnavailableError("budget denied: <reason>")` and stashes `reason` in `router._last_budget_reason` so `main.py` can surface it.

## `chat_response` payload contract

```ts
{
  type: "chat_response",
  payload: {
    text: string,
    budget_exceeded?: true,          // new in S8
    budget_reason?: string,          // e.g. "daily_budget_exceeded:12.03/10.00"
  }
}
```
Frontend (`useBudgetToast`) subscribes via `ControlChannel.onMessage` and renders a toast when `budget_exceeded === true`. Missing `budget_exceeded` means the normal (non-denial) path; existing UI still works unchanged.

## `budget_status` WS handler

Request:
```json
{"type": "budget_status"}
```
Reply:
```json
{
  "type": "budget_status",
  "payload": {
    "spent_today_cny": 1.234,
    "daily_budget_cny": 10.0,
    "remaining_cny": 8.766,
    "percent_used": 0.1234
  }
}
```
`SettingsPanel.tsx` (stub shipped in this slice; S3 will supersede) calls `fetchDailyBudget(channel)` which sends the request and resolves on the next `budget_status` frame (3s timeout).

## Cross-slice contract stubs

This slice ships **stub** copies of two files whose canonical owners are other slices:

1. `backend/router/types.py` — S6 will land an identical copy on master. Signature is locked via the finale spec §3.
2. `tauri-app/src/panels/SettingsPanel.tsx` — S3 owns the full panel; the S8 version is a minimal 今日使用 widget + `fetchDailyBudget` helper. When S3 lands, the final panel replaces this stub; the S8 protocol (`budget_status` WS + `DailyBudgetStatus` shape in `types/messages.ts`) stays.

Merge strategy: last one in wins on the TS file (S3's richer panel). For `types.py`, either slice's file will merge cleanly because both produce identical bytes.

## `OpenAICompatibleProvider.last_usage`

New public attribute, populated per `chat_stream()` call when the server emits a terminal chunk containing `usage` (OpenAI/DashScope always do, because we now send `stream_options={"include_usage": True}`).

**Ollama caveat:** Ollama's `/v1/chat/completions` stream currently does NOT emit a `usage` frame. For local calls this means `last_usage` stays `None` → `main.py` records no row. Net effect: **local usage is not billed**, which is correct (local route costs 0 anyway) but also means we can't track local tokens for analytics. Out of scope for S8; flag for a future Ollama-usage slice if we need it.

## main.py wiring notes

- Ledger is constructed at module load, registered into ServiceContext (`"billing_ledger"`) for future callers.
- `await billing_ledger.init()` runs inside `lifespan()` — DB creation is best-effort (warn on failure, don't block startup).
- The `chat` handler uses `llm._cloud.last_usage` / `llm._local.last_usage` to debit. That's a private-attribute probe; a cleaner `router.last_used_provider` public property can replace it in P2-2 without touching any of the S8 contracts.

## Out of scope / future work

- **Per-user budget** — deskpet is single-user; not modeling accounts.
- **Monthly / weekly windows** — only daily ledger today.
- **Auto-purge old rows** — `calls` grows forever. A P2-2 add: `billing vacuum --before-days N` CLI.
- **Ollama token accounting** — needs Ollama's non-SSE `/api/chat` stream (has `prompt_eval_count` / `eval_count` fields) or manual token counting.
- **Live pricing feed** — pricing baked into `config.toml`; user must edit manually when DashScope changes rates.
- **Interruption accounting** — if `interrupt` cancels a cloud stream mid-flight, we still get whatever `usage` the provider emits at cancel time (typically none from OpenAI); the call may not be billed despite network cost. Minor.

## Manual E2E status

Automated checkpoint (`uv run python` scripted test) verified:
- local route always allowed
- cloud route allowed under budget
- cloud route denied once `spent_today_cny >= daily_budget_cny`
- `BillingLedger.status()` shape matches contract
- `HybridRouter + BillingLedger` end-to-end raises `LLMUnavailableError("budget denied: ...")` with `_last_budget_reason` populated

**Still requires human E2E pass after merge into master:**
1. Full Tauri app launch + `sqlite3 backend/data/billing.db "SELECT * FROM calls"` shows real rows after a cloud chat.
2. Toast actually appears in the UI when cloud budget exhausted.
3. SettingsPanel "刷新" button round-trips `budget_status`.
4. Local-only chat inserts a `provider='local', cost_cny=0.0` row (contingent on an Ollama release that emits usage, or a local token counter — see Out of scope).

## Test count delta

Before S8: 154 tests passing in the S8-touched modules (excluding `faster_whisper`-dependent tests which fail on this environment for unrelated reasons).

After S8 (verified in slice):
- `test_billing_ledger.py` +7 (new module)
- `test_config_billing.py` +5 (new module)
- `test_openai_compatible.py` +2 (`captures_usage`, `last_usage_resets_when_absent`)
- `test_hybrid_router.py` +4 (`budget_denies_cloud...`, `budget_allow_goes_through`, `budget_hook_not_called_for_local_only_path`, `budget_denial_with_force_cloud_raises`)

Total: **+18 new tests, all passing**; no existing tests regressed.

## Rebase notes (when master gets S6 + S3)

- `backend/router/types.py` — S6 will land identical bytes; `git` sees no conflict.
- `backend/router/hybrid_router.py` — S6 introduces `budget_hook: BudgetHook = allow_all_budget`; S8 now uses it. If S6 lands first, this slice's edits already match the master signature.
- `backend/tests/test_hybrid_router.py` — S6 adds its own budget-related tests. If both slices touch the same test file, merge should keep both test sets (they don't share function names).
- `tauri-app/src/panels/SettingsPanel.tsx` — S3's full panel supersedes the S8 stub.
- `tauri-app/src/types/messages.ts` — `ChatResponse.payload.budget_exceeded?` and `BudgetStatusMessage` must survive the merge.
