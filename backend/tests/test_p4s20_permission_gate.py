"""P4-S20 Wave 0b: PermissionGate TDD tests.

Covers spec `permission-gate`:
  - Always-pass category (default-allow for read_file)
  - Prompt + IPC for first occurrence
  - User "No" returns deny
  - Session "Yes-always" cache
  - 60s timeout → auto-deny with source="timeout"
  - Sensitive-path upgrade (read_file → read_file_sensitive)
  - Categories enum exhaustive
  - Deny patterns from config (config-deny)
  - User cannot override config-deny
"""
from __future__ import annotations

import asyncio
import os
import pytest

from deskpet.permissions.gate import (
    PermissionGate,
    PermissionGateConfig,
)
from deskpet.types.skill_platform import (
    PermissionDecision,
    PermissionResponse,
)


@pytest.fixture
def gate() -> PermissionGate:
    """Minimal gate with no IPC sender — tests inject responses directly."""
    return PermissionGate(
        config=PermissionGateConfig(
            timeout_s=0.5,  # tests run fast
            shell_deny_patterns=["rm -rf /", "format c:"],
        )
    )


@pytest.mark.asyncio
async def test_always_pass_read_file_default(gate: PermissionGate) -> None:
    """read_file with normal path returns default-allow without IPC."""
    decision = await gate.check(
        category="read_file",
        params={"path": "C:/tmp/note.txt"},
        session_id="s1",
    )
    assert decision.allow is True
    assert decision.source == "default-allow"


@pytest.mark.asyncio
async def test_sensitive_read_upgraded(gate: PermissionGate) -> None:
    """read_file with .ssh/id_rsa upgrades to read_file_sensitive (prompts)."""
    # No responder registered → request times out → auto-deny
    decision = await gate.check(
        category="read_file",
        params={"path": "C:/Users/me/.ssh/id_rsa"},
        session_id="s1",
    )
    assert decision.allow is False
    assert decision.source == "timeout"


@pytest.mark.asyncio
async def test_user_allows_one_time(gate: PermissionGate) -> None:
    """User responds allow → decision is allow, NOT cached."""

    async def responder(req):
        return PermissionResponse(request_id=req.request_id, decision="allow")

    gate.set_responder(responder)
    d1 = await gate.check("write_file", {"path": "a.txt"}, "s1")
    assert d1.allow is True
    assert d1.source == "user-allowed"

    # Second call still prompts (one-time, no cache)
    calls = {"n": 0}

    async def counting_responder(req):
        calls["n"] += 1
        return PermissionResponse(request_id=req.request_id, decision="allow")

    gate.set_responder(counting_responder)
    await gate.check("write_file", {"path": "b.txt"}, "s1")
    await gate.check("write_file", {"path": "c.txt"}, "s1")
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_user_deny(gate: PermissionGate) -> None:
    async def responder(req):
        return PermissionResponse(request_id=req.request_id, decision="deny")

    gate.set_responder(responder)
    d = await gate.check("write_file", {"path": "x.txt"}, "s1")
    assert d.allow is False
    assert d.source == "user-denied"


@pytest.mark.asyncio
async def test_allow_session_cached(gate: PermissionGate) -> None:
    calls = {"n": 0}

    async def responder(req):
        calls["n"] += 1
        return PermissionResponse(
            request_id=req.request_id, decision="allow_session"
        )

    gate.set_responder(responder)
    await gate.check("shell", {"command": "git status"}, "sA")
    await gate.check("shell", {"command": "git log"}, "sA")
    await gate.check("shell", {"command": "git diff"}, "sA")
    # Only the first call prompts; rest are cache-hits
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_session_cache_isolated(gate: PermissionGate) -> None:
    """A different session must prompt again."""
    calls = {"n": 0}

    async def responder(req):
        calls["n"] += 1
        return PermissionResponse(
            request_id=req.request_id, decision="allow_session"
        )

    gate.set_responder(responder)
    await gate.check("shell", {"command": "echo a"}, "sA")
    await gate.check("shell", {"command": "echo b"}, "sB")  # different session
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_timeout_auto_denies(gate: PermissionGate) -> None:
    # No responder set → asyncio.wait_for times out per gate.config.timeout_s
    decision = await gate.check("write_file", {"path": "y.txt"}, "s1")
    assert decision.allow is False
    assert decision.source == "timeout"


@pytest.mark.asyncio
async def test_config_deny_takes_precedence(gate: PermissionGate) -> None:
    """rm -rf / pattern is rejected without prompting, even if responder allows."""
    calls = {"n": 0}

    async def responder(req):
        calls["n"] += 1
        return PermissionResponse(request_id=req.request_id, decision="allow")

    gate.set_responder(responder)
    d = await gate.check("shell", {"command": "rm -rf /"}, "s1")
    assert d.allow is False
    assert d.source == "config-deny"
    assert d.pattern == "rm -rf /"
    assert calls["n"] == 0  # responder NEVER called


@pytest.mark.asyncio
async def test_config_deny_overrides_session_cache(
    gate: PermissionGate,
) -> None:
    """Even if user said 'allow_session' once, deny pattern still rejects."""

    async def responder(req):
        return PermissionResponse(
            request_id=req.request_id, decision="allow_session"
        )

    gate.set_responder(responder)
    # Cache an allow for shell
    d1 = await gate.check("shell", {"command": "echo hi"}, "s1")
    assert d1.allow is True
    # Now try a denied pattern in same session — must be denied
    d2 = await gate.check("shell", {"command": "rm -rf /"}, "s1")
    assert d2.allow is False
    assert d2.source == "config-deny"


@pytest.mark.asyncio
async def test_unknown_category_raises(gate: PermissionGate) -> None:
    with pytest.raises(ValueError, match="unknown permission category"):
        await gate.check("hax", {}, "s1")  # type: ignore[arg-type]
