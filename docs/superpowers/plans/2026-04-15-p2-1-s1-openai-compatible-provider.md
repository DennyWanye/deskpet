# P2-1-S1 OpenAICompatibleProvider Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `backend/providers/ollama_llm.py` with a single `OpenAICompatibleProvider` that speaks OpenAI's `/v1/chat/completions` SSE protocol against any compatible endpoint (local Ollama on `/v1`, cloud DashScope compat endpoint, or any other). This is P2-1 Slice 1 — the provider-class unification groundwork for everything downstream.

**Architecture:**

- One provider class implementing the existing `LLMProvider` Protocol (`backend/providers/base.py`). Constructor takes `(base_url, api_key, model, temperature, timeout)`; `chat_stream()` streams OpenAI-format SSE tokens.
- Use `httpx` (already a dep) + hand-rolled SSE parser. **Do NOT add the `openai` SDK** — the wire format is trivial, and keeping the dep surface small matches the ollama_llm approach we're replacing.
- `ProviderProfile` (the JSON-backed registry) is the responsibility of S2 — S1 keeps wiring backwards-compatible by reading from `config.toml [llm]` with three new fields (`api_key`, `/v1` base URL), so `main.py` continues to construct a single provider instance and S2 can swap to profile-based construction without re-churning S1's work.

**Tech Stack:** Python 3.11+, httpx (async streaming + `MockTransport` for SSE tests), pytest, pytest-asyncio, structlog.

**Spec reference:** [`docs/superpowers/specs/2026-04-15-p2-1-design.md`](../specs/2026-04-15-p2-1-design.md) §3 (abstractions), §4.2 (profile shape), §6 (slice S1 row).

---

## File map

| Operation | Path | Responsibility |
|---|---|---|
| Create | `backend/providers/openai_compatible.py` | New provider class — OpenAI `/v1/chat/completions` SSE client |
| Create | `backend/tests/test_openai_compatible.py` | Dedicated unit tests: Protocol conformance, SSE parsing (mocked), health check, two integration tests (skippable) |
| Delete | `backend/providers/ollama_llm.py` | Replaced |
| Modify | `backend/config.py` | `LLMConfig.provider` default `"openai_compatible"`, add `api_key: str = "ollama"`, default `base_url` → `http://localhost:11434/v1` |
| Modify | `config.toml` | `[llm]` section: add `api_key = "ollama"`, update `base_url` to `http://localhost:11434/v1`, update `provider` to `"openai_compatible"` |
| Modify | `backend/main.py:37,52-57` | Replace `from providers.ollama_llm import OllamaLLM` + construction with `OpenAICompatibleProvider` |
| Modify | `backend/tests/test_providers.py:1-29` | Drop `OllamaLLM` test block; the dedicated test file above covers the replacement |
| Modify | `backend/tests/test_e2e_integration.py:40` | Update the comment `"Stand-in for OllamaLLM"` to `"Stand-in for OpenAICompatibleProvider"` (no behavior change; FakeLLM already matches Protocol) |

Untouched in this slice (handled in later slices): `ProviderProfile` dataclass, `PersonaRegistry`, `HybridRouter`, IPC commands, `billing_ledger`, SettingsPanel UI, credential manager.

---

## Protocol target (what `chat_stream` must produce)

OpenAI SSE wire format (one line per frame, blank lines separate frames; `data:` prefix; `[DONE]` sentinel):

```
data: {"id":"...","object":"chat.completion.chunk","choices":[{"delta":{"content":"Hello"},"index":0}]}

data: {"id":"...","object":"chat.completion.chunk","choices":[{"delta":{"content":" world"},"index":0}]}

data: [DONE]
```

For each frame: parse JSON, take `choices[0].delta.content` (may be absent on the first frame — skip), yield the string if non-empty. Stop on `data: [DONE]` or response EOF.

**Payload to send:**

```json
{
  "model": "<model>",
  "messages": [...],
  "stream": true,
  "temperature": 0.7,
  "max_tokens": 2048
}
```

**Headers:** `Authorization: Bearer <api_key>`, `Content-Type: application/json`. Ollama's `/v1` endpoint accepts any non-empty bearer token (convention: `"ollama"`).

**Health check:** `GET <base_url>/models` with the same bearer header. 200 = healthy. (This works on both Ollama `/v1/models` and DashScope `/v1/models` — confirmed by spec §3.)

---

## Tasks

### Task 1: Scaffold the provider class and confirm Protocol conformance

**Files:**

- Create: `backend/providers/openai_compatible.py`
- Create: `backend/tests/test_openai_compatible.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_openai_compatible.py` with:

```python
"""Unit tests for OpenAICompatibleProvider (P2-1-S1).

Covers:
- Protocol conformance (runtime-checkable LLMProvider)
- chat_stream against a mocked SSE transport (no network)
- health_check against mocked /models endpoints
- Two integration tests (skip if endpoints offline / no api key)
"""
from __future__ import annotations

import json
import os

import httpx
import pytest

from providers.base import LLMProvider
from providers.openai_compatible import OpenAICompatibleProvider


def test_openai_compatible_implements_protocol():
    provider = OpenAICompatibleProvider(
        base_url="http://localhost:11434/v1",
        api_key="ollama",
        model="gemma4:e4b",
    )
    assert isinstance(provider, LLMProvider)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_openai_compatible.py::test_openai_compatible_implements_protocol -v`

Expected: `ModuleNotFoundError: No module named 'providers.openai_compatible'`.

- [ ] **Step 3: Write minimal implementation**

Create `backend/providers/openai_compatible.py`:

```python
from __future__ import annotations

import json
from typing import AsyncIterator

import httpx
import structlog

logger = structlog.get_logger()


class OpenAICompatibleProvider:
    """LLM provider speaking OpenAI's /v1/chat/completions SSE protocol.

    Works against any compatible endpoint:
      - Local Ollama on /v1 (api_key "ollama", ignored server-side).
      - DashScope compatible-mode /v1 (real bearer token).
      - Any other OpenAI-compatible gateway.

    Implements the `LLMProvider` Protocol in providers/base.py.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        temperature: float = 0.7,
        timeout: float = 120.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.timeout = timeout

    async def chat_stream(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int = 2048,
    ) -> AsyncIterator[str]:
        raise NotImplementedError  # implemented in Task 3

    async def health_check(self) -> bool:
        raise NotImplementedError  # implemented in Task 2
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_openai_compatible.py::test_openai_compatible_implements_protocol -v`

Expected: PASS. The Protocol is `@runtime_checkable` and only inspects that `chat_stream` / `health_check` attributes exist as callables — they do, so `isinstance` check succeeds even though bodies raise.

- [ ] **Step 5: Commit**

```bash
git add backend/providers/openai_compatible.py backend/tests/test_openai_compatible.py
git commit -m "feat(providers): scaffold OpenAICompatibleProvider (P2-1-S1)"
```

---

### Task 2: Implement `health_check` against `/models` endpoint

**Files:**

- Modify: `backend/providers/openai_compatible.py`
- Modify: `backend/tests/test_openai_compatible.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_openai_compatible.py`:

```python
@pytest.mark.asyncio
async def test_health_check_returns_true_on_200():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/models")
        assert request.headers["authorization"] == "Bearer test-key"
        return httpx.Response(200, json={"data": [{"id": "any-model"}]})

    provider = OpenAICompatibleProvider(
        base_url="http://example.invalid/v1",
        api_key="test-key",
        model="x",
    )
    transport = httpx.MockTransport(handler)
    # Inject the mock transport via a provider hook (see impl below).
    provider._transport = transport
    assert await provider.health_check() is True


@pytest.mark.asyncio
async def test_health_check_returns_false_on_5xx():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    provider = OpenAICompatibleProvider(
        base_url="http://example.invalid/v1",
        api_key="test-key",
        model="x",
    )
    provider._transport = httpx.MockTransport(handler)
    assert await provider.health_check() is False


@pytest.mark.asyncio
async def test_health_check_returns_false_on_connect_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    provider = OpenAICompatibleProvider(
        base_url="http://example.invalid/v1",
        api_key="test-key",
        model="x",
    )
    provider._transport = httpx.MockTransport(handler)
    assert await provider.health_check() is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_openai_compatible.py -v -k health_check`

Expected: all three FAIL with `NotImplementedError`.

- [ ] **Step 3: Implement `health_check`**

Edit `backend/providers/openai_compatible.py`. Add a private `_transport` slot to the constructor and a helper `_client()` that uses it when set; fill in `health_check`:

Replace the `__init__` method:

```python
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        temperature: float = 0.7,
        timeout: float = 120.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.timeout = timeout
        # Test-only injection point; real code leaves this None.
        self._transport: httpx.BaseTransport | None = None

    def _client(self, timeout: float) -> httpx.AsyncClient:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        return httpx.AsyncClient(
            timeout=timeout,
            headers=headers,
            transport=self._transport,
        )
```

Replace the `health_check` body:

```python
    async def health_check(self) -> bool:
        try:
            async with self._client(timeout=5.0) as client:
                resp = await client.get(f"{self.base_url}/models")
                return resp.status_code == 200
        except Exception:
            return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_openai_compatible.py -v -k health_check`

Expected: all three PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/providers/openai_compatible.py backend/tests/test_openai_compatible.py
git commit -m "feat(providers): implement OpenAICompatibleProvider.health_check"
```

---

### Task 3: Implement `chat_stream` with SSE parsing

**Files:**

- Modify: `backend/providers/openai_compatible.py`
- Modify: `backend/tests/test_openai_compatible.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_openai_compatible.py`:

```python
def _sse(frames: list[dict | str]) -> bytes:
    """Serialize OpenAI-style SSE frames. A str entry is treated as raw data (e.g. '[DONE]')."""
    lines: list[str] = []
    for frame in frames:
        if isinstance(frame, str):
            lines.append(f"data: {frame}\n")
        else:
            lines.append(f"data: {json.dumps(frame)}\n")
        lines.append("\n")
    return "".join(lines).encode("utf-8")


def _delta(text: str) -> dict:
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion.chunk",
        "choices": [{"index": 0, "delta": {"content": text}}],
    }


@pytest.mark.asyncio
async def test_chat_stream_yields_tokens_in_order():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        captured["auth"] = request.headers.get("authorization")
        body = _sse([_delta("Hello"), _delta(" "), _delta("world"), "[DONE]"])
        return httpx.Response(
            200,
            content=body,
            headers={"content-type": "text/event-stream"},
        )

    provider = OpenAICompatibleProvider(
        base_url="http://example.invalid/v1",
        api_key="sk-test",
        model="qwen3.6-plus",
    )
    provider._transport = httpx.MockTransport(handler)

    tokens: list[str] = []
    async for tok in provider.chat_stream(
        [{"role": "user", "content": "hi"}],
        max_tokens=32,
    ):
        tokens.append(tok)

    assert tokens == ["Hello", " ", "world"]
    assert captured["url"] == "http://example.invalid/v1/chat/completions"
    assert captured["auth"] == "Bearer sk-test"
    assert captured["body"]["model"] == "qwen3.6-plus"
    assert captured["body"]["stream"] is True
    assert captured["body"]["max_tokens"] == 32
    assert captured["body"]["messages"] == [{"role": "user", "content": "hi"}]


@pytest.mark.asyncio
async def test_chat_stream_skips_empty_and_missing_content_deltas():
    """Role-only opening delta and empty content chunks must not emit tokens."""

    def handler(request: httpx.Request) -> httpx.Response:
        frames = [
            # First frame is role-only (no 'content') — OpenAI sends this.
            {
                "choices": [{"index": 0, "delta": {"role": "assistant"}}],
            },
            _delta(""),        # empty string — skip
            _delta("abc"),
            "[DONE]",
        ]
        return httpx.Response(
            200,
            content=_sse(frames),
            headers={"content-type": "text/event-stream"},
        )

    provider = OpenAICompatibleProvider(
        base_url="http://example.invalid/v1",
        api_key="k",
        model="m",
    )
    provider._transport = httpx.MockTransport(handler)

    tokens = [t async for t in provider.chat_stream([{"role": "user", "content": "x"}])]
    assert tokens == ["abc"]


@pytest.mark.asyncio
async def test_chat_stream_respects_explicit_temperature_override():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            content=_sse(["[DONE]"]),
            headers={"content-type": "text/event-stream"},
        )

    provider = OpenAICompatibleProvider(
        base_url="http://example.invalid/v1",
        api_key="k",
        model="m",
        temperature=0.7,
    )
    provider._transport = httpx.MockTransport(handler)
    async for _ in provider.chat_stream(
        [{"role": "user", "content": "x"}],
        temperature=0.2,
    ):
        pass
    assert captured["body"]["temperature"] == 0.2


@pytest.mark.asyncio
async def test_chat_stream_raises_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "bad key"}})

    provider = OpenAICompatibleProvider(
        base_url="http://example.invalid/v1",
        api_key="wrong",
        model="m",
    )
    provider._transport = httpx.MockTransport(handler)

    with pytest.raises(httpx.HTTPStatusError):
        async for _ in provider.chat_stream([{"role": "user", "content": "x"}]):
            pass
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_openai_compatible.py -v -k chat_stream`

Expected: all four FAIL with `NotImplementedError`.

- [ ] **Step 3: Implement `chat_stream`**

Edit `backend/providers/openai_compatible.py`. Replace the `chat_stream` body:

```python
    async def chat_stream(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int = 2048,
    ) -> AsyncIterator[str]:
        temp = temperature if temperature is not None else self.temperature
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "temperature": temp,
            "max_tokens": max_tokens,
        }
        async with self._client(timeout=self.timeout) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                json=payload,
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data_str = line[len("data:"):].strip()
                    if not data_str:
                        continue
                    if data_str == "[DONE]":
                        break
                    try:
                        data = json.loads(data_str)
                    except json.JSONDecodeError:
                        logger.warning(
                            "openai_compat_bad_sse_frame", raw=data_str
                        )
                        continue
                    choices = data.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    token = delta.get("content")
                    if token:
                        yield token
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_openai_compatible.py -v`

Expected: all tests from Tasks 1–3 PASS (≥ 7 passing).

- [ ] **Step 5: Commit**

```bash
git add backend/providers/openai_compatible.py backend/tests/test_openai_compatible.py
git commit -m "feat(providers): implement OpenAICompatibleProvider.chat_stream (SSE)"
```

---

### Task 4: Add live integration tests (skippable) for Ollama `/v1` and DashScope

**Files:**

- Modify: `backend/tests/test_openai_compatible.py`

- [ ] **Step 1: Append integration tests**

Add to the bottom of `backend/tests/test_openai_compatible.py`:

```python
# --------------------------------------------------------------------------
# Integration tests — skipped by default unless the endpoint is reachable.
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_integration_ollama_v1_roundtrip():
    """Hits local Ollama's OpenAI-compatible endpoint. Skipped if not running."""
    provider = OpenAICompatibleProvider(
        base_url="http://localhost:11434/v1",
        api_key="ollama",
        model=os.environ.get("DESKPET_OLLAMA_MODEL", "gemma4:e4b"),
    )
    if not await provider.health_check():
        pytest.skip("Ollama /v1 not reachable — start ollama or set DESKPET_OLLAMA_MODEL")

    tokens: list[str] = []
    async for tok in provider.chat_stream(
        [{"role": "user", "content": "Reply with the single word: ping"}],
        max_tokens=16,
    ):
        tokens.append(tok)
    joined = "".join(tokens).lower()
    assert "ping" in joined


@pytest.mark.asyncio
async def test_integration_dashscope_roundtrip():
    """Hits DashScope compat-mode endpoint. Skipped if DESKPET_DASHSCOPE_KEY unset."""
    api_key = os.environ.get("DESKPET_DASHSCOPE_KEY")
    if not api_key:
        pytest.skip("DESKPET_DASHSCOPE_KEY not set — skipping live cloud test")

    provider = OpenAICompatibleProvider(
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key=api_key,
        model=os.environ.get("DESKPET_DASHSCOPE_MODEL", "qwen3.6-plus"),
    )
    if not await provider.health_check():
        pytest.skip("DashScope /models 非 200 — 可能是密钥无效或网络问题")

    tokens: list[str] = []
    async for tok in provider.chat_stream(
        [{"role": "user", "content": "请用一个字回答：好"}],
        max_tokens=8,
    ):
        tokens.append(tok)
    assert len("".join(tokens)) >= 1
```

- [ ] **Step 2: Run the full file**

Run: `cd backend && python -m pytest tests/test_openai_compatible.py -v`

Expected: Unit tests PASS; the two integration tests report SKIPPED unless the machine has Ollama/DashScope configured. On the maintainer's box where Ollama is running, `test_integration_ollama_v1_roundtrip` should PASS (model `gemma4:e4b` present per `config.toml`).

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_openai_compatible.py
git commit -m "test(providers): integration tests for Ollama /v1 + DashScope compat endpoints"
```

---

### Task 5: Update `LLMConfig` schema + `config.toml`

**Files:**

- Modify: `backend/config.py:13-18`
- Modify: `config.toml:8-13`

- [ ] **Step 1: Update `LLMConfig`**

Edit `backend/config.py`. Replace the `LLMConfig` dataclass:

```python
@dataclass
class LLMConfig:
    provider: str = "openai_compatible"
    model: str = "gemma4:e4b"
    base_url: str = "http://localhost:11434/v1"
    api_key: str = "ollama"
    temperature: float = 0.7
    max_tokens: int = 2048
```

- [ ] **Step 2: Update `config.toml`**

Edit `config.toml`. Replace the `[llm]` section with:

```toml
[llm]
provider = "openai_compatible"
model = "gemma4:e4b"
base_url = "http://localhost:11434/v1"
api_key = "ollama"
temperature = 0.7
max_tokens = 2048
```

- [ ] **Step 3: Verify config parses**

Run: `cd backend && python -c "from config import load_config; c = load_config('../config.toml'); print(c.llm)"`

Expected output (exact): `LLMConfig(provider='openai_compatible', model='gemma4:e4b', base_url='http://localhost:11434/v1', api_key='ollama', temperature=0.7, max_tokens=2048)`

- [ ] **Step 4: Commit**

```bash
git add backend/config.py config.toml
git commit -m "feat(config): LLMConfig gains api_key field; base_url points at /v1"
```

---

### Task 6: Wire `main.py` to `OpenAICompatibleProvider` and delete `ollama_llm.py`

**Files:**

- Modify: `backend/main.py:37,52-57`
- Delete: `backend/providers/ollama_llm.py`
- Modify: `backend/tests/test_providers.py:1-29`
- Modify: `backend/tests/test_e2e_integration.py:40` (comment only)

- [ ] **Step 1: Edit `main.py` import + construction**

Edit `backend/main.py`. Replace line 37:

```python
from providers.ollama_llm import OllamaLLM
```

with:

```python
from providers.openai_compatible import OpenAICompatibleProvider
```

Then replace lines 52–57:

```python
ollama_llm = OllamaLLM(
    model=config.llm.model,
    base_url=config.llm.base_url,
    temperature=config.llm.temperature,
)
service_context.register("llm_engine", ollama_llm)
```

with:

```python
llm = OpenAICompatibleProvider(
    base_url=config.llm.base_url,
    api_key=config.llm.api_key,
    model=config.llm.model,
    temperature=config.llm.temperature,
)
service_context.register("llm_engine", llm)
```

Then line 87 currently reads:

```python
base_agent = SimpleLLMAgent(ollama_llm, memory=memory_store)
```

Replace with:

```python
base_agent = SimpleLLMAgent(llm, memory=memory_store)
```

- [ ] **Step 2: Delete the old provider file**

Run: `git rm backend/providers/ollama_llm.py`

Expected: `rm 'backend/providers/ollama_llm.py'`. No other Python file should still import from it after Steps 1 and 3.

- [ ] **Step 3: Remove dead Ollama tests from `test_providers.py`**

Edit `backend/tests/test_providers.py`. Delete lines 1–29 (the three Ollama-specific tests + their imports). The file's new top should begin with:

```python
import pytest

from providers.faster_whisper_asr import FasterWhisperASR
from providers.base import ASRProvider


def test_faster_whisper_implements_protocol():
    asr = FasterWhisperASR(model="large-v3-turbo")
    assert isinstance(asr, ASRProvider)
```

(The OpenAI-compatible coverage lives in the dedicated `test_openai_compatible.py` from Tasks 1–4.)

- [ ] **Step 4: Update the `FakeLLM` docstring comment in `test_e2e_integration.py`**

Edit `backend/tests/test_e2e_integration.py:40`. Replace:

```python
    """Stand-in for OllamaLLM that streams a deterministic reply."""
```

with:

```python
    """Stand-in for OpenAICompatibleProvider that streams a deterministic reply."""
```

(The class body stays identical — `FakeLLM` already matches the `LLMProvider` Protocol by duck-typing.)

- [ ] **Step 5: Verify nothing still references `OllamaLLM` or `ollama_llm`**

Run: `cd backend && python -m pytest --collect-only -q 2>&1 | tail -5`

Expected: collection succeeds with no import errors.

Run (search for stragglers): `cd .. && grep -R --include='*.py' -n 'OllamaLLM\|ollama_llm' backend 2>/dev/null || true`

Expected: empty output (zero matches). If any match appears, fix the reference before moving on.

- [ ] **Step 6: Commit**

```bash
git add backend/main.py backend/providers/ollama_llm.py backend/tests/test_providers.py backend/tests/test_e2e_integration.py
git commit -m "refactor(providers): replace OllamaLLM with OpenAICompatibleProvider

- main.py constructs OpenAICompatibleProvider from config.toml [llm]
- ollama_llm.py removed; openai_compatible.py covers both endpoints
- test_providers.py trims dead Ollama tests (moved to test_openai_compatible.py)"
```

---

### Task 7: Full-suite smoke + live Ollama sanity check

**Files:** none changed.

- [ ] **Step 1: Run the full backend test suite**

Run: `cd backend && python -m pytest -v 2>&1 | tail -40`

Expected: all previously-green tests still PASS. New `test_openai_compatible.py` tests PASS; integration tests either PASS (if Ollama up) or SKIPPED (if not). Zero failures, zero errors.

- [ ] **Step 2: Live smoke: boot the backend against Ollama `/v1` and get one streamed reply**

Start the backend (in one shell):

```bash
cd backend && python -m uvicorn main:app --host 127.0.0.1 --port 8100 --log-level info
```

Wait for `"startup complete"` in stderr. In a second shell, run a one-shot probe using the already-installed httpx:

```bash
cd backend && python -c "
import asyncio, httpx, json
async def main():
    async with httpx.AsyncClient(timeout=30.0) as c:
        async with c.stream('POST',
            'http://localhost:11434/v1/chat/completions',
            headers={'Authorization': 'Bearer ollama', 'Content-Type': 'application/json'},
            json={'model': 'gemma4:e4b', 'stream': True,
                  'messages': [{'role':'user','content':'Reply with: pong'}],
                  'max_tokens': 8}) as r:
            r.raise_for_status()
            async for line in r.aiter_lines():
                if line.startswith('data: ') and line != 'data: [DONE]':
                    d = json.loads(line[6:])
                    tok = (d.get('choices',[{}])[0].get('delta',{}) or {}).get('content','')
                    if tok: print(tok, end='', flush=True)
            print()
asyncio.run(main())
"
```

Expected: at least one non-empty token stream containing "pong" (case-insensitive). This confirms Ollama `/v1` is up and the SSE frames the provider expects are actually emitted.

Stop the backend with Ctrl+C. On Windows also run `taskkill /f /im uvicorn.exe 2>nul` if the process sticks.

- [ ] **Step 3: Final commit (empty — checkpoint)**

No files changed; if there are no stray edits, skip commit. If anything surfaced from smoke that required a fix, apply it and commit with message `fix(providers): <details>`.

---

### Task 8: Close the slice — update STATE.md index

**Files:**

- Modify: `docs/superpowers/STATE.md`

- [ ] **Step 1: Add an S1 row to the active slice table**

Edit `docs/superpowers/STATE.md`. In the section listing P2-0 slices (table titled "Completed P2-0 slices (quick index)"), append a new table below it — **or** insert a new "Active P2-1 slices" section just above "Pending follow-ups" — with:

```markdown
## Active P2-1 slices

| Slice | Status | Theme |
|-------|--------|-------|
| S1 | ✅ merged | `OpenAICompatibleProvider` replaces `OllamaLLM`; unit + integration tests |
```

Also bump the "Last updated:" line at the top of the file to today's date with the note `(+ P2-1-S1)`.

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/STATE.md
git commit -m "docs(state): record P2-1-S1 completion (OpenAICompatibleProvider)"
```

---

## Self-review

**1. Spec coverage.** S1 row of spec §6 demands: new `openai_compatible.py`, delete `ollama_llm.py`, all ServiceContext construction points updated, unit tests covering both Ollama-compat and DashScope endpoints. ✅ Mapped: Tasks 1–3 = new class, Task 6 = delete + main.py wiring, Tasks 3–4 = both endpoints covered (mocked for hermetic runs + live integration opt-ins). The S2 `ProviderProfile` dataclass is explicitly deferred per the slice boundary.

**2. Placeholder scan.** No `TBD`/`TODO`/`implement later`. Every code step has the exact code. Every run step has the exact command + expected output. No "add appropriate error handling" — the `try/except` in `health_check`, the `raise_for_status()` in `chat_stream`, and the JSON-decode-warning fallback are explicit.

**3. Type consistency.** The provider constructor signature `(base_url, api_key, model, temperature=0.7, timeout=120.0)` is used identically in Tasks 1, 2, 3, 4, and 6. `chat_stream` keyword args `(temperature, max_tokens)` match the `LLMProvider` Protocol in `backend/providers/base.py:6`. `_transport` is introduced in Task 1 (stub), wired in Task 2 (`_client`), and relied on by all mocked tests in Tasks 2–3. `LLMConfig` gains `api_key: str` in Task 5 and is consumed verbatim in Task 6.

No issues found.

---

**Signed-off:** 2026-04-15, pending execution.
