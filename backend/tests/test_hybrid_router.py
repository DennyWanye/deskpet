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
