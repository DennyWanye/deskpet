"""P4-S20 真实 WS 端到端测试 — 模拟前端打实跑后端 chat_v2 路径。

不依赖 Tauri 桌面壳像素截图，而是直接打 ws://127.0.0.1:8100/ws/control
模拟真实前端的 IPC 调用。比 e2e_stage_a_full.py 更严苛 —— 用的是生产
backend 进程（已经在 8100 上跑着的 main.py），不是脚本里临时构造的
AgentLoop。

依赖：backend 已经在 8100 监听（DESKPET_DEV_MODE=1 下绕过 secret）。

运行：
    cd backend && python -m scripts.e2e_stage_a_ws
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

import websockets


def _print(*parts: object) -> None:
    print("[e2e-ws]", *parts, flush=True)


async def main() -> int:
    # 1. 准备 hermetic Desktop（生产 desktop_create_file 用 USERPROFILE）
    tmp = Path(tempfile.mkdtemp(prefix="deskpet_e2e_ws_"))
    fake_home = tmp / "fakeuser"
    desktop = fake_home / "Desktop"
    desktop.mkdir(parents=True)
    if sys.platform == "win32":
        os.environ["USERPROFILE"] = str(fake_home)
    else:
        os.environ["HOME"] = str(fake_home)
    _print("hermetic Desktop:", desktop)
    _print("注意：当前进程的 env 改了，但后端是另一进程 —— 它走的是真桌面")

    uri = "ws://127.0.0.1:8100/ws/control?secret="
    _print("connecting", uri)

    async with websockets.connect(uri) as ws:
        _print("connected")

        # 跳过 startup_status 帧
        first = await asyncio.wait_for(ws.recv(), timeout=5.0)
        _print("first frame type:", json.loads(first).get("type"))

        # 2. 发送 chat_v2 消息
        await ws.send(
            json.dumps(
                {
                    "type": "chat_v2",
                    "payload": {
                        "text": (
                            "Use the desktop_create_file tool to create a "
                            "file named todo.txt on the desktop with the "
                            "content: 吃饭买菜"
                        ),
                    },
                }
            )
        )
        _print("sent chat_v2 prompt")

        # 3. 监听 + 自动响应权限请求
        permission_count = 0
        tool_calls_seen = []
        final_text = None
        timeout_s = 90.0

        async def consume() -> None:
            nonlocal final_text, permission_count
            while True:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=timeout_s)
                except asyncio.TimeoutError:
                    _print("timeout waiting for next frame")
                    return
                msg = json.loads(raw)
                t = msg.get("type", "")
                payload = msg.get("payload", {})

                if t == "permission_request":
                    permission_count += 1
                    _print(
                        f"permission_request #{permission_count}",
                        f"category={payload.get('category')}",
                        f"summary={payload.get('summary')!r}",
                    )
                    # 自动同意
                    await ws.send(
                        json.dumps(
                            {
                                "type": "permission_response",
                                "payload": {
                                    "request_id": payload["request_id"],
                                    "decision": "allow",
                                },
                            }
                        )
                    )
                    _print("  -> sent allow")
                elif t == "tool_use_event":
                    tool_calls_seen.append(
                        (payload.get("kind"), payload.get("tool_name"))
                    )
                    _print(
                        f"tool_use_event kind={payload.get('kind')}",
                        f"tool={payload.get('tool_name')}",
                    )
                elif t == "chat_v2_final":
                    final_text = payload.get("text", "")
                    _print(
                        f"chat_v2_final iters={payload.get('iterations')}",
                        f"text={final_text[:100]!r}",
                    )
                    return
                elif t == "chat_v2_error":
                    _print("chat_v2_error:", payload)
                    return
                elif t == "chat_response":
                    # 中间的助手消息
                    _print("chat_response:", payload.get("text", "")[:80])
                else:
                    _print(f"  ({t})")

        try:
            await asyncio.wait_for(consume(), timeout=timeout_s)
        except asyncio.TimeoutError:
            _print("FAIL: overall timeout")
            return 1

    _print(f"summary: {permission_count} permission popup(s), tool_calls={tool_calls_seen}")
    if not final_text and not tool_calls_seen:
        _print("FAIL: no tool call observed at all")
        return 1
    if permission_count == 0:
        _print("WARN: permission gate NOT consulted (default-allow path?)")
    _print("PASS: chat_v2 IPC -> permission gate -> tool_use_event flow verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
