"""Tests for S6 security primitives: sensitive-info redaction + tool confirmation."""
from __future__ import annotations

from typing import AsyncIterator

import pytest

from agent.providers.tool_using import ToolUsingAgent
from memory.sensitive_filter import RedactingMemoryStore, redact
from tools.base import ToolSpec
from tools.registry import ToolRegistry


# --- redact() ---


@pytest.mark.parametrize(
    "raw, expected_marker",
    [
        ("my key is sk-abc123def456ghi789jkl012mno", "[REDACTED:API_KEY]"),
        ("auth: sk-ant-api03-0123456789abcdef0123456789abcdef012345", "[REDACTED:ANTHROPIC_KEY]"),
        ("ghp_abcd1234efgh5678ijkl9012mnop3456qrst", "[REDACTED:GITHUB_TOKEN]"),
        ("AKIAIOSFODNN7EXAMPLE", "[REDACTED:AWS_KEY]"),
        ("contact me at alice@example.com please", "[REDACTED:EMAIL]"),
        ("phone: 13912345678", "[REDACTED:PHONE_CN]"),
        ("password: hunter2", "[REDACTED:CREDENTIAL]"),
        ("API_KEY=s3cr3tv4lu3", "[REDACTED:CREDENTIAL]"),
        ("4111 1111 1111 1111", "[REDACTED:CREDIT_CARD]"),
        (
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NSJ9.abc123xyz",
            "[REDACTED:JWT]",
        ),
    ],
)
def test_redact_replaces_sensitive_spans(raw: str, expected_marker: str):
    out = redact(raw)
    assert expected_marker in out
    # Original secret substring should no longer appear verbatim (except for
    # well-known placeholders that don't match our patterns).
    assert out != raw


def test_redact_leaves_innocent_text_alone():
    benign = "Hello, how are you today? The weather is nice."
    assert redact(benign) == benign


def test_redact_handles_empty_input():
    assert redact("") == ""


# --- RedactingMemoryStore ---


class _FakeStore:
    def __init__(self):
        self.appended: list[tuple[str, str, str]] = []

    async def get_recent(self, session_id, limit=10):
        return []

    async def append(self, session_id, role, content):
        self.appended.append((session_id, role, content))

    async def clear(self, session_id):
        self.appended = [
            t for t in self.appended if t[0] != session_id
        ]


@pytest.mark.asyncio
async def test_redacting_store_masks_on_append():
    inner = _FakeStore()
    store = RedactingMemoryStore(inner)

    await store.append("s1", "user", "my key is sk-abc123def456ghi789jkl012mno ok?")

    assert len(inner.appended) == 1
    _, _, stored = inner.appended[0]
    assert "[REDACTED:API_KEY]" in stored
    assert "sk-abc123def456ghi789jkl012mno" not in stored


@pytest.mark.asyncio
async def test_redacting_store_passes_reads_through():
    inner = _FakeStore()
    store = RedactingMemoryStore(inner)

    # Even though inner has nothing, the call should be forwarded.
    result = await store.get_recent("s1")
    assert result == []


# --- Tool confirmation ---


class _StubAgent:
    """Yields a reply that invokes a single tool."""

    def __init__(self, text: str):
        self._text = text

    async def chat_stream(
        self, messages, *, session_id="default"
    ) -> AsyncIterator[str]:
        yield self._text


class _StubTool:
    def __init__(self, spec: ToolSpec, result: str = "ok"):
        self.spec = spec
        self.result = result
        self.invoked = False

    async def invoke(self, **kwargs):
        self.invoked = True
        return self.result


@pytest.mark.asyncio
async def test_high_risk_tool_denied_by_default():
    """Fail-closed: requires_confirmation=True + no callback → refused."""
    registry = ToolRegistry()
    tool = _StubTool(ToolSpec(
        name="delete_file",
        description="removes a file",
        requires_confirmation=True,
    ))
    registry.register(tool)

    agent = ToolUsingAgent(_StubAgent("please run <tool>delete_file</tool>"), registry)
    chunks = [c async for c in agent.chat_stream([{"role": "user", "content": "go"}])]
    out = "".join(chunks)

    assert "[tool refused:" in out
    assert not tool.invoked


@pytest.mark.asyncio
async def test_high_risk_tool_runs_when_confirm_approves():
    registry = ToolRegistry()
    tool = _StubTool(ToolSpec(
        name="open_url",
        description="opens a URL",
        requires_confirmation=True,
    ))
    registry.register(tool)

    async def approve(name, session_id):  # noqa: ARG001
        return True

    agent = ToolUsingAgent(
        _StubAgent("<tool>open_url</tool>"),
        registry,
        confirm=approve,
    )
    out = "".join([c async for c in agent.chat_stream([{"role": "user", "content": "go"}])])

    assert "[tool:open_url] ok" in out
    assert tool.invoked


@pytest.mark.asyncio
async def test_low_risk_tool_bypasses_confirmation():
    """requires_confirmation=False (default) must NOT consult the callback."""
    registry = ToolRegistry()
    tool = _StubTool(ToolSpec(name="get_time", description="returns time"))
    registry.register(tool)

    called = {"n": 0}

    async def track(name, session_id):  # noqa: ARG001
        called["n"] += 1
        return True

    agent = ToolUsingAgent(
        _StubAgent("<tool>get_time</tool>"),
        registry,
        confirm=track,
    )
    out = "".join([c async for c in agent.chat_stream([{"role": "user", "content": "go"}])])

    assert tool.invoked
    assert "[tool:get_time] ok" in out
    assert called["n"] == 0  # callback untouched for low-risk


@pytest.mark.asyncio
async def test_sync_confirm_callback_also_works():
    registry = ToolRegistry()
    tool = _StubTool(ToolSpec(
        name="reboot",
        description="reboots the machine",
        requires_confirmation=True,
    ))
    registry.register(tool)

    agent = ToolUsingAgent(
        _StubAgent("<tool>reboot</tool>"),
        registry,
        confirm=lambda name, sid: True,  # sync
    )
    out = "".join([c async for c in agent.chat_stream([{"role": "user", "content": "go"}])])
    assert tool.invoked
    assert "[tool:reboot] ok" in out
