# P2-1-S7 Fallback E2E — HANDOFF

**Slice:** P2-1-S7 Fallback end-to-end testing
**Branch:** `feat/p2-1-s7-fallback-e2e`
**Status:** DONE

## Goal

pytest-level end-to-end validation that `HybridRouter` fallback works across
the full WebSocket → agent → router → provider chain. No Docker, no
toxiproxy, no external network — just `httpx.MockTransport` injected into
`OpenAICompatibleProvider._test_transport`.

## Commits

- `e5382bf` — test(fallback): pytest E2E for HybridRouter fallback paths via MockTransport (P2-1-S7)

## Test scenarios (6/6 PASS in ~3.7s)

| # | Scenario | Validates |
|---|----------|-----------|
| 1 | `test_local_healthy_returns_local_response` | Baseline — `local_first` strategy picks local when both healthy |
| 2 | `test_local_503_falls_back_to_cloud` | Router catches provider exception, falls back to cloud |
| 3 | `test_local_503_and_cloud_503_returns_echo_fallback` | Both dead → `LLMUnavailableError` → `ws/control` handler returns `[echo] {text}` (current main.py contract) |
| 4 | `test_cloud_only_when_local_unconfigured` | `HybridRouter(local=None, cloud=...)` skips local probe entirely |
| 5 | `test_circuit_opens_after_three_local_failures` | 3 local 503s open the breaker; subsequent requests bypass local |
| 6 | `test_repeated_fallback_does_not_leak` | 100-cycle stress — no exceptions, no slow degradation (<30s) |

## Decision records

### Why `httpx.MockTransport` instead of toxiproxy / Docker

- **No extra infra:** CI runs don't need Docker-in-Docker, no network
  namespace setup, no port conflicts.
- **Deterministic:** no real sockets means no flaky timing, no port races
  across parallel test workers.
- **Full coverage of real code:** unlike the S2 unit tests which use
  `_FakeProvider` (bypassing httpx entirely), this suite exercises the
  real `OpenAICompatibleProvider.chat_stream` SSE-parsing path.
- **Single trade-off:** we don't exercise real TCP failure modes (RST,
  half-open connections). Acceptable — those belong in a live-smoke
  runbook, not unit-level CI.

### Why `backend/tests/` (pytest fixture) rather than `tests/e2e/` (live smoke)

- The "E2E" in this slice refers to the full backend call chain
  (WebSocket → agent → router → provider → SSE parser), not the full
  application chain (Tauri ↔ backend). Pytest is the natural fit.
- Keeps the round-trip fast enough for every-commit CI.
- Live UI-level E2E (frontend + real backend) is out of scope here; the
  existing manual smoke runbook covers it.

### Why `ServiceContext.register()` was sufficient — no `replace()` helper added

The plan considered adding a test-only `replace()` method. Inspection of
`backend/context.py` showed `register()` already uses `setattr`, so
overwriting an existing service is a no-op delta — no API change needed.
The fixture uses `service_context.register("llm_engine", r)` and
`register("agent_engine", SimpleLLMAgent(r, memory=...))` directly.

## Fixture gotchas encountered

1. **`service_context._services` doesn't exist.** The plan's first draft
   assumed a dict-backed store. Real `ServiceContext` is a dataclass with
   named attributes (`llm_engine`, `agent_engine`, `memory_store`, ...).
   Fixture adapted to use `register()` and direct attribute reads.

2. **`main.py` imports heavy optional deps at module load**
   (`faster_whisper`, `edge_tts`, `silero_vad`). These aren't in the
   default dev install, so `import main` crashes with
   `ModuleNotFoundError`. Fix: stub the dependency modules in
   `sys.modules` before `importlib.reload(main)`. Stubs are defined
   inline in `test_fallback_e2e.py::_stub_heavy_modules()` and are
   idempotent. The chat path never touches ASR/TTS/VAD so fake
   implementations are never called.

3. **Lifespan must not fire.** `TestClient` only runs `lifespan` when
   used as a context manager (`with TestClient(app) as c:`). We use it
   as a bare object (`client = TestClient(app)`), so the preload loop
   that would try to call `asr.load()` / `tts.load()` is skipped. This
   is deliberate — the WS routes are still fully wired.

4. **`importlib.reload(main)` each test.** Ensures `service_context`,
   `DEV_MODE`, and the module-level `HybridRouter` are freshly
   initialized per scenario so circuit state from one test can't bleed
   into the next.

## Re-use for P2-2

When the realtime duplex (voice interrupt + streaming) slice lands, the
same fixture pattern applies: stub heavy deps, reload main, replace
`agent_engine` / `tts_engine` with MockTransport-backed providers, then
drive via `TestClient.websocket_connect`. The `_ok_handler` / `_503_handler`
pair in this file is a solid template.

## Out of scope (leaves room for later slices)

- **UI-level E2E:** Tauri window opening, chat bar input → backend → bubble
  render. Manual smoke only (`scripts/smoke/manual_e2e.md`-style runbook).
- **Live-network failure injection:** real TCP half-open, SSL timeouts.
  Would need toxiproxy; not worth the CI-cost right now.
- **Budget gating path:** `budget_check=None` in main.py today. When S8
  wires the BillingLedger, add a scenario here:
  `test_cloud_skipped_when_budget_exhausted`.
- **Concurrent WS fan-out:** we send one chat at a time. Multi-session
  concurrent fallback would need P2-2's voice pipeline anyway.

## Verification

```
cd backend && DESKPET_DEV_MODE=1 uv run pytest tests/test_fallback_e2e.py -v
# ====== 6 passed in 3.66s ======

cd backend && uv run pytest --ignore=tests/test_cosyvoice_provider.py \
  --ignore=tests/test_e2e_integration.py --ignore=tests/test_e2e_pipeline.py \
  --ignore=tests/test_providers.py --ignore=tests/test_websocket.py -q
# 148 passed, 1 skipped in 10.92s
```

The 5 excluded files have pre-existing `ImportError`s from missing
optional deps (same `faster_whisper` / `edge_tts` / `silero_vad` gap).
Out of scope for this slice — tracked separately.
