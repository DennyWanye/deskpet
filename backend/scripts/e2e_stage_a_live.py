"""P4-S20 Wave 2c — live LLM probe.

Calls ``chat_with_tools`` against the configured local + cloud
providers WITHOUT going through the WS, so we can quickly tell
whether each one actually emits OpenAI ``tool_calls`` (which the
v2 agent loop needs).

Usage:
    cd backend && python -m scripts.e2e_stage_a_live

Output:
    - For each provider, prints whether it supports tool_calls.
    - If yes, runs one round-trip with desktop_create_file tools
      schema and prints the parsed tool_call. NO file is actually
      written — this script never invokes the handler.
    - If no, prints the raw text response so we can see what the
      model returned (often regex-style fallback like
      `<tool>desktop_create_file</tool>`).

This is the diagnostic the user can run while doing the chat-panel
real-test in the Tauri shell. The real Tauri-side smoke is documented
in docs/EVIDENCE/skill-platform-v1.md (Wave 6).
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import load_config, resolve_cloud_api_key  # type: ignore

config = load_config()
from providers.openai_compatible import OpenAICompatibleProvider


async def _probe(label: str, provider: OpenAICompatibleProvider) -> None:
    print(f"\n[probe] {label}: {provider.base_url}  model={provider.model}")
    tools = [
        {
            "type": "function",
            "function": {
                "name": "desktop_create_file",
                "description": "Create a file on the user's Desktop",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["name", "content"],
                },
            },
        }
    ]
    try:
        out = await provider.chat_with_tools(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a desktop assistant. When the user asks "
                        "to create a file on their desktop, you MUST call "
                        "the desktop_create_file tool with name and content "
                        "arguments. Do not respond with text alone."
                    ),
                },
                {
                    "role": "user",
                    "content": "Create a file called todo.txt on my desktop with the content 吃饭买菜",
                },
            ],
            tools=tools,
            max_tokens=512,
            temperature=0.0,
        )
    except Exception as exc:
        print(f"  ERROR: {type(exc).__name__}: {exc}")
        return

    print(f"  stop_reason={out['stop_reason']}")
    print(f"  content={out['content']!r}")
    print(f"  tool_calls={json.dumps(out['tool_calls'], ensure_ascii=False)}")
    if out["tool_calls"]:
        print("  PASS provider supports OpenAI tool_calls -- chat_v2 will work")
    else:
        print(
            "  WARN provider returned text only -- chat_v2 will NOT trigger "
            "the OS tool. This provider needs the legacy chat path "
            "(regex tool detection)."
        )


async def main() -> int:
    # Local
    local = OpenAICompatibleProvider(
        base_url=config.llm.local.base_url,
        api_key=config.llm.local.api_key,
        model=config.llm.local.model,
        timeout=60.0,
    )
    await _probe("LOCAL", local)

    # Cloud (only if api key resolves)
    cloud_key = resolve_cloud_api_key()
    if cloud_key:
        cloud = OpenAICompatibleProvider(
            base_url=config.llm.cloud.base_url,
            api_key=cloud_key,
            model=config.llm.cloud.model,
            timeout=60.0,
        )
        await _probe("CLOUD", cloud)
    else:
        print("\n[probe] CLOUD skipped — no DESKPET_CLOUD_API_KEY")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
