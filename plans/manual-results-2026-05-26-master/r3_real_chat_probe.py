"""R3-1 真测探针：通过 WebSocket 直接发 '你好' 给 backend → 看是否真的拿到 LLM 回复。

不走 UI，不靠 SendKeys/Clipboard — 用 Python websockets 库直接打 ws://127.0.0.1:8100/ws/control，
模拟前端发 chat 消息，收集所有回包，timeout 60s。

输出：JSON 格式 {sent, got_chunks, full_reply, end_reason, llm_called, errors}
"""
import asyncio
import json
import sys
import time

import websockets

URI = "ws://127.0.0.1:8100/ws/control?secret=<redacted-shared-secret>&session_id=r3-probe"


async def main():
    sent_text = "你好"
    got = {
        "sent": sent_text,
        "uri": URI,
        "start_ts": time.time(),
        "frames": [],
        "ended_ok": False,
        "end_reason": None,
        "llm_response_text": "",
        "errors": [],
    }
    try:
        async with websockets.connect(URI, max_size=2**22, open_timeout=10) as ws:
            # 先收 startup_status frame
            try:
                first = await asyncio.wait_for(ws.recv(), timeout=3)
                got["frames"].append({"_recv": json.loads(first)})
            except Exception as e:
                got["errors"].append(f"first recv fail: {e}")

            # 发送 chat
            await ws.send(json.dumps({
                "type": "chat",
                "payload": {"text": sent_text, "session_id": "r3-probe"},
            }))
            got["sent_ts"] = time.time()

            # 收回包直到 end_turn / error / 60s timeout
            deadline = time.time() + 60
            while time.time() < deadline:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=deadline - time.time())
                except asyncio.TimeoutError:
                    got["end_reason"] = "timeout"
                    break
                try:
                    msg = json.loads(raw)
                except Exception:
                    got["frames"].append({"_recv_raw": raw[:500]})
                    continue
                got["frames"].append(msg)
                t = msg.get("type", "")
                pl = msg.get("payload", {}) or {}
                # 聚合 LLM 回复文字
                if t in ("chat_v2_text_delta", "chat_text_delta", "assistant_chunk", "chat_v2_text", "chat_text"):
                    got["llm_response_text"] += str(pl.get("text", pl.get("delta", "")) or "")
                elif t in ("assistant_message", "chat_v2_message"):
                    if isinstance(pl.get("text"), str):
                        got["llm_response_text"] = pl["text"]
                # 结束信号
                if t in ("chat_v2_done", "chat_done", "assistant_end", "end_turn", "chat_v2_end"):
                    got["ended_ok"] = True
                    got["end_reason"] = t
                    break
                if t in ("chat_v2_error", "chat_error", "error"):
                    got["end_reason"] = f"server_error:{pl.get('error','?')}"
                    got["errors"].append(pl)
                    break
    except Exception as e:
        got["errors"].append(f"top-level: {type(e).__name__}: {e}")
    got["end_ts"] = time.time()
    got["duration_s"] = round(got["end_ts"] - got["start_ts"], 2)
    # 控制输出体积：frames 截断
    if len(got["frames"]) > 50:
        got["frames"] = got["frames"][:25] + [{"_truncated": len(got["frames"]) - 50}] + got["frames"][-25:]
    out_path = sys.argv[1] if len(sys.argv) > 1 else "r3_probe_result.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(got, f, ensure_ascii=False, indent=2)
    # 控制台只 print ASCII 摘要避 GBK encode 错
    summary = {
        "ended_ok": got["ended_ok"],
        "end_reason": got["end_reason"],
        "duration_s": got["duration_s"],
        "reply_len": len(got["llm_response_text"]),
        "reply_preview_ascii_only": got["llm_response_text"].encode("ascii", "replace").decode("ascii")[:200],
        "frame_count": len(got["frames"]),
        "frame_types": [f.get("type", "?") for f in got["frames"][:50] if isinstance(f, dict)],
        "errors": got["errors"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2).encode("ascii", "replace").decode("ascii"))


if __name__ == "__main__":
    asyncio.run(main())
