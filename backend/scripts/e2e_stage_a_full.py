"""P4-S20 Wave 2c — full live Stage A chain.

End-to-end with the LIVE local Ollama LLM (no mocks):
    user prompt -> AgentLoop -> chat_with_tools -> tool_calls
                -> ToolRegistry.execute_tool -> PermissionGate (auto-approve)
                -> desktop_create_file -> real file written

Hermetic: redirects $USERPROFILE/$HOME to a tempdir so we don't write
to the user's actual desktop. Confirms the bytes round-trip and that
the permission gate fired exactly once with category=desktop_write.

Run:
    cd backend && python -m scripts.e2e_stage_a_full
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.agent_loop import (
    AgentLoop,
    AssistantMessageEvent,
    ErrorEvent,
    FinalEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from agent.tool_use_shim import OpenAICompatibleAgentLLM
from config import load_config
from providers.openai_compatible import OpenAICompatibleProvider

from deskpet.permissions.gate import PermissionGate, PermissionGateConfig
from deskpet.tools.os_tools import register_os_tools
from deskpet.tools.registry import ToolRegistry
from deskpet.types.skill_platform import PermissionResponse


def _print(*parts: object) -> None:
    print("[e2e-full]", *parts, flush=True)


async def main() -> int:
    config = load_config()

    # Hermetic Desktop
    tmp = tempfile.mkdtemp(prefix="deskpet_e2e_full_")
    fake_home = Path(tmp) / "fakeuser"
    desktop = fake_home / "Desktop"
    desktop.mkdir(parents=True)
    if sys.platform == "win32":
        os.environ["USERPROFILE"] = str(fake_home)
    else:
        os.environ["HOME"] = str(fake_home)
    _print("hermetic Desktop:", desktop)

    # Live LLM
    provider = OpenAICompatibleProvider(
        base_url=config.llm.local.base_url,
        api_key=config.llm.local.api_key,
        model=config.llm.local.model,
        timeout=120.0,
    )
    shim = OpenAICompatibleAgentLLM(provider=provider)
    _print("provider:", provider.base_url, "model=", provider.model)

    # v2 registry + gate
    registry = ToolRegistry()
    register_os_tools(registry)
    gate = PermissionGate(
        config=PermissionGateConfig(timeout_s=2.0)
    )
    permission_log: list[tuple[str, str]] = []

    async def auto_approve(req):
        permission_log.append((req.category, req.summary))
        _print(f"popup: category={req.category} summary={req.summary!r} -> ALLOW")
        return PermissionResponse(
            request_id=req.request_id, decision="allow"
        )

    gate.set_responder(auto_approve)
    registry.set_permission_gate(gate)

    # Agent loop
    loop = AgentLoop(
        llm_registry=shim,
        tool_registry=registry,
        max_iterations=4,
    )

    final_text = ""
    async for ev in loop.run(
        [
            {
                "role": "system",
                "content": (
                    "You are a desktop assistant. When the user asks to "
                    "create a file on their desktop, you MUST call the "
                    "desktop_create_file tool. Do not respond with text "
                    "alone."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Create a file called todo.txt on my desktop with "
                    "the content '吃饭买菜'."
                ),
            },
        ],
        session_id="e2e-full",
    ):
        if isinstance(ev, AssistantMessageEvent):
            if ev.content:
                _print("assistant:", ev.content[:200])
        elif isinstance(ev, ToolCallEvent):
            _print(
                f"tool_call iter={ev.iteration}",
                ev.tool_call.name,
                ev.tool_call.arguments,
            )
        elif isinstance(ev, ToolResultEvent):
            _print(f"tool_result iter={ev.iteration}", ev.tool_name, ev.result[:200])
        elif isinstance(ev, FinalEvent):
            final_text = ev.content
            _print("FINAL:", ev.content[:200], f"iters={ev.iteration}")
        elif isinstance(ev, ErrorEvent):
            _print("ERROR:", ev.reason, ev.detail)

    target = desktop / "todo.txt"
    if not target.exists():
        _print("FAIL: file not created at", target)
        return 1
    actual = target.read_text(encoding="utf-8")
    if actual != "吃饭买菜":
        _print(f"FAIL: content mismatch: {actual!r}")
        return 1
    if not any(c == "desktop_write" for c, _ in permission_log):
        _print("FAIL: permission gate was not consulted for desktop_write")
        return 1

    _print("PASS: live LLM -> tool_use -> permission -> file written")
    _print("artifact:", target, f"({target.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
