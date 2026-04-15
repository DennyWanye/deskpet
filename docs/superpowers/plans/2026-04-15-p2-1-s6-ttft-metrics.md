# P2-1-S6 TTFT 埋点 + /metrics 实现 plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 backend 加 `/metrics` Prometheus 端点 + `llm_ttft_seconds{provider,model}` Histogram + 创建 `BudgetHook` 类型骨架（S8 后续填实现）。

**Architecture:** 新增 `backend/observability/metrics.py`（Prometheus Registry 单例）和 `backend/router/types.py`（BudgetHook 类型）。HybridRouter 内部加 `_stream_with_ttft` 包装函数，在 first-yield 处记录 TTFT。`/metrics` 端点用与 WS 同一 secret 模型鉴权。

**Tech Stack:** `prometheus_client`（新依赖）、FastAPI Response、structlog。

**Spec:** `docs/superpowers/specs/2026-04-15-p2-1-finale-design.md` §1.1 + §2.2

**Branch:** `feat/p2-1-s6-ttft-metrics`

---

### Task 1: 加 prometheus_client 依赖

**Files:**
- Modify: `backend/pyproject.toml`

- [ ] **Step 1: 加依赖到 `[project].dependencies`**

```bash
cd backend && uv add prometheus-client
```

Expect: `prometheus-client` appears in `pyproject.toml` and `uv.lock` updated.

- [ ] **Step 2: Verify import works**

```bash
cd backend && uv run python -c "import prometheus_client; print(prometheus_client.__version__)"
```

Expect: a version string like `0.20.0` printed, no ImportError.

- [ ] **Step 3: Commit**

```bash
git add backend/pyproject.toml backend/uv.lock
git commit -m "feat(deps): add prometheus_client for /metrics endpoint (P2-1-S6)"
```

---

### Task 2: 创建 `backend/router/types.py` (BudgetHook 类型骨架)

**Files:**
- Create: `backend/router/types.py`
- Create: `backend/tests/test_router_types.py`

- [ ] **Step 1: 写失败的 test**

```python
# backend/tests/test_router_types.py
import pytest
from router.types import BudgetContext, BudgetDecision, allow_all_budget

def test_budget_context_is_frozen():
    ctx = BudgetContext(provider="cloud", model="qwen3.6-plus", estimated_input_tokens=None)
    with pytest.raises(Exception):  # frozen dataclass raises FrozenInstanceError
        ctx.provider = "local"

def test_budget_decision_holds_reason():
    d = BudgetDecision(allow=False, reason="预算用完")
    assert d.allow is False
    assert d.reason == "预算用完"

@pytest.mark.asyncio
async def test_allow_all_returns_allow_true():
    ctx = BudgetContext(provider="cloud", model="x", estimated_input_tokens=100)
    d = await allow_all_budget(ctx)
    assert d.allow is True
    assert d.reason is None
```

- [ ] **Step 2: Run to verify failure**

```bash
cd backend && uv run pytest tests/test_router_types.py -v
```

Expect: ImportError (`router.types` 不存在).

- [ ] **Step 3: 实现 types.py**

```python
# backend/router/types.py
"""Shared types for router-level budget hook (P2-1-S6 skeleton, S8 fills impl).

`BudgetHook` is the contract HybridRouter calls before delegating to a provider.
S6 ships only the type + `allow_all_budget` default; S8 ships `BillingLedger`
that produces a real hook.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Literal


@dataclass(frozen=True)
class BudgetContext:
    provider: Literal["local", "cloud"]
    model: str
    estimated_input_tokens: int | None  # None = caller didn't estimate


@dataclass(frozen=True)
class BudgetDecision:
    allow: bool
    reason: str | None  # human-readable; UI may surface as toast


BudgetHook = Callable[[BudgetContext], Awaitable[BudgetDecision]]


async def allow_all_budget(ctx: BudgetContext) -> BudgetDecision:
    """Default no-op hook used when no ledger is wired (P2-1-S6 default)."""
    return BudgetDecision(allow=True, reason=None)
```

- [ ] **Step 4: 修 router/__init__.py 导出新类型**

```python
# backend/router/__init__.py — append to existing exports
from router.types import BudgetContext, BudgetDecision, BudgetHook, allow_all_budget

__all__ = [
    "HybridRouter", "LLMUnavailableError", "RoutingStrategy",
    "BudgetContext", "BudgetDecision", "BudgetHook", "allow_all_budget",
]
```

- [ ] **Step 5: Run tests**

```bash
cd backend && uv run pytest tests/test_router_types.py -v
```

Expect: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add backend/router/types.py backend/router/__init__.py backend/tests/test_router_types.py
git commit -m "feat(router): add BudgetHook type skeleton + allow_all default (P2-1-S6)"
```

---

### Task 3: 创建 `backend/observability/metrics.py`

**Files:**
- Create: `backend/observability/metrics.py`
- Create: `backend/tests/test_metrics.py`

- [ ] **Step 1: 写失败的 test**

```python
# backend/tests/test_metrics.py
from observability.metrics import llm_ttft_seconds, render

def test_render_returns_prometheus_format():
    body, content_type = render()
    assert isinstance(body, bytes)
    assert "text/plain" in content_type
    # the metric metadata should appear in the output even with 0 observations
    assert b"llm_ttft_seconds" in body

def test_observe_records_to_histogram():
    llm_ttft_seconds.labels(provider="local", model="gemma4:e4b").observe(0.123)
    body, _ = render()
    assert b'llm_ttft_seconds_count{model="gemma4:e4b",provider="local"} 1.0' in body \
        or b'llm_ttft_seconds_count{provider="local",model="gemma4:e4b"} 1.0' in body
```

- [ ] **Step 2: Run to verify failure**

```bash
cd backend && uv run pytest tests/test_metrics.py -v
```

Expect: ImportError.

- [ ] **Step 3: 实现 metrics.py**

```python
# backend/observability/metrics.py
"""Prometheus metrics registry for backend (P2-1-S6).

Centralized so all parts of the app import the same Histogram instances;
otherwise each module would create its own and double-register.
"""
from __future__ import annotations

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Histogram,
    generate_latest,
)

# Buckets chosen for typical LLM TTFT range:
#   local Ollama: 100ms-2s
#   cloud (DashScope/etc): 200ms-5s
_TTFT_BUCKETS = (
    0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 3.0, 5.0, 10.0, float("inf"),
)

llm_ttft_seconds = Histogram(
    "llm_ttft_seconds",
    "Time from chat_stream call to first yielded token, by provider+model",
    labelnames=["provider", "model"],
    buckets=_TTFT_BUCKETS,
)


def render() -> tuple[bytes, str]:
    """Render current metrics in Prometheus text format. Returns (body, content_type)."""
    return generate_latest(), CONTENT_TYPE_LATEST
```

- [ ] **Step 4: Run tests**

```bash
cd backend && uv run pytest tests/test_metrics.py -v
```

Expect: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/observability/metrics.py backend/tests/test_metrics.py
git commit -m "feat(observability): add Prometheus llm_ttft_seconds Histogram + render() (P2-1-S6)"
```

---

### Task 4: 在 HybridRouter 内插 TTFT 包装

**Files:**
- Modify: `backend/router/hybrid_router.py`
- Modify: `backend/tests/test_hybrid_router.py`

- [ ] **Step 1: 写失败的 test**

```python
# backend/tests/test_hybrid_router.py — append
@pytest.mark.asyncio
async def test_ttft_recorded_for_local_path(monkeypatch):
    """First yielded token from local provider must observe llm_ttft_seconds."""
    from observability.metrics import llm_ttft_seconds

    fake_now = [1000.0]
    monkeypatch.setattr("router.hybrid_router._now", lambda: fake_now[0])

    local = _FakeProvider(health=True, chat_chunks=["hi"])
    local.model = "fake-local"
    router = HybridRouter(local=local, cloud=None)

    sample_before = llm_ttft_seconds.labels(
        provider="local", model="fake-local"
    )._sum.get()

    # advance fake clock between call start and first yield
    async def _advance_then_collect():
        out = []
        async for tok in router.chat_stream([{"role": "user", "content": "x"}]):
            fake_now[0] += 0.5  # 500ms before first yield is observed
            out.append(tok)
        return out

    await _advance_then_collect()

    sample_after = llm_ttft_seconds.labels(
        provider="local", model="fake-local"
    )._sum.get()
    assert sample_after > sample_before  # at least one observation recorded


@pytest.mark.asyncio
async def test_ttft_recorded_for_cloud_path(monkeypatch):
    from observability.metrics import llm_ttft_seconds
    fake_now = [2000.0]
    monkeypatch.setattr("router.hybrid_router._now", lambda: fake_now[0])

    local = _FakeProvider(health=False)
    cloud = _FakeProvider(health=True, chat_chunks=["c1"])
    cloud.model = "fake-cloud"
    router = HybridRouter(local=local, cloud=cloud)

    before = llm_ttft_seconds.labels(provider="cloud", model="fake-cloud")._sum.get()
    await _collect(router.chat_stream([{"role": "user", "content": "x"}]))
    after = llm_ttft_seconds.labels(provider="cloud", model="fake-cloud")._sum.get()
    assert after > before
```

- [ ] **Step 2: Run to verify failure**

```bash
cd backend && uv run pytest tests/test_hybrid_router.py::test_ttft_recorded_for_local_path tests/test_hybrid_router.py::test_ttft_recorded_for_cloud_path -v
```

Expect: 2 failed (TTFT not yet wired).

- [ ] **Step 3: 改 hybrid_router.py — 加 `_stream_with_ttft` + 接入 local/cloud 路径**

```python
# backend/router/hybrid_router.py — top imports
from observability.metrics import llm_ttft_seconds
from router.types import BudgetHook, allow_all_budget

# replace existing __init__ signature for budget_check param
def __init__(
    self,
    *,
    local: LLMProvider | None,
    cloud: LLMProvider | None,
    strategy: RoutingStrategy = RoutingStrategy.LOCAL_FIRST,
    budget_check: BudgetHook | None = None,
) -> None:
    self._local = local
    self._cloud = cloud
    self._strategy = strategy
    self._budget_check = budget_check or allow_all_budget
    self._local_state = _ProviderState()
    self._cloud_state = _ProviderState()

# add helper after __init__
async def _stream_with_ttft(
    self, provider, provider_label: str, messages, **kwargs
):
    t0 = _now()
    first = True
    async for tok in provider.chat_stream(messages, **kwargs):
        if first:
            llm_ttft_seconds.labels(
                provider=provider_label,
                model=getattr(provider, "model", "unknown"),
            ).observe(_now() - t0)
            first = False
        yield tok
```

Then in `chat_stream` body, replace the **two** direct `provider.chat_stream(...)` call sites:

- local path (around line 143): `async for tok in self._stream_with_ttft(self._local, "local", messages, temperature=temperature, max_tokens=max_tokens):`
- cloud path inside `_stream_cloud` (around line 178): `async for tok in self._stream_with_ttft(self._cloud, "cloud", messages, temperature=temperature, max_tokens=max_tokens):`

- [ ] **Step 4: Run tests — new ones pass + all existing pass**

```bash
cd backend && uv run pytest tests/test_hybrid_router.py -v
```

Expect: 21 passed (19 original + 2 new TTFT).

- [ ] **Step 5: Run full backend suite — no regressions**

```bash
cd backend && uv run pytest -q
```

Expect: ≥ 165 passed (163 original + 3 router types + 2 metrics + 2 TTFT — but some may overlap with same router file).

- [ ] **Step 6: Commit**

```bash
git add backend/router/hybrid_router.py backend/tests/test_hybrid_router.py
git commit -m "feat(router): wrap chat_stream with TTFT instrumentation + upgrade BudgetHook signature (P2-1-S6)"
```

---

### Task 5: 挂 `/metrics` 路由到 FastAPI

**Files:**
- Modify: `backend/main.py`
- Create: `backend/tests/test_metrics_endpoint.py`

- [ ] **Step 1: 写失败的 test**

```python
# backend/tests/test_metrics_endpoint.py
"""Test /metrics endpoint auth + content (P2-1-S6)."""
import pytest
from fastapi.testclient import TestClient

# Note: importing main triggers full app construction (router, providers).
# That's fine for this test — we just exercise the route.

def test_metrics_requires_secret_when_dev_mode_off(monkeypatch):
    monkeypatch.setattr("main.DEV_MODE", False)
    from main import app
    client = TestClient(app)
    resp = client.get("/metrics")
    assert resp.status_code == 401

def test_metrics_open_in_dev_mode(monkeypatch):
    monkeypatch.setattr("main.DEV_MODE", True)
    from main import app
    client = TestClient(app)
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "llm_ttft_seconds" in resp.text

def test_metrics_with_correct_secret(monkeypatch):
    monkeypatch.setattr("main.DEV_MODE", False)
    from main import app, SHARED_SECRET
    client = TestClient(app)
    resp = client.get("/metrics", headers={"x-shared-secret": SHARED_SECRET})
    assert resp.status_code == 200
```

- [ ] **Step 2: Run to verify failure**

```bash
cd backend && uv run pytest tests/test_metrics_endpoint.py -v
```

Expect: 3 failed (route doesn't exist).

- [ ] **Step 3: 在 main.py 加 /metrics 路由**

```python
# backend/main.py — top imports
from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect
from observability.metrics import render as render_metrics

# add after the existing /health endpoint
@app.get("/metrics")
async def metrics(request: Request):
    if not DEV_MODE:
        secret = request.headers.get("x-shared-secret", "")
        if not secret or not secrets.compare_digest(secret, SHARED_SECRET):
            return Response(status_code=401)
    body, content_type = render_metrics()
    return Response(content=body, media_type=content_type)
```

- [ ] **Step 4: Run tests**

```bash
cd backend && uv run pytest tests/test_metrics_endpoint.py -v
```

Expect: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/main.py backend/tests/test_metrics_endpoint.py
git commit -m "feat(main): mount /metrics endpoint with secret-or-dev-mode auth (P2-1-S6)"
```

---

### Task 6: 创建 `scripts/perf/ttft_cloud.py`

**Files:**
- Create: `backend/scripts/perf/ttft_cloud.py`

- [ ] **Step 1: 实现 perf 脚本**

```python
# backend/scripts/perf/ttft_cloud.py
"""P2-1-S6 cloud TTFT smoke — run N rounds of force_cloud chat, print p50/p95.

Usage:
    DESKPET_DEV_MODE=1 python main.py &
    python scripts/perf/ttft_cloud.py --rounds 10

Requires cloud provider configured (config.toml [llm.cloud] uncommented +
DESKPET_CLOUD_API_KEY env). Skips with notice if cloud not reachable.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time

import websockets


async def one_round(url: str, prompt: str) -> float:
    async with websockets.connect(url) as ws:
        t0 = time.perf_counter()
        await ws.send(json.dumps({
            "type": "chat",
            "payload": {"text": prompt, "force_cloud": True},
        }))
        # Wait until first non-pong response (chat_response carries final text;
        # for TTFT we approximate using ws response time since /ws/control
        # buffers full reply. Real per-token TTFT requires audio channel.)
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=30.0)
            msg = json.loads(raw)
            if msg.get("type") == "chat_response":
                return time.perf_counter() - t0


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--port", type=int, default=8100)
    args = parser.parse_args()

    url = f"ws://127.0.0.1:{args.port}/ws/control?secret=&session_id=ttft-smoke"
    samples = []
    for i in range(args.rounds):
        try:
            dt = await one_round(url, f"用一句话介绍中国第{i+1}大城市")
            samples.append(dt)
            print(f"  round {i+1}: {dt*1000:.0f} ms")
        except Exception as e:
            print(f"  round {i+1}: FAILED {type(e).__name__} {e}")

    if not samples:
        print("[ttft_cloud] no successful samples; cloud probably unconfigured")
        return 1

    print(f"\n[ttft_cloud] n={len(samples)} "
          f"p50={statistics.median(samples)*1000:.0f}ms "
          f"max={max(samples)*1000:.0f}ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
```

- [ ] **Step 2: Smoke run (skip if no cloud configured)**

```bash
# Only smoke locally if cloud is set up; otherwise skip — script self-reports
cd backend && uv run python scripts/perf/ttft_cloud.py --rounds 2 2>&1 | tail -10
```

Expect: either 2 samples printed, or `cloud probably unconfigured` notice.

- [ ] **Step 3: Commit**

```bash
git add backend/scripts/perf/ttft_cloud.py
git commit -m "feat(perf): add ttft_cloud.py smoke script (P2-1-S6)"
```

---

### Task 7: Manual E2E

- [ ] **Step 1: 启动 backend with DEV_MODE**

```bash
cd backend && DESKPET_DEV_MODE=1 uv run python main.py &
sleep 5
```

- [ ] **Step 2: 跑 smoke_chat.py 一次（生成 1 个 TTFT sample）**

```bash
cd backend && uv run python scripts/smoke_chat.py
```

Expect: PASS line.

- [ ] **Step 3: 抓 /metrics 验证 histogram**

```bash
curl -s http://127.0.0.1:8100/metrics | grep llm_ttft_seconds
```

Expect lines like:
```
llm_ttft_seconds_bucket{provider="local",model="gemma4:e4b",le="0.5"} 1.0
llm_ttft_seconds_count{provider="local",model="gemma4:e4b"} 1.0
```

- [ ] **Step 4: 关 backend**

```bash
taskkill //F //IM python.exe
```

If any step fails, fix root cause then re-run.

---

### Task 8: HANDOFF 文档

**Files:**
- Create: `docs/superpowers/handoffs/p2-1-s6-ttft-metrics.md`

- [ ] **Step 1: 写 handoff（仿 p2-1-s2 格式）**

包含：Goal / Commits / Files changed / Behavior contract（/metrics auth model + TTFT label set）/ Out of scope（其他指标推迟）/ 下一步指向 S8 接 BudgetHook impl。

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/handoffs/p2-1-s6-ttft-metrics.md
git commit -m "docs(p2-1-s6): handoff for TTFT metrics + BudgetHook skeleton slice"
```

---

## 完成判据

- [ ] `pytest backend/` 全绿
- [ ] `curl /metrics` 看到 llm_ttft_seconds histogram
- [ ] HybridRouter 接受 BudgetHook 签名（向后兼容 None）
- [ ] handoff 文档已写
- [ ] 8 个 commit 干净，可被 master fast-forward
