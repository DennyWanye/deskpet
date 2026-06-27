"""Decisive probe: does STREAM=True + json_schema strict hang on gpt-5.5 (relay)?
The app's chat_with_tools always forces stream:True. Read-only diagnosis.
"""
from __future__ import annotations

import os
import sys
import time

import httpx

BASE = "https://relay.example.com/v1"
sys.path.insert(0, os.path.dirname(__file__))
from relay_diag import get_key  # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from deskpet.agent.intent_triage import _PRE_ANALYSIS_SCHEMA  # noqa: E402

MSG = [{"role": "user", "content": "用户问题：帮我分析一下为什么我这段 Python 排序代码越界报错。按 schema 输出。"}]


def stream_call(label: str, rf, model="gpt-5.5") -> None:
    body = {"model": model, "messages": MSG, "max_tokens": 1536, "temperature": 0.2,
            "stream": True, "stream_options": {"include_usage": True}}
    if rf is not None:
        body["response_format"] = rf
    t0 = time.time()
    first_byte = None
    chunks = 0
    content = ""
    try:
        with httpx.Client(timeout=httpx.Timeout(connect=8, read=90, write=8, pool=8), trust_env=False) as cli:
            with cli.stream("POST", f"{BASE}/chat/completions",
                            headers={"Authorization": f"Bearer {get_key()}", "Content-Type": "application/json"},
                            json=body) as r:
                for line in r.iter_lines():
                    if first_byte is None:
                        first_byte = (time.time() - t0) * 1000
                    if not line or not line.startswith("data:"):
                        continue
                    chunks += 1
                    if "[DONE]" in line:
                        break
                dt = (time.time() - t0) * 1000
                print(f"  [{label}] status={r.status_code} first_byte={first_byte}ms total={dt:.0f}ms chunks={chunks}")
    except Exception as e:  # noqa: BLE001
        dt = (time.time() - t0) * 1000
        print(f"  [{label}] EXC {dt:.0f}ms {repr(e)[:140]}")


def main() -> int:
    print("STREAM=True + json_schema strict across models (find one the relay supports):")
    for m in ["gpt-5.5", "gpt-5.4", "gpt-5.4-mini", "gpt-5.2", "deepseek-v4-pro",
              "deepseek-v4-flash", "doubao-seed-2.0-pro", "doubao-1.5-pro-32k", "volc-glm-4.7"]:
        stream_call(f"json_schema {m}", _PRE_ANALYSIS_SCHEMA, model=m)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
