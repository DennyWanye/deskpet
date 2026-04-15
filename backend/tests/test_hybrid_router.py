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

from providers.base import LLMProvider
from router.hybrid_router import HybridRouter, LLMUnavailableError


def test_hybrid_router_implements_llm_provider_protocol():
    router = HybridRouter(local=None, cloud=None)
    assert isinstance(router, LLMProvider)


import time
from router.hybrid_router import _ProviderState, _CircuitState


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
