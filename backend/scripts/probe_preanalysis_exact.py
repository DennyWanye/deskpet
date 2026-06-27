"""Probe gpt-5.5 with the EXACT pre-analysis json_schema (strict) vs json_object,
direct connect, to find what makes the app's pre-analysis hang. Read-only diagnosis.
"""
from __future__ import annotations

import os
import sys
import time

import httpx

BASE = "https://relay.example.com/v1"
sys.path.insert(0, os.path.dirname(__file__))
from relay_diag import get_key  # noqa: E402

# import the real schema the app uses
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from deskpet.agent.intent_triage import _PRE_ANALYSIS_SCHEMA  # noqa: E402

MSG = [
    {"role": "system", "content": "你是预分析器，按 schema 输出 JSON。"},
    {"role": "user", "content": "用户问题：帮我分析一下为什么我这段 Python 排序代码越界报错"},
]


def run(label: str, model: str, rf, stream: bool, trust_env: bool) -> None:
    t0 = time.time()
    body = {"model": model, "messages": MSG, "max_tokens": 600, "stream": stream}
    if rf is not None:
        body["response_format"] = rf
    try:
        with httpx.Client(timeout=httpx.Timeout(connect=8, read=120, write=8, pool=8),
                          trust_env=trust_env) as cli:
            r = cli.post(f"{BASE}/chat/completions",
                         headers={"Authorization": f"Bearer {get_key()}", "Content-Type": "application/json"},
                         json=body)
            dt = (time.time() - t0) * 1000
            try:
                txt = r.json()["choices"][0]["message"]["content"][:80]
            except Exception:
                txt = r.text[:160]
            print(f"  [{label}] status={r.status_code} {dt:7.0f}ms  {txt!r}")
    except Exception as e:  # noqa: BLE001
        dt = (time.time() - t0) * 1000
        print(f"  [{label}] EXC {dt:7.0f}ms {repr(e)[:140]}")


def main() -> int:
    print(f"trust_env(no-proxy)=False probes:")
    run("json_schema strict (EXACT app shape)", "gpt-5.5", _PRE_ANALYSIS_SCHEMA, False, False)
    run("json_object", "gpt-5.5", {"type": "json_object"}, False, False)
    run("no response_format", "gpt-5.5", None, False, False)
    print(f"\ntrust_env=True (would use proxy if set) probe:")
    run("json_schema strict trust_env=True", "gpt-5.5", _PRE_ANALYSIS_SCHEMA, False, True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
