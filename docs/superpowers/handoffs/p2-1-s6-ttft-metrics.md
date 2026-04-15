# Handoff — P2-1-S6 TTFT Metrics + BudgetHook Skeleton

**Branch:** `feat/p2-1-s6-ttft-metrics` (based on master `8d7cc2f`)
**Worktree:** `G:/projects/deskpet-s6`
**Spec:** `docs/superpowers/specs/2026-04-15-p2-1-finale-design.md` §1.1, §2.2, §3
**Plan:** `docs/superpowers/plans/2026-04-15-p2-1-s6-ttft-metrics.md`

## Goal

1. Ship a `/metrics` Prometheus endpoint + `llm_ttft_seconds{provider,model}`
   Histogram so the hybrid router's time-to-first-token is observable
   from a scraper or a local `curl`.
2. Create the `BudgetHook` type skeleton that S3 / S7 / S8 will consume.
   S6 ships only the types and a no-op `allow_all_budget` default; S8
   wires the real `BillingLedger`-backed hook.

## Commits

| SHA | Summary |
|-----|---------|
| `134d6ba` | `feat(deps): add prometheus_client for /metrics endpoint` |
| `259e84a` | `feat(router): add BudgetHook type skeleton + allow_all default` |
| `c4f4a74` | `feat(observability): add Prometheus llm_ttft_seconds Histogram + render()` |
| `1d34644` | `feat(router): wrap chat_stream with TTFT instrumentation + rename budget_check to budget_hook` |
| `7c1d3f8` | `feat(main): mount /metrics endpoint with secret-or-dev-mode auth` |
| `f221166` | `feat(perf): add ttft_cloud.py smoke script` |

## Files changed

- `backend/pyproject.toml` / `backend/uv.lock` — added `prometheus-client`.
- `backend/router/types.py` (new) — `BudgetContext`, `BudgetDecision`,
  `BudgetHook`, `allow_all_budget`.
- `backend/router/__init__.py` — re-export the four new symbols.
- `backend/observability/metrics.py` — append Prometheus Histogram
  `llm_ttft_seconds` with buckets `(0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 3.0, 5.0, 10.0, +Inf)` and `render()` helper.
- `backend/router/hybrid_router.py` — `budget_check` renamed to
  `budget_hook` (type `BudgetHook`, default `allow_all_budget`). New
  `_stream_with_ttft` wrapper observes TTFT on first yield for both
  local and cloud paths.
- `backend/main.py` — switch HybridRouter kwarg to `budget_hook=None`;
  new `GET /metrics` route gated by the shared secret (open in
  `DESKPET_DEV_MODE=1`).
- `backend/scripts/perf/ttft_cloud.py` (new) — WS-based force-cloud
  smoke with p50/p95 reporting.
- Tests: `backend/tests/test_router_types.py` (5),
  `backend/tests/test_metrics.py` (2),
  `backend/tests/test_metrics_endpoint.py` (5),
  plus 3 new cases in `backend/tests/test_hybrid_router.py`.

## Behavior contract (for downstream slices)

### BudgetHook (used by S3 / S7 / S8)

```python
from router.types import BudgetContext, BudgetDecision, BudgetHook, allow_all_budget

@dataclass(frozen=True)
class BudgetContext:
    route: Literal["local", "cloud"]
    model: str

@dataclass(frozen=True)
class BudgetDecision:
    allow: bool
    reason: str | None = None

BudgetHook = Callable[[BudgetContext], Awaitable[BudgetDecision]]
```

`HybridRouter.__init__` accepts `budget_hook: BudgetHook | None = None`;
`None` falls back to `allow_all_budget`. **S8** will import
`allow_all_budget` to assemble tests and wire a real hook through the
same parameter.

### `/metrics` endpoint

- Path: `GET /metrics`.
- Auth: if `DESKPET_DEV_MODE=1`, open. Otherwise requires
  `x-shared-secret: <SHARED_SECRET>` header (same secret used by `/ws/*`).
- Response: `text/plain; version=0.0.4; charset=utf-8` Prometheus text
  format.
- Always exposes `llm_ttft_seconds_{bucket,count,sum}` with labels
  `provider=local|cloud`, `model=<model-id>`. Zero-observation series
  still emit HELP/TYPE metadata (test coverage).

### TTFT label set

- `provider` is always `"local"` or `"cloud"`.
- `model` is pulled from `getattr(provider, "model", "unknown")` — this
  matches `OpenAICompatibleProvider`, which stores the configured model
  id on the instance. `unknown` is the documented fallback if a future
  provider class forgets to set it.

## Test status

- `uv run pytest tests/test_hybrid_router.py tests/test_router_types.py tests/test_metrics.py tests/test_metrics_endpoint.py -v`
  → **34 passed** (19 pre-existing hybrid_router + 3 new TTFT/hook + 5
  BudgetHook + 2 metrics render + 5 metrics endpoint).
- Broader backend suite (tests that don't pull heavy deps):
  `uv run pytest --ignore=tests/test_cosyvoice_provider.py --ignore=tests/test_e2e_integration.py --ignore=tests/test_e2e_pipeline.py --ignore=tests/test_providers.py --ignore=tests/test_websocket.py --ignore=tests/test_memory_api.py -q`
  → **152 passed, 1 skipped** (no regressions introduced by this slice).

### Pre-existing environment gaps (not S6 issues)

This worktree doesn't have `torch`, `faster_whisper`, or `silero` wheels
installed, so the following suites error out at collection time on both
master and this branch:

- `tests/test_cosyvoice_provider.py` / `tests/test_providers.py` /
  `tests/test_websocket.py` / `tests/test_e2e_pipeline.py` / `tests/test_e2e_integration.py` — missing `faster_whisper`.
- `tests/test_memory_api.py` — chained import failure through `pipeline/voice_pipeline.py`.

These were already failing/erroring before this slice. Running the suite
on a fully-provisioned machine (e.g. the dev box with GPU wheels) should
pick up the same results as our scoped run above.

## Manual E2E — deferred to human

The plan calls for `uv run python main.py &` → `scripts/smoke_chat.py`
→ `curl /metrics`. In this worktree `import main` fails at the
`torch`/`faster_whisper` imports, so the server can't start here.

**Action item for the reviewer (or S-final integrator):** on a machine
with the full env installed, run:

```bash
cd backend
DESKPET_DEV_MODE=1 uv run python main.py &
sleep 5
uv run python scripts/smoke_chat.py         # produces 1 TTFT observation
curl -s http://127.0.0.1:8100/metrics | grep llm_ttft_seconds
taskkill //F //IM python.exe
```

Expect `llm_ttft_seconds_count{provider="local",model="gemma4:e4b"} 1.0`
(or the configured local model id).

## Out of scope

- Additional metrics (tokens-per-second, provider-error-rate,
  circuit-breaker state gauge) — deferred to a follow-up observability
  slice.
- True per-token TTFT via the audio channel — the Histogram is
  server-side (first yield from the provider). The `ttft_cloud.py`
  script measures *full-reply* latency because the control WS buffers
  the whole reply before emitting `chat_response`. This is a proxy, not
  first-token; it exists to dot-check that cloud samples reach
  `/metrics` rather than to be an SLI.
- BudgetHook implementation — deferred to **S8** (`BillingLedger`).

## Next step — S8 integration

Drop-in points for S8:

1. Build the real hook in `backend/billing/ledger.py`:
   ```python
   from router.types import BudgetContext, BudgetDecision, BudgetHook
   class BillingLedger:
       async def hook(self, ctx: BudgetContext) -> BudgetDecision: ...
   ```
2. In `main.py`, replace `budget_hook=None` with
   `budget_hook=billing_ledger.hook`. **No signature change to
   HybridRouter is needed** — the parameter, type, and default are
   already in place.
3. When S8 adds per-turn accounting in `chat_stream`, call
   `self._budget_hook(BudgetContext(route="cloud", model=self._cloud.model))`
   *before* entering `_stream_cloud`, short-circuit on `allow=False`.
