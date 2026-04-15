# P2-1-S2 HybridRouter — HANDOFF

**Date**: 2026-04-15
**Sprint**: V6 Phase 2 · Sprint P2-1 · Slice 2
**Status**: ✅ Merged on `feat/p2-1-s2-hybrid-router`; ready to merge into `master`
**Plan**: `docs/superpowers/plans/2026-04-15-p2-1-s2-hybrid-router.md`
**Spec**: `docs/superpowers/specs/2026-04-15-p2-1-design.md` §3.2 + §4 + §6 (Slice S2 row)

---

## Goal

Insert `HybridRouter` between `agent_engine` and the bare `OpenAICompatibleProvider`, so the LLM call path becomes `local_first` with circuit-breaker-aware fallback to a (currently empty) cloud slot. Lays the wiring for S6/S7/S8 to add TTFT metrics, fallback E2E, and budget enforcement without touching the routing code itself.

---

## Commits (branch `feat/p2-1-s2-hybrid-router`)

| SHA       | Subject                                                                       |
| --------- | ----------------------------------------------------------------------------- |
| `6c283e8` | feat(router): add HybridRouter skeleton + LLMUnavailableError                 |
| `01c7130` | feat(config): split [llm] into routing + local/cloud endpoints                |
| `a910363` | feat(router): add per-provider circuit breaker + health TTL cache             |
| `f86a0f1` | feat(router): implement health_check with TTL cache                           |
| `9b031fe` | feat(router): implement local_first chat_stream with circuit breaker fallback |
| `20ae687` | feat(main): wire HybridRouter as llm_engine                                   |
| `fc4eb6e` | test(router): integration test with real OpenAICompatibleProvider             |

7 commits, ~470 LoC of production code + ~310 LoC of tests.

---

## Files changed

| File                                  | Action   | Notes                                                                                   |
| ------------------------------------- | -------- | --------------------------------------------------------------------------------------- |
| `backend/router/__init__.py`          | created  | re-exports `HybridRouter`, `LLMUnavailableError`, `RoutingStrategy`                     |
| `backend/router/hybrid_router.py`     | created  | ~180 LoC: `_now()`, `_CircuitState`, `_ProviderState`, `HybridRouter`                   |
| `backend/config.py`                   | modified | replaced flat `LLMConfig` with `LLMRoutingConfig` + `LLMEndpointConfig`                 |
| `backend/main.py`                     | modified | `OpenAICompatibleProvider(...)` → `HybridRouter(local=..., cloud=...)`                  |
| `config.toml`                         | modified | `[llm]` split into `[llm]` (routing) + `[llm.local]` + commented-out `[llm.cloud]`       |
| `backend/tests/test_hybrid_router.py` | created  | 19 tests across all 4 task seams                                                        |
| `backend/tests/test_config.py`        | modified | refreshed fixtures for new schema + 2 new tests for routing config + cloud-optional case |

Total backend test count: **163 passed, 1 skipped** (skipped is the pre-existing live-network DashScope test).

---

## Behavior contract

### Routing decision (default `RoutingStrategy.LOCAL_FIRST`)

| Caller flag         | Local circuit | Local health | Local stream | Cloud configured | Result                                                                                      |
| ------------------- | ------------- | ------------ | ------------ | ---------------- | ------------------------------------------------------------------------------------------- |
| `force_cloud=False` | not OPEN      | True         | succeeds     | —                | Stream from local; record success                                                           |
| `force_cloud=False` | not OPEN      | True         | raises       | yes              | Record failure (++consecutive_failures), log warning, fall back to cloud                    |
| `force_cloud=False` | not OPEN      | False        | —            | yes              | Skip local, fall back to cloud                                                              |
| `force_cloud=False` | OPEN          | —            | —            | yes              | Skip local entirely (don't even health-probe), fall back to cloud                           |
| `force_cloud=False` | any           | any          | any          | no               | If local works → local; else `LLMUnavailableError("cloud provider not configured…")`        |
| `force_cloud=True`  | —             | —            | —            | yes              | Direct to cloud (no local probe)                                                            |
| `force_cloud=True`  | —             | —            | —            | no               | `LLMUnavailableError("cloud provider not configured…")`                                     |
| any                 | —             | —            | —            | cloud OPEN       | `LLMUnavailableError("cloud circuit breaker OPEN, retry in <30s")`                          |

Mid-stream failure (already yielded ≥1 token) re-raises upward — no silent provider switch (would emit duplicate tokens to TTS).

`RoutingStrategy != LOCAL_FIRST` → `NotImplementedError(...)` (S6/S8 may add `cost_aware`).

### Circuit breaker (per provider, both local + cloud have separate state)

```
CLOSED  --3 consecutive failures-->  OPEN
OPEN    --30s elapsed-->             HALF_OPEN  (lazy: state computed on read in circuit_state_now())
HALF_OPEN  --any chat success-->     CLOSED + reset failure counter
HALF_OPEN  --any chat failure-->     OPEN (re-armed for another 30s)
```

State machine constants live at module top of `hybrid_router.py`:

- `_CIRCUIT_OPEN_AFTER_FAILURES = 3`
- `_CIRCUIT_OPEN_DURATION_SECONDS = 30.0`
- `_HEALTH_TTL_SECONDS = 30.0`

Time source is `time.monotonic()` indirected through module-level `_now()` so tests can `monkeypatch.setattr("router.hybrid_router._now", lambda: fake_now[0])`.

### Health cache

`HybridRouter.health_check()` returns True iff **any** configured provider is healthy. Per-provider TTL cache (30s) keeps `/healthz` polls from hammering Ollama / DashScope. If `provider.health_check()` raises, treat as `False` and cache it.

---

## Config migration (breaking for users on existing `config.toml`)

**Old shape (≤ v0.2.0):**

```toml
[llm]
provider = "openai"
model = "gemma4:e4b"
base_url = "http://localhost:11434/v1"
api_key = "ollama"
temperature = 0.7
max_tokens = 2048
```

**New shape (this slice and after):**

```toml
[llm]
strategy = "local_first"
daily_budget_cny = 10.0

[llm.local]
model = "gemma4:e4b"
base_url = "http://localhost:11434/v1"
api_key = "ollama"
temperature = 0.7
max_tokens = 2048

# [llm.cloud]              # ← optional; uncomment + fill to enable cloud fallback
# model = "qwen3.6-plus"
# base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
# api_key = "sk-…"           # TODO(P2-1-S3): move to Windows Credential Manager
# temperature = 0.7
# max_tokens = 2048
```

`load_config()` already silently drops unknown TOML keys (covered by `test_load_config_ignores_unknown_toml_keys`), so leftover old keys won't crash startup — but the LLM will fall back to the hardcoded `LLMEndpointConfig` defaults if `[llm.local]` is missing entirely. Add a CHANGELOG note for the next release.

---

## Out of scope (deferred)

| Concern                                                         | Owner slice                                                                  |
| --------------------------------------------------------------- | ---------------------------------------------------------------------------- |
| API key in Windows Credential Manager (currently plaintext TOML) | **P2-1-S3**                                                                  |
| `llm_ttft_seconds{profile,model}` metric                         | **P2-1-S6**                                                                  |
| Fallback E2E with toxiproxy / 503 injection                     | **P2-1-S7**                                                                  |
| `BillingLedger` SQLite + `budget_check` hook (currently `None`) | **P2-1-S8**                                                                  |
| `cost_aware` / `latency_aware` strategies                       | **Post P2-1-S8** (or skipped — `local_first` covers the design intent)       |
| Persona-aware routing (`provider_profile_id`, `provider_mode`)  | **Phase 3** (S4/S5 cut from P2-1, see roadmap §3.2)                          |

---

## Plan deviations (vs. the original spec)

1. **`cache_health(False)` removed from local-failure path in `chat_stream`** (`9b031fe`).
   The plan said to call `local_state.cache_health(False)` after a local chat exception. With monkeypatched static `_now()` (used in `test_circuit_opens_after_three_chat_failures`), this would freeze health to False for 30s after the **first** failure, and call 2 would short-circuit at the health check before incrementing `consecutive_failures` — circuit breaker would never reach the 3-failure trip threshold. Removing the line lets the circuit breaker do its job; once OPEN, the `circuit_state_now()` gate already short-circuits ahead of `_check_health` in the `chat_stream` flow.
   The same line is **kept** in `_stream_cloud`'s exception handler. There it's effectively dead code (the `circuit_state_now() == OPEN` gate fires first after 3 failures), but removing it would have been a larger diff than the test suite covered. Worth a one-line cleanup pass when S6/S7/S8 touches `_stream_cloud`.

2. **`asyncio_mode = "strict"`, not `"auto"`.**
   Plan assumed pytest-asyncio auto mode. Actual `pyproject.toml` is `strict`. Tests carry `@pytest.mark.asyncio` decorators throughout — confirmed working.

---

## Manual verification

### Smoke (real Ollama)

```powershell
# Terminal 1
cd backend
$env:DESKPET_DEV_MODE = "1"
uv run python main.py

# Terminal 2 (after backend logs "uvicorn running")
cd backend
uv run python scripts/smoke_chat.py
```

Expected: `[smoke] VERDICT: PASS — real LLM reply via agent->provider->Ollama`.
**Status during this slice:** skipped (Ollama not running on dev box). Re-run before tagging the next release.

### Fallback (real DashScope, optional)

1. In `config.toml`, uncomment `[llm.cloud]` and fill in a real `sk-…` key (still plaintext until S3).
2. Set `[llm.local].base_url` to something invalid (`http://127.0.0.1:9`).
3. Restart backend, send a message via the UI.
4. Expect log line `router_local_chat_failed_falling_back_cloud` and a normal cloud-served reply.
5. Restore `[llm.local].base_url` after testing.

---

## Known issues

None blocking. Two follow-ups worth tracking:

- The `cache_health(False)` symmetry inconsistency in `_stream_cloud` (see deviation #1 above).
- The HybridRouter is instantiated **once** at startup; multiple WebSocket sessions share the same `_local_state` / `_cloud_state`. This is the right design for a circuit breaker (it's about provider health, not per-session state), but worth a sanity-check by an independent reviewer for race conditions on `consecutive_failures` mutations under concurrent `chat_stream` invocations. Python's GIL plus the fact that all mutations are done from `await`-resumed coroutines on the single event loop should make this safe, but a fresh pair of eyes is welcome.

---

## Next slice

**P2-1-S3 — API key management (Windows Credential Manager)**. With the routing wiring done, S3 swaps the plaintext `api_key` field in `[llm.cloud]` for a Credential Manager-backed `get_api_key(profile_id)` Tauri IPC. The HybridRouter / OpenAICompatibleProvider boundary doesn't change — only the **source** of the secret moves.
