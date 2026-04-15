"""HybridRouter 单元测试 (P2-1-S2).

Strategy: local_first
- 本地优先，本地 health/chat 失败时 fallback 到云端
- force_cloud=True 直接走云端（per-request 一次性）
- 都失败 / 云端未配置时抛 LLMUnavailableError
- circuit breaker: 单 provider 连续 3 次 chat 失败进入 OPEN，
  30s 后 HALF_OPEN，成功一次回 CLOSED
- health_check 结果有 30s TTL 缓存
"""
from __future__ import annotations

import pytest
import httpx

from providers.base import LLMProvider
from providers.openai_compatible import OpenAICompatibleProvider
from router.hybrid_router import (
    HybridRouter,
    LLMUnavailableError,
    _CircuitState,
    _ProviderState,
)


def test_hybrid_router_implements_llm_provider_protocol():
    router = HybridRouter(local=None, cloud=None)
    assert isinstance(router, LLMProvider)


def test_provider_state_starts_closed():
    s = _ProviderState()
    assert s.circuit == _CircuitState.CLOSED
    assert s.consecutive_failures == 0


def test_provider_state_opens_after_three_failures():
    s = _ProviderState()
    for _ in range(3):
        s.record_chat_failure()
    assert s.circuit == _CircuitState.OPEN


def test_provider_state_half_open_after_30s(monkeypatch):
    s = _ProviderState()
    fake_now = [1000.0]
    monkeypatch.setattr("router.hybrid_router._now", lambda: fake_now[0])
    for _ in range(3):
        s.record_chat_failure()
    assert s.circuit == _CircuitState.OPEN
    fake_now[0] += 31.0  # > 30s
    assert s.circuit_state_now() == _CircuitState.HALF_OPEN


def test_provider_state_chat_success_closes_circuit(monkeypatch):
    s = _ProviderState()
    fake_now = [1000.0]
    monkeypatch.setattr("router.hybrid_router._now", lambda: fake_now[0])
    for _ in range(3):
        s.record_chat_failure()
    fake_now[0] += 31.0
    s.record_chat_success()  # HALF_OPEN trial succeeded
    assert s.circuit == _CircuitState.CLOSED
    assert s.consecutive_failures == 0


def test_health_cache_returns_within_ttl(monkeypatch):
    s = _ProviderState()
    fake_now = [1000.0]
    monkeypatch.setattr("router.hybrid_router._now", lambda: fake_now[0])
    s.cache_health(True)
    # 29s later — still cached
    fake_now[0] += 29.0
    assert s.cached_health() is True
    # 31s later — expired
    fake_now[0] += 2.0
    assert s.cached_health() is None


class _FakeProvider:
    """Minimal LLMProvider stub for router tests."""
    def __init__(self, *, health: bool = True, chat_chunks: list[str] | None = None,
                 chat_raises: Exception | None = None):
        self._health = health
        self._chat_chunks = chat_chunks or []
        self._chat_raises = chat_raises
        self.health_calls = 0
        self.chat_calls = 0

    async def health_check(self) -> bool:
        self.health_calls += 1
        return self._health

    async def chat_stream(self, messages, *, temperature=0.7, max_tokens=2048):
        self.chat_calls += 1
        if self._chat_raises is not None:
            raise self._chat_raises
        for c in self._chat_chunks:
            yield c


@pytest.mark.asyncio
async def test_router_health_check_true_when_local_healthy():
    router = HybridRouter(local=_FakeProvider(health=True), cloud=None)
    assert await router.health_check() is True


@pytest.mark.asyncio
async def test_router_health_check_true_when_cloud_healthy_only():
    router = HybridRouter(
        local=_FakeProvider(health=False),
        cloud=_FakeProvider(health=True),
    )
    assert await router.health_check() is True


@pytest.mark.asyncio
async def test_router_health_check_false_when_all_dead():
    router = HybridRouter(
        local=_FakeProvider(health=False),
        cloud=_FakeProvider(health=False),
    )
    assert await router.health_check() is False


@pytest.mark.asyncio
async def test_router_health_check_false_when_no_providers():
    router = HybridRouter(local=None, cloud=None)
    assert await router.health_check() is False


@pytest.mark.asyncio
async def test_router_health_uses_cache_within_ttl(monkeypatch):
    """Two consecutive health_check calls within TTL → underlying provider hit once."""
    fake_now = [1000.0]
    monkeypatch.setattr("router.hybrid_router._now", lambda: fake_now[0])
    local = _FakeProvider(health=True)
    router = HybridRouter(local=local, cloud=None)
    assert await router.health_check() is True
    assert await router.health_check() is True
    assert local.health_calls == 1  # cached on second call


# ---------------------------------------------------------------------------
# Task 5 — chat_stream routing logic
# ---------------------------------------------------------------------------

async def _collect(agen):
    return [x async for x in agen]


@pytest.mark.asyncio
async def test_chat_local_first_uses_local_when_healthy():
    local = _FakeProvider(health=True, chat_chunks=["hi", " local"])
    cloud = _FakeProvider(health=True, chat_chunks=["should not be called"])
    router = HybridRouter(local=local, cloud=cloud)
    out = await _collect(router.chat_stream([{"role": "user", "content": "x"}]))
    assert out == ["hi", " local"]
    assert cloud.chat_calls == 0


@pytest.mark.asyncio
async def test_chat_falls_back_to_cloud_when_local_unhealthy():
    local = _FakeProvider(health=False)
    cloud = _FakeProvider(health=True, chat_chunks=["from", " cloud"])
    router = HybridRouter(local=local, cloud=cloud)
    out = await _collect(router.chat_stream([{"role": "user", "content": "x"}]))
    assert out == ["from", " cloud"]
    assert local.chat_calls == 0


@pytest.mark.asyncio
async def test_chat_force_cloud_skips_local_entirely():
    local = _FakeProvider(health=True, chat_chunks=["local"])
    cloud = _FakeProvider(health=True, chat_chunks=["cloud"])
    router = HybridRouter(local=local, cloud=cloud)
    out = await _collect(router.chat_stream(
        [{"role": "user", "content": "x"}], force_cloud=True))
    assert out == ["cloud"]
    assert local.chat_calls == 0


@pytest.mark.asyncio
async def test_chat_force_cloud_raises_when_cloud_unconfigured():
    router = HybridRouter(local=_FakeProvider(health=True), cloud=None)
    with pytest.raises(LLMUnavailableError):
        await _collect(router.chat_stream(
            [{"role": "user", "content": "x"}], force_cloud=True))


@pytest.mark.asyncio
async def test_chat_raises_when_all_providers_dead():
    router = HybridRouter(
        local=_FakeProvider(health=False),
        cloud=_FakeProvider(health=False),
    )
    with pytest.raises(LLMUnavailableError):
        await _collect(router.chat_stream([{"role": "user", "content": "x"}]))


@pytest.mark.asyncio
async def test_circuit_opens_after_three_chat_failures(monkeypatch):
    fake_now = [1000.0]
    monkeypatch.setattr("router.hybrid_router._now", lambda: fake_now[0])
    local = _FakeProvider(health=True, chat_raises=RuntimeError("boom"))
    cloud = _FakeProvider(health=True, chat_chunks=["c"])
    router = HybridRouter(local=local, cloud=cloud)
    for _ in range(3):
        await _collect(router.chat_stream([{"role": "user", "content": "x"}]))
    # 4th call: local circuit OPEN, must skip local entirely
    await _collect(router.chat_stream([{"role": "user", "content": "x"}]))
    assert local.chat_calls == 3  # not incremented on 4th call


@pytest.mark.asyncio
async def test_circuit_recovers_on_half_open_success(monkeypatch):
    fake_now = [1000.0]
    monkeypatch.setattr("router.hybrid_router._now", lambda: fake_now[0])
    # Local fails 3x then recovers
    local = _FlakeyProvider(fail_first_n=3, then_chunks=["recovered"])
    cloud = _FakeProvider(health=True, chat_chunks=["c"])
    router = HybridRouter(local=local, cloud=cloud)
    for _ in range(3):
        await _collect(router.chat_stream([{"role": "user", "content": "x"}]))
    # 30s later → HALF_OPEN
    fake_now[0] += 31.0
    out = await _collect(router.chat_stream([{"role": "user", "content": "x"}]))
    assert out == ["recovered"]
    # circuit closed again → next call also goes local
    out2 = await _collect(router.chat_stream([{"role": "user", "content": "x"}]))
    assert out2 == ["recovered"]
    assert cloud.chat_calls == 3  # all 3 OPEN-period calls fell back to cloud


class _FlakeyProvider:
    """Fails first N chat calls, then yields then_chunks."""
    def __init__(self, *, fail_first_n: int, then_chunks: list[str]):
        self._fail = fail_first_n
        self._chunks = then_chunks
        self.chat_calls = 0

    async def health_check(self) -> bool:
        return True

    async def chat_stream(self, messages, *, temperature=0.7, max_tokens=2048):
        self.chat_calls += 1
        if self._fail > 0:
            self._fail -= 1
            raise RuntimeError("flakey boom")
        for c in self._chunks:
            yield c


# ---------------------------------------------------------------------------
# Task 7 — integration test with real OpenAICompatibleProvider + MockTransport
# ---------------------------------------------------------------------------

def _sse_done() -> bytes:
    return b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\ndata: [DONE]\n\n'


# ---------------------------------------------------------------------------
# P2-1-S6 — TTFT instrumentation
# ---------------------------------------------------------------------------


class _SlowProvider:
    """LLMProvider stub that advances a shared fake clock before first yield.

    Used by TTFT tests: the router captures ``t0 = _now()`` before pulling
    the first token, then measures ``_now() - t0`` at the first yield —
    so we need the clock to tick forward *between* those two calls, which
    only happens if the provider's own generator advances it. Bumping the
    clock in the test body after awaiting `_collect` is too late.
    """
    def __init__(self, *, clock: list[float], latency_s: float, chunks: list[str],
                 model: str = "fake-slow", health: bool = True):
        self._clock = clock
        self._latency = latency_s
        self._chunks = chunks
        self._health = health
        self.model = model

    async def health_check(self) -> bool:
        return self._health

    async def chat_stream(self, messages, *, temperature=0.7, max_tokens=2048):
        # Simulate provider-side latency before the first token is ready.
        self._clock[0] += self._latency
        for c in self._chunks:
            yield c


@pytest.mark.asyncio
async def test_ttft_recorded_for_local_path(monkeypatch):
    """First yielded token from local provider must observe llm_ttft_seconds."""
    from observability.metrics import llm_ttft_seconds

    fake_now = [1000.0]
    monkeypatch.setattr("router.hybrid_router._now", lambda: fake_now[0])

    local = _SlowProvider(clock=fake_now, latency_s=0.25,
                          chunks=["hi"], model="fake-local-ttft")
    router = HybridRouter(local=local, cloud=None)

    hist = llm_ttft_seconds.labels(provider="local", model="fake-local-ttft")
    before_sum = hist._sum.get()

    await _collect(router.chat_stream([{"role": "user", "content": "x"}]))

    after_sum = hist._sum.get()
    # We simulated ~250ms of provider latency; observation must be > 0.
    assert after_sum > before_sum


@pytest.mark.asyncio
async def test_ttft_recorded_for_cloud_path(monkeypatch):
    """First yielded token from cloud provider must observe llm_ttft_seconds."""
    from observability.metrics import llm_ttft_seconds

    fake_now = [2000.0]
    monkeypatch.setattr("router.hybrid_router._now", lambda: fake_now[0])

    local = _FakeProvider(health=False)
    cloud = _SlowProvider(clock=fake_now, latency_s=0.4,
                          chunks=["c1"], model="fake-cloud-ttft")
    router = HybridRouter(local=local, cloud=cloud)

    hist = llm_ttft_seconds.labels(provider="cloud", model="fake-cloud-ttft")
    before_sum = hist._sum.get()
    await _collect(router.chat_stream([{"role": "user", "content": "x"}]))
    after_sum = hist._sum.get()
    assert after_sum > before_sum


@pytest.mark.asyncio
async def test_ttft_skips_empty_leading_chunks(monkeypatch):
    """Empty leading chunks must NOT be counted as the first token.

    Some OpenAI-compatible backends stream an empty delta as a keep-alive
    before the real content. Observing TTFT on that chunk records a
    near-zero sample and poisons the histogram. The router must wait for
    the first truthy chunk.
    """
    from observability.metrics import llm_ttft_seconds

    fake_now = [3000.0]
    monkeypatch.setattr("router.hybrid_router._now", lambda: fake_now[0])

    class _EmptyThenReal:
        model = "fake-empty-then-real"

        async def health_check(self) -> bool:
            return True

        async def chat_stream(self, messages, *, temperature=0.7, max_tokens=2048):
            # Two keep-alive empties arrive instantly...
            yield ""
            yield ""
            # ...then real content arrives 0.6s later.
            fake_now[0] += 0.6
            yield "hello"

    router = HybridRouter(local=_EmptyThenReal(), cloud=None)

    hist = llm_ttft_seconds.labels(provider="local", model="fake-empty-then-real")
    before_sum = hist._sum.get()

    await _collect(router.chat_stream([{"role": "user", "content": "x"}]))

    # The observed delta must reflect the 0.6s simulated latency —
    # not ~0, which is what we'd get if an empty chunk triggered
    # the first-token observation.
    observed = hist._sum.get() - before_sum
    assert observed >= 0.5, f"expected >=0.5s (got {observed}); empty chunks poisoned TTFT"


@pytest.mark.asyncio
async def test_budget_hook_param_accepted():
    """HybridRouter __init__ accepts ``budget_hook`` (not ``budget_check``)."""
    from router.types import BudgetDecision

    calls: list[str] = []

    async def hook(ctx):
        calls.append(f"{ctx.route}:{ctx.model}")
        return BudgetDecision(allow=True)

    local = _FakeProvider(health=True, chat_chunks=["ok"])
    local.model = "fake-model"
    router = HybridRouter(local=local, cloud=None, budget_hook=hook)
    # Just constructing must not raise; behavioral wiring is S8's job.
    assert router is not None


@pytest.mark.asyncio
async def test_router_with_real_providers_routes_to_local_when_healthy():
    """Both providers are real OpenAICompatibleProvider w/ MockTransport injected."""
    local_calls = {"chat": 0, "models": 0}
    cloud_calls = {"chat": 0, "models": 0}

    def local_handler(req):
        if req.url.path.endswith("/models"):
            local_calls["models"] += 1
            return httpx.Response(200, json={"data": [{"id": "gemma4:e4b"}]})
        local_calls["chat"] += 1
        return httpx.Response(200, content=_sse_done(),
                              headers={"content-type": "text/event-stream"})

    def cloud_handler(req):
        if req.url.path.endswith("/models"):
            cloud_calls["models"] += 1
            return httpx.Response(200, json={"data": [{"id": "qwen3.6-plus"}]})
        cloud_calls["chat"] += 1
        return httpx.Response(200, content=_sse_done(),
                              headers={"content-type": "text/event-stream"})

    local = OpenAICompatibleProvider(
        base_url="http://local.invalid/v1", api_key="ollama", model="gemma4:e4b")
    local._test_transport = httpx.MockTransport(local_handler)
    cloud = OpenAICompatibleProvider(
        base_url="http://cloud.invalid/v1", api_key="sk", model="qwen3.6-plus")
    cloud._test_transport = httpx.MockTransport(cloud_handler)

    router = HybridRouter(local=local, cloud=cloud)
    tokens = await _collect(router.chat_stream([{"role": "user", "content": "hi"}]))

    assert tokens == ["ok"]
    assert local_calls["chat"] == 1
    assert cloud_calls["chat"] == 0  # local healthy → cloud not touched
