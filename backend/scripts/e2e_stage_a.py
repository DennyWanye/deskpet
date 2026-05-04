"""P4-S20 Wave 2a — Stage A end-to-end smoke.

Validates the full tool_use loop path WITHOUT a live LLM:

    user request → AgentLoop → ToolRegistry.execute_tool
                              → PermissionGate.check (responder approves)
                              → desktop_create_file handler
                              → real file written to a fake $USERPROFILE/Desktop

If this script exits 0, the integration plumbing is correct and the
only thing standing between us and the real "create todo.txt with
吃饭买菜" demo is wiring AgentLoop into the chat WS handler (Wave 2b)
+ a real LLM that emits tool_calls.

Per MEMORY.md ``feedback_real_test.md``: this is a backend smoke, not
a substitute for the UI screenshot evidence in Wave 2c.

Run:
    cd backend && python -m scripts.e2e_stage_a
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

# Make backend/ importable when run as a script
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.agent_loop import AgentLoop, FinalEvent, ToolCallEvent, ToolResultEvent
from llm.types import ChatResponse, ChatUsage, ToolCall

from deskpet.tools.registry import ToolRegistry
from deskpet.tools.os_tools import register_os_tools
from deskpet.permissions.gate import PermissionGate, PermissionGateConfig
from deskpet.types.skill_platform import PermissionResponse


class _ScriptedLLM:
    """Replay a fixed list of ChatResponses; no network calls."""

    def __init__(self, responses: list[ChatResponse]) -> None:
        self._responses = list(responses)

    async def chat_with_fallback(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        **kwargs: Any,
    ) -> ChatResponse:
        if not self._responses:
            raise RuntimeError("scripted LLM exhausted")
        return self._responses.pop(0)


def _print(label: str, *parts: object) -> None:
    print(f"[e2e-stage-a] {label}", *parts, flush=True)


async def main() -> int:
    # Hermetic: fake $USERPROFILE so this runs without touching the
    # real desktop, but still uses the same desktop_create_file code
    # path that the real demo will use.
    tmp = tempfile.mkdtemp(prefix="deskpet_e2e_stage_a_")
    fake_home = Path(tmp) / "fakeuser"
    desktop = fake_home / "Desktop"
    desktop.mkdir(parents=True)
    if sys.platform == "win32":
        os.environ["USERPROFILE"] = str(fake_home)
    else:
        os.environ["HOME"] = str(fake_home)
    _print("hermetic Desktop:", str(desktop))

    # --- arrange: registry, gate, scripted LLM, agent loop ----------
    registry = ToolRegistry()
    register_os_tools(registry)
    _print("registered tools:", registry.list_tools())

    gate = PermissionGate(
        config=PermissionGateConfig(
            timeout_s=2.0,
            shell_deny_patterns=["rm -rf /", "format c:"],
        )
    )
    permission_log: list[tuple[str, str]] = []

    async def auto_approve_responder(req):
        permission_log.append((req.category, req.summary))
        _print(
            "popup -> auto-approve",
            f"category={req.category}",
            f"summary={req.summary!r}",
        )
        return PermissionResponse(
            request_id=req.request_id, decision="allow"
        )

    gate.set_responder(auto_approve_responder)
    registry.set_permission_gate(gate)

    desired_args = {"name": "todo.txt", "content": "吃饭买菜"}
    llm = _ScriptedLLM(
        [
            # Turn 1 — model asks to call desktop_create_file
            ChatResponse(
                content="OK, creating the file…",
                tool_calls=[
                    ToolCall(
                        id="call_1",
                        name="desktop_create_file",
                        arguments=desired_args,
                    )
                ],
                stop_reason="tool_use",
                usage=ChatUsage(input_tokens=20, output_tokens=12),
            ),
            # Turn 2 — model wraps up
            ChatResponse(
                content="Done — your todo file is on the desktop.",
                tool_calls=[],
                stop_reason="end_turn",
                usage=ChatUsage(input_tokens=10, output_tokens=10),
            ),
        ]
    )

    loop = AgentLoop(llm_registry=llm, tool_registry=registry, max_iterations=5)

    # --- act: drive the loop --------------------------------------
    events = []
    async for ev in loop.run(
        [
            {
                "role": "user",
                "content": "create todo.txt on my desktop with content 吃饭买菜",
            }
        ],
        session_id="e2e",
    ):
        events.append(ev)
        if isinstance(ev, ToolCallEvent):
            _print("tool_call", ev.tool_call.name, ev.tool_call.arguments)
        elif isinstance(ev, ToolResultEvent):
            _print("tool_result", ev.tool_name, ev.result[:200])
        elif isinstance(ev, FinalEvent):
            _print("final", ev.content)

    # --- assert: file exists on fake desktop ----------------------
    target = desktop / "todo.txt"
    assert target.exists(), f"{target} not created"
    actual = target.read_text(encoding="utf-8")
    assert actual == "吃饭买菜", f"content mismatch: {actual!r}"
    assert any(c == "desktop_write" for c, _ in permission_log), (
        "permission gate was not consulted for desktop_write"
    )

    _print("PASS — file exists with correct UTF-8 content; permission gate fired")
    _print("artifact:", target, f"({target.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
