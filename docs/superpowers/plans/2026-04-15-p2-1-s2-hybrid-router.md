# P2-1-S2 HybridRouter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 引入 `HybridRouter`，按 `local_first` 策略路由 chat 请求；本地不可达或调用方传 `force_cloud=True` 时切云端；带 30s TTL health 缓存 + per-provider circuit breaker；失败抛 `LLMUnavailableError` 由 main.py 现有 `[echo]` 兜底。HybridRouter 实现 `LLMProvider` Protocol，原 `service_context.llm_engine` 由 `OpenAICompatibleProvider` 直接持有改为 router 持有。

**Architecture:**
- `backend/router/hybrid_router.py` 新增 router；持有 `local: LLMProvider | None` + `cloud: LLMProvider | None` 两个固定槽
- `config.toml` `[llm]` 段拆为 `[llm.local]` + `[llm.cloud]`（cloud 可缺）；`AppConfig.llm` 由 `LLMConfig` 改为 `LLMRoutingConfig`（含 local + cloud + strategy + daily_budget_cny）
- `providers/base.py::LLMProvider` 不改协议（router 保持兼容）
- 预算钩子 `budget_check: Callable[[], bool] | None`，S2 阶段不挂；S8 来挂
- API key 仍走 config 明文（标 TODO，S3 迁 Credential Manager）

**Tech Stack:** Python 3.11 / asyncio / httpx (transitive via OpenAICompatibleProvider) / pytest-asyncio / structlog / dataclasses / tomli

---

## File Structure

| 路径 | 责任 | 操作 |
|---|---|---|
| `backend/router/__init__.py` | 包入口 | 创建（空 + `from .hybrid_router import HybridRouter`） |
| `backend/router/hybrid_router.py` | HybridRouter 主类 + 状态机 + LLMUnavailableError | 创建 |
| `backend/config.py` | 扩展为 `LLMRoutingConfig` | 修改 |
| `backend/main.py` | 用 HybridRouter 替换裸 OpenAICompatibleProvider | 修改 |
| `config.toml` | `[llm]` → `[llm.local]` + `[llm.cloud]` | 修改 |
| `backend/tests/test_hybrid_router.py` | router 单测（7 场景） | 创建 |
| `backend/tests/test_config.py` | 加 routing config 测试 | 修改 |
| `docs/superpowers/STATE.md` | 标 S2 ✅ shipped | 修改 |
| `docs/superpowers/handoffs/p2-1-s2-hybrid-router.md` | slice handoff | 创建 |

---

## Task 1: 定义 LLMUnavailableError + Router 基础类型

**Files:**
- Create: `backend/router/__init__.py`
- Create: `backend/router/hybrid_router.py`
- Create: `backend/tests/test_hybrid_router.py`

- [ ] **Step 1: 写第一个失败测试 — Router 是 LLMProvider**

写到 `backend/tests/test_hybrid_router.py`：

```python
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
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd backend && uv run pytest tests/test_hybrid_router.py -v
```

预期：`ModuleNotFoundError: No module named 'router'`

- [ ] **Step 3: 创建 router 包 + 最小骨架**

写到 `backend/router/__init__.py`：

```python
from .hybrid_router import HybridRouter, LLMUnavailableError, RoutingStrategy

__all__ = ["HybridRouter", "LLMUnavailableError", "RoutingStrategy"]
```

写到 `backend/router/hybrid_router.py`：

```python
"""HybridRouter — local_first LLM 路由 + circuit breaker + 预算钩子.

Implements LLMProvider Protocol, drop-in replacement for a single
OpenAICompatibleProvider in service_context.llm_engine.

Design references:
  docs/superpowers/specs/2026-04-15-p2-1-design.md §4.4 / §3.3
  docs/superpowers/plans/2026-04-15-p2-1-s2-hybrid-router.md
"""
from __future__ import annotations

import enum
from typing import AsyncIterator, Callable

import structlog

from providers.base import LLMProvider

logger = structlog.get_logger()


class LLMUnavailableError(RuntimeError):
    """All routes exhausted (local + cloud both failed or unconfigured)."""


class RoutingStrategy(str, enum.Enum):
    LOCAL_FIRST = "local_first"
    CLOUD_FIRST = "cloud_first"      # P2-1-S2 unimplemented
    COST_AWARE = "cost_aware"        # P2-1-S2 unimplemented
    LATENCY_AWARE = "latency_aware"  # P2-1-S2 unimplemented


class HybridRouter:
    """Routes chat_stream calls between a local and a cloud provider.

    S2 implements only `local_first`. Other strategies parse from config
    but raise NotImplementedError on use, so a future slice can fill them
    in without changing the public surface.
    """

    def __init__(
        self,
        *,
        local: LLMProvider | None,
        cloud: LLMProvider | None,
        strategy: RoutingStrategy = RoutingStrategy.LOCAL_FIRST,
        budget_check: Callable[[], bool] | None = None,
    ) -> None:
        self._local = local
        self._cloud = cloud
        self._strategy = strategy
        self._budget_check = budget_check

    async def chat_stream(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> AsyncIterator[str]:
        raise NotImplementedError("filled in Task 5")
        yield  # pragma: no cover  (make it an async generator)

    async def health_check(self) -> bool:
        raise NotImplementedError("filled in Task 4")
```

- [ ] **Step 4: 跑测试确认通过**

```bash
cd backend && uv run pytest tests/test_hybrid_router.py::test_hybrid_router_implements_llm_provider_protocol -v
```

预期：PASS

- [ ] **Step 5: Commit**

```bash
git add backend/router/__init__.py backend/router/hybrid_router.py backend/tests/test_hybrid_router.py
git commit -m "feat(router): add HybridRouter skeleton + LLMUnavailableError"
```

---

## Task 2: 配置 schema 拆分 — `[llm]` → `[llm.local]` + `[llm.cloud]`

**Files:**
- Modify: `backend/config.py`
- Modify: `config.toml`
- Modify: `backend/tests/test_config.py`

- [ ] **Step 1: 写失败测试**

加到 `backend/tests/test_config.py` 末尾：

```python
def test_load_config_parses_llm_routing_with_local_and_cloud(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(dedent("""
        [llm]
        strategy = "local_first"
        daily_budget_cny = 10.0

        [llm.local]
        model = "gemma4:e4b"
        base_url = "http://localhost:11434/v1"
        api_key = "ollama"
        temperature = 0.7

        [llm.cloud]
        model = "qwen3.6-plus"
        base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
        api_key = "sk-test-not-real"
        temperature = 0.7
    """).strip())

    cfg = load_config(cfg_path)
    assert cfg.llm.strategy == "local_first"
    assert cfg.llm.daily_budget_cny == 10.0
    assert cfg.llm.local.model == "gemma4:e4b"
    assert cfg.llm.cloud is not None
    assert cfg.llm.cloud.model == "qwen3.6-plus"


def test_load_config_llm_cloud_optional(tmp_path):
    """No [llm.cloud] section → cfg.llm.cloud is None, router runs local-only."""
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(dedent("""
        [llm]
        strategy = "local_first"

        [llm.local]
        model = "gemma4:e4b"
        base_url = "http://localhost:11434/v1"
        api_key = "ollama"
    """).strip())

    cfg = load_config(cfg_path)
    assert cfg.llm.cloud is None
    assert cfg.llm.local.model == "gemma4:e4b"
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd backend && uv run pytest tests/test_config.py -v
```

预期：FAIL（AttributeError: 'LLMConfig' object has no attribute 'strategy' 等）

- [ ] **Step 3: 改 `backend/config.py`**

替换 `LLMConfig` dataclass 与 `load_config` 中 `if "llm" in raw` 块：

```python
@dataclass
class LLMEndpointConfig:
    """Per-endpoint config (local or cloud). Mirrors OpenAICompatibleProvider ctor."""
    model: str = "gemma4:e4b"
    base_url: str = "http://localhost:11434/v1"
    api_key: str = "ollama"
    temperature: float = 0.7
    max_tokens: int = 2048


@dataclass
class LLMRoutingConfig:
    strategy: str = "local_first"
    daily_budget_cny: float = 10.0
    local: LLMEndpointConfig = field(default_factory=LLMEndpointConfig)
    cloud: LLMEndpointConfig | None = None
```

`AppConfig.llm` 类型改为 `LLMRoutingConfig`：

```python
@dataclass
class AppConfig:
    backend: BackendConfig = field(default_factory=BackendConfig)
    llm: LLMRoutingConfig = field(default_factory=LLMRoutingConfig)
    # ... rest unchanged
```

`load_config` 中 `[llm]` 处理改为：

```python
if "llm" in raw:
    raw_llm = raw["llm"]
    raw_local = raw_llm.pop("local", None)
    raw_cloud = raw_llm.pop("cloud", None)
    routing = _load_section(LLMRoutingConfig, raw_llm)
    if raw_local is not None:
        routing.local = _load_section(LLMEndpointConfig, raw_local)
    if raw_cloud is not None:
        routing.cloud = _load_section(LLMEndpointConfig, raw_cloud)
    config.llm = routing
```

旧 `LLMConfig` 删掉。

- [ ] **Step 4: 改 `config.toml`** （顶层）

把现有 `[llm]` 段替换为：

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

# [llm.cloud]
# model = "qwen3.6-plus"
# base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
# api_key = ""  # TODO(P2-1-S3): move to Windows Credential Manager
# temperature = 0.7
# max_tokens = 2048
```

- [ ] **Step 5: 跑全部 config 测试**

```bash
cd backend && uv run pytest tests/test_config.py -v
```

预期：PASS（含已有的 `test_load_config_ignores_unknown_toml_keys`）

- [ ] **Step 6: Commit**

```bash
git add backend/config.py backend/tests/test_config.py config.toml
git commit -m "feat(config): split [llm] into routing + local/cloud endpoints"
```

---

## Task 3: HybridRouter 内部状态 — circuit breaker + health TTL

**Files:**
- Modify: `backend/router/hybrid_router.py`
- Modify: `backend/tests/test_hybrid_router.py`

设计参数（硬编码常量）：
- `_HEALTH_TTL_SECONDS = 30.0`
- `_CIRCUIT_OPEN_AFTER_FAILURES = 3`
- `_CIRCUIT_OPEN_DURATION_SECONDS = 30.0`

- [ ] **Step 1: 写失败测试**

加到 `backend/tests/test_hybrid_router.py`：

```python
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
```

- [ ] **Step 2: 跑确认失败**

```bash
cd backend && uv run pytest tests/test_hybrid_router.py -v -k "provider_state or health_cache"
```

预期：FAIL（ImportError on `_ProviderState`）

- [ ] **Step 3: 实现状态类**

加到 `backend/router/hybrid_router.py`（顶部 import 后、`HybridRouter` 类前）：

```python
import time

_HEALTH_TTL_SECONDS = 30.0
_CIRCUIT_OPEN_AFTER_FAILURES = 3
_CIRCUIT_OPEN_DURATION_SECONDS = 30.0


def _now() -> float:
    """Monkeypatchable time source for deterministic tests."""
    return time.monotonic()


class _CircuitState(str, enum.Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class _ProviderState:
    """Per-provider rolling state: circuit breaker + health cache.

    Held by HybridRouter as a private attribute; not part of public API.
    """

    def __init__(self) -> None:
        self.consecutive_failures: int = 0
        self.circuit: _CircuitState = _CircuitState.CLOSED
        self._opened_at: float | None = None
        self._health_value: bool | None = None
        self._health_at: float | None = None

    # --- circuit breaker ---

    def record_chat_failure(self) -> None:
        self.consecutive_failures += 1
        if self.consecutive_failures >= _CIRCUIT_OPEN_AFTER_FAILURES:
            self.circuit = _CircuitState.OPEN
            self._opened_at = _now()

    def record_chat_success(self) -> None:
        self.consecutive_failures = 0
        self.circuit = _CircuitState.CLOSED
        self._opened_at = None

    def circuit_state_now(self) -> _CircuitState:
        """Returns logical state, transitioning OPEN→HALF_OPEN if cooldown elapsed."""
        if self.circuit == _CircuitState.OPEN and self._opened_at is not None:
            if _now() - self._opened_at >= _CIRCUIT_OPEN_DURATION_SECONDS:
                return _CircuitState.HALF_OPEN
        return self.circuit

    # --- health cache ---

    def cache_health(self, value: bool) -> None:
        self._health_value = value
        self._health_at = _now()

    def cached_health(self) -> bool | None:
        if self._health_at is None:
            return None
        if _now() - self._health_at > _HEALTH_TTL_SECONDS:
            return None
        return self._health_value
```

- [ ] **Step 4: 跑确认通过**

```bash
cd backend && uv run pytest tests/test_hybrid_router.py -v -k "provider_state or health_cache"
```

预期：5 个 PASS

- [ ] **Step 5: Commit**

```bash
git add backend/router/hybrid_router.py backend/tests/test_hybrid_router.py
git commit -m "feat(router): add per-provider circuit breaker + health TTL cache"
```

---

## Task 4: HybridRouter.health_check 实现

**Files:**
- Modify: `backend/router/hybrid_router.py`
- Modify: `backend/tests/test_hybrid_router.py`

语义：router 的 health = 任一 provider 健康即为健康。供 `/healthz` 端点用。

- [ ] **Step 1: 写失败测试**

加到 `backend/tests/test_hybrid_router.py`：

```python
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
```

- [ ] **Step 2: 跑确认失败**

```bash
cd backend && uv run pytest tests/test_hybrid_router.py -v -k "router_health"
```

预期：FAIL（NotImplementedError）

- [ ] **Step 3: 实现 health_check + 内部 helper**

替换 `HybridRouter.__init__` + 添加私有 helper + 实现 health_check：

```python
class HybridRouter:
    def __init__(
        self,
        *,
        local: LLMProvider | None,
        cloud: LLMProvider | None,
        strategy: RoutingStrategy = RoutingStrategy.LOCAL_FIRST,
        budget_check: Callable[[], bool] | None = None,
    ) -> None:
        self._local = local
        self._cloud = cloud
        self._strategy = strategy
        self._budget_check = budget_check
        self._local_state = _ProviderState()
        self._cloud_state = _ProviderState()

    async def _check_health(self, provider: LLMProvider, state: _ProviderState) -> bool:
        cached = state.cached_health()
        if cached is not None:
            return cached
        try:
            ok = await provider.health_check()
        except Exception as exc:
            logger.warning("router_health_check_raised", error=str(exc))
            ok = False
        state.cache_health(ok)
        return ok

    async def health_check(self) -> bool:
        if self._local is not None and await self._check_health(self._local, self._local_state):
            return True
        if self._cloud is not None and await self._check_health(self._cloud, self._cloud_state):
            return True
        return False

    async def chat_stream(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> AsyncIterator[str]:
        raise NotImplementedError("filled in Task 5")
        yield  # pragma: no cover
```

- [ ] **Step 4: 跑确认通过**

```bash
cd backend && uv run pytest tests/test_hybrid_router.py -v -k "router_health"
```

预期：5 个 PASS

- [ ] **Step 5: Commit**

```bash
git add backend/router/hybrid_router.py backend/tests/test_hybrid_router.py
git commit -m "feat(router): implement health_check with TTL cache"
```

---

## Task 5: HybridRouter.chat_stream — local_first + force_cloud + fallback

**Files:**
- Modify: `backend/router/hybrid_router.py`
- Modify: `backend/tests/test_hybrid_router.py`

行为契约：
- `force_cloud=True` 直接走云端；云端不可达 → `LLMUnavailableError`
- `force_cloud=False` (默认)：
  1. 若 local circuit OPEN（未到 HALF_OPEN）→ 跳过 local
  2. 否则尝试 local；本地 health 失败 或 chat_stream 抛错（首 token 前）→ 转 cloud
  3. cloud 不存在 / cloud 失败 → `LLMUnavailableError`
- 已开始流式输出后失败 → 抛错给上游（不 silent 切换，避免重复 token）
- 其他策略：调到 `_strategy != LOCAL_FIRST` → `NotImplementedError`

- [ ] **Step 1: 写失败测试（7 场景）**

加到 `backend/tests/test_hybrid_router.py`：

```python
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
```

- [ ] **Step 2: 跑确认失败**

```bash
cd backend && uv run pytest tests/test_hybrid_router.py -v -k "chat or circuit"
```

预期：FAIL

- [ ] **Step 3: 实现 chat_stream**

替换 `HybridRouter.chat_stream` 方法体：

```python
    async def chat_stream(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        force_cloud: bool = False,
    ) -> AsyncIterator[str]:
        if self._strategy != RoutingStrategy.LOCAL_FIRST:
            raise NotImplementedError(
                f"strategy {self._strategy} not implemented in P2-1-S2"
            )

        if force_cloud:
            async for tok in self._stream_cloud(messages, temperature, max_tokens):
                yield tok
            return

        # local_first
        if self._local is not None:
            local_state = self._local_state
            if local_state.circuit_state_now() != _CircuitState.OPEN:
                if await self._check_health(self._local, local_state):
                    try:
                        async for tok in self._local.chat_stream(
                            messages, temperature=temperature, max_tokens=max_tokens
                        ):
                            yield tok
                        local_state.record_chat_success()
                        return
                    except Exception as exc:
                        local_state.record_chat_failure()
                        # invalidate cached health so next call re-probes
                        local_state.cache_health(False)
                        logger.warning(
                            "router_local_chat_failed_falling_back_cloud",
                            error=str(exc),
                            consecutive_failures=local_state.consecutive_failures,
                        )

        # local skipped or local failed — try cloud
        async for tok in self._stream_cloud(messages, temperature, max_tokens):
            yield tok

    async def _stream_cloud(
        self, messages: list[dict[str, str]], temperature: float, max_tokens: int
    ) -> AsyncIterator[str]:
        if self._cloud is None:
            raise LLMUnavailableError(
                "cloud provider not configured and local unavailable"
            )
        cloud_state = self._cloud_state
        if cloud_state.circuit_state_now() == _CircuitState.OPEN:
            raise LLMUnavailableError(
                "cloud circuit breaker OPEN, retry in <30s"
            )
        if not await self._check_health(self._cloud, cloud_state):
            raise LLMUnavailableError(
                "cloud provider health_check failed and local unavailable"
            )
        try:
            async for tok in self._cloud.chat_stream(
                messages, temperature=temperature, max_tokens=max_tokens
            ):
                yield tok
            cloud_state.record_chat_success()
        except Exception as exc:
            cloud_state.record_chat_failure()
            cloud_state.cache_health(False)
            logger.error("router_cloud_chat_failed", error=str(exc))
            raise LLMUnavailableError(f"cloud chat failed: {exc}") from exc
```

注意：`LLMProvider` Protocol 签名 (`chat_stream(messages, *, temperature, max_tokens)`) 不含 `force_cloud`；新增的 `force_cloud` 是 router 私有扩展，不破坏 Protocol。`isinstance(..., LLMProvider)` 仍然 True，因为 Protocol 看的是必需方法名+参数子集。

- [ ] **Step 4: 跑全部 router 测试**

```bash
cd backend && uv run pytest tests/test_hybrid_router.py -v
```

预期：全部 PASS（含 Task 1/3/4 的）

- [ ] **Step 5: Commit**

```bash
git add backend/router/hybrid_router.py backend/tests/test_hybrid_router.py
git commit -m "feat(router): implement local_first chat_stream with circuit breaker fallback"
```

---

## Task 6: 接入 main.py — service_context.llm_engine 改为 HybridRouter

**Files:**
- Modify: `backend/main.py`

- [ ] **Step 1: 改 import + 实例化**

定位 `backend/main.py` 第 36-58 行附近的 LLM 注册块：

```python
# --- Register providers ---
from providers.openai_compatible import OpenAICompatibleProvider
from providers.silero_vad import SileroVAD
from providers.faster_whisper_asr import FasterWhisperASR
# ...
from router.hybrid_router import HybridRouter, RoutingStrategy

local_llm = OpenAICompatibleProvider(
    base_url=config.llm.local.base_url,
    api_key=config.llm.local.api_key,
    model=config.llm.local.model,
    temperature=config.llm.local.temperature,
)

cloud_llm = None
if config.llm.cloud is not None:
    cloud_llm = OpenAICompatibleProvider(
        base_url=config.llm.cloud.base_url,
        api_key=config.llm.cloud.api_key,
        model=config.llm.cloud.model,
        temperature=config.llm.cloud.temperature,
    )

llm = HybridRouter(
    local=local_llm,
    cloud=cloud_llm,
    strategy=RoutingStrategy(config.llm.strategy),
    budget_check=None,  # TODO(P2-1-S8): wire BillingLedger
)
service_context.register("llm_engine", llm)
```

- [ ] **Step 2: 全套 backend pytest**

```bash
cd backend && uv run pytest -v
```

预期：全部 PASS（包括 Task 1-5 + 已有 142 个）

- [ ] **Step 3: 启动 backend，跑 smoke_chat.py**

终端 1：
```bash
cd backend
$env:DESKPET_DEV_MODE="1"
uv run python main.py
```

终端 2（等 backend 就绪后）：
```bash
cd backend && uv run python scripts/smoke_chat.py
```

预期：
```
[smoke] VERDICT: PASS — real LLM reply via agent->provider->Ollama
```

（链路现在是 ws/control → agent_engine → HybridRouter → local OpenAICompatibleProvider → Ollama）

- [ ] **Step 4: Stop backend (Ctrl+C 终端 1)**

- [ ] **Step 5: Commit**

```bash
git add backend/main.py
git commit -m "feat(router): wire HybridRouter into service_context.llm_engine"
```

---

## Task 7: 集成测试 — 真 OpenAICompatibleProvider 跑 router

**Files:**
- Modify: `backend/tests/test_hybrid_router.py`

目标：用 `httpx.MockTransport` 注入两个真 `OpenAICompatibleProvider`，验证 router 在 Protocol 真实实现上仍然路由正确（不只对 fake stub）。

- [ ] **Step 1: 写测试**

加到 `backend/tests/test_hybrid_router.py`：

```python
import httpx
import json
from providers.openai_compatible import OpenAICompatibleProvider


def _sse_done() -> bytes:
    return b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\ndata: [DONE]\n\n'


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
```

- [ ] **Step 2: 跑确认通过**

```bash
cd backend && uv run pytest tests/test_hybrid_router.py::test_router_with_real_providers_routes_to_local_when_healthy -v
```

预期：PASS

- [ ] **Step 3: 全套 pytest 最终绿灯**

```bash
cd backend && uv run pytest -v
```

预期：所有测试 PASS（>= 150 passed）

- [ ] **Step 4: Commit**

```bash
git add backend/tests/test_hybrid_router.py
git commit -m "test(router): integration test with real OpenAICompatibleProvider"
```

---

## Task 8: STATE.md + handoff 文档

**Files:**
- Modify: `docs/superpowers/STATE.md`
- Create: `docs/superpowers/handoffs/p2-1-s2-hybrid-router.md`

- [ ] **Step 1: 改 STATE.md**

定位 `## Active P2-1 slices` 表，加 S2 行：

```markdown
| S2 | ✅ merged | HybridRouter (local_first + circuit breaker) replaces direct OpenAICompatibleProvider |
```

把"Last updated"行更新为：
```
**Last updated:** 2026-04-XX (P2-1-S2 HybridRouter shipped)
```

把"P2-1"那行的 pending list 由 "S2/S3/S6/S7/S8 pending" 改为 "S3/S6/S7/S8 pending"。

- [ ] **Step 2: 写 handoff**

新建 `docs/superpowers/handoffs/p2-1-s2-hybrid-router.md`，用 `p2s7-release-v0.2.0.md` 同款骨架，至少含：
- **Goal**：一句话
- **Files changed**：列出 6 个文件
- **Behavior contract**：local_first 触发表 + force_cloud 语义 + circuit breaker 状态转移
- **Out of scope (deferred to later slices)**：cost_aware/latency_aware 策略；Credential Manager (S3)；budget hook 接入 (S8)；fallback E2E (S7)；TTFT 埋点 (S6)
- **Manual verification**：smoke_chat.py 跑通 + 手动改 config.toml 把 local base_url 指错验证 fallback（如果 cloud 已配 key）
- **Known issues**：None

- [ ] **Step 3: 全套 pytest 最终验证**

```bash
cd backend && uv run pytest -v
```

预期：全绿

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/STATE.md docs/superpowers/handoffs/p2-1-s2-hybrid-router.md
git commit -m "docs(p2-1-s2): handoff + STATE update for HybridRouter slice"
```

- [ ] **Step 5: Final code review (slice-wide)**

派 code-reviewer agent 看 commit range，输入：
- branch: `feat/p2-1-s2-hybrid-router`
- commit range: from branch base to HEAD
- review focus: race conditions in `_ProviderState`（router 是单例 + 多 ws session 共享 → 并发安全？）；error message clarity；budget_check hook surface adequacy for S8

---

## Self-Review Checklist

- [x] 所有任务都有具体文件路径 ✓
- [x] 每段代码完整可粘贴，无 "TBD"/"add error handling" 占位 ✓
- [x] 测试在实现之前 (TDD) ✓
- [x] 类型一致：`LLMProvider` Protocol / `_CircuitState` enum / `LLMRoutingConfig` dataclass 在多任务间引用一致 ✓
- [x] Spec §4.4 / §3.3 的 HybridRouter 覆盖（除 persona / budget / 多策略 已显式标 out-of-scope）✓
- [x] backward-compat：旧 `[llm]` 段会被 `_load_section` 接住的 unknown-key 警告掉但不崩 — 实际行为是 Old-style 配置下 `cfg.llm.local` 用默认值，`cfg.llm.cloud` = None；user 升级时需要手改 config.toml（CHANGELOG 提醒）

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-15-p2-1-s2-hybrid-router.md`.

**推荐：Subagent-Driven Execution** — 8 个 task 各派 fresh implementer + spec reviewer + code quality reviewer，控制 token 同时保证质量。S1 已经走通这套流程。
