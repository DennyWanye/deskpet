"""Diagnose which relay models respond to a NON-STREAMING structured (json) call
(the pre-analysis shape that currently hangs on gpt-5.5). Direct connect (no proxy).

Read-only probe to unblock the real UI E2E — NOT used as test evidence.
"""
from __future__ import annotations

import os
import sys
import time

import httpx

BASE = "https://relay.example.com/v1"

# reuse relay_diag's key reader
sys.path.insert(0, os.path.dirname(__file__))
from relay_diag import get_key  # noqa: E402


def client() -> httpx.Client:
    return httpx.Client(timeout=httpx.Timeout(connect=8, read=40, write=8, pool=8), trust_env=False)


def list_models(cli: httpx.Client, key: str) -> list[str]:
    r = cli.get(f"{BASE}/models", headers={"Authorization": f"Bearer {key}"})
    if r.status_code >= 400:
        print("models ERR", r.status_code, r.text[:200])
        return []
    ids = sorted(str(m.get("id")) for m in r.json().get("data", []))
    print(f"=== {len(ids)} models ===")
    for i in ids:
        print("  ", i)
    return ids


def probe_structured(cli: httpx.Client, key: str, model: str) -> None:
    """Non-streaming + response_format json_object — the pre-analysis shape."""
    t0 = time.time()
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You output only compact JSON."},
            {"role": "user", "content": '返回 JSON：{"problem_type":"debug","ambiguity":0.2}。只输出 JSON。'},
        ],
        "max_tokens": 64,
        "stream": False,
        "response_format": {"type": "json_object"},
    }
    try:
        r = cli.post(f"{BASE}/chat/completions",
                     headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                     json=body)
        dt = (time.time() - t0) * 1000
        txt = ""
        try:
            txt = r.json()["choices"][0]["message"]["content"][:60]
        except Exception:
            txt = r.text[:120]
        print(f"  {model:22s} status={r.status_code} {dt:6.0f}ms  {txt!r}")
    except Exception as e:  # noqa: BLE001
        dt = (time.time() - t0) * 1000
        print(f"  {model:22s} EXC {dt:6.0f}ms {repr(e)[:120]}")


def main() -> int:
    key = get_key()
    if not key:
        print("FATAL no key")
        return 2
    print(f"[key] len={len(key)} prefix={key[:6]}")
    with client() as cli:
        ids = list_models(cli, key)
        print("\n=== NON-STREAM structured (json_object) latency probe ===")
        # the model the pipeline currently uses + likely fast/non-thinking candidates
        wanted = ["gpt-5.5"]
        for cand in ["gpt-4.1", "gpt-4.1-mini", "gpt-4o", "gpt-4o-mini", "gpt-5-mini",
                     "deepseek-v3", "deepseek-v3.1", "qwen-max", "qwen-plus", "glm-4.6", "claude-haiku-4.5"]:
            if cand in ids:
                wanted.append(cand)
        for m in wanted:
            probe_structured(cli, key, m)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
