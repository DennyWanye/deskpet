"""Quick health probe: is deepseek-v4-pro 403ing on the relay right now? Compare vs gpt-5.5.
Direct connect (no proxy). Read-only diagnostic — NOT test evidence.
"""
from __future__ import annotations

import os
import sys

import httpx

BASE = "https://relay.example.com/v1"
sys.path.insert(0, os.path.dirname(__file__))
from relay_diag import get_key  # noqa: E402


def probe(cli: httpx.Client, key: str, model: str) -> None:
    try:
        r = cli.post(
            f"{BASE}/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": "回复一个字: 好"}],
                "stream": False,
                "max_tokens": 16,
            },
        )
        body = r.text[:160].replace("\n", " ")
        print(f"{model:20s} -> HTTP {r.status_code}  body={body!r}")
    except Exception as exc:  # noqa: BLE001
        print(f"{model:20s} -> EXC {type(exc).__name__}: {str(exc)[:120]}")


def main() -> int:
    key = get_key()
    print(f"key prefix: {key[:8]}... len={len(key)}")
    cli = httpx.Client(timeout=httpx.Timeout(connect=8, read=40, write=8, pool=8), trust_env=False)
    for m in ("deepseek-v4-pro", "gpt-5.5", "deepseek-chat", "deepseek-v3"):
        probe(cli, key, m)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
