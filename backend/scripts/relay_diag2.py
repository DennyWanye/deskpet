"""复测：seedream 用生产同款 payload；4.5/5.0 试不同 size 看 400 根因。"""
from __future__ import annotations

import json
import time

import httpx
import win32cred

BASE = "https://relay.example.com/v1"


def key() -> str:
    c = win32cred.CredRead("default.deskpet-cloud-llm", win32cred.CRED_TYPE_GENERIC)
    return c["CredentialBlob"].decode("utf-16-le")


def post(cli, k, model, payload):
    t0 = time.time()
    try:
        r = cli.post(f"{BASE}/images/generations",
                     headers={"Authorization": f"Bearer {k}", "Content-Type": "application/json"},
                     json={"model": model, **payload})
        dt = (time.time() - t0) * 1000
        try:
            j = r.json()
        except Exception:
            j = r.text[:200]
        if r.status_code < 400 and isinstance(j, dict):
            d = j.get("data", [])
            info = f"items={len(d)} url={bool(d and d[0].get('url'))} b64={bool(d and d[0].get('b64_json'))}"
        else:
            info = json.dumps(j, ensure_ascii=False)[:200] if isinstance(j, dict) else str(j)
        print(f"  {model:22s} {str(payload)[:60]:62s} -> {r.status_code} {dt:.0f}ms {info}")
    except Exception as e:
        print(f"  {model:22s} EXC {repr(e)[:120]}")


def main():
    k = key()
    cli = httpx.Client(timeout=httpx.Timeout(connect=8, read=60, write=8, pool=8), trust_env=False)
    print("=== seedream-4.0 生产同款 payload（response_format=b64_json + quality）===")
    post(cli, k, "doubao-seedream-4.0", {"prompt": "a small red circle on white", "size": "1024x1024", "n": 1, "quality": "high", "response_format": "b64_json"})
    post(cli, k, "doubao-seedream-4.0", {"prompt": "a small red circle on white", "size": "1024x1024", "n": 1, "response_format": "b64_json"})
    print("\n=== 4.5 / 5.0-lite 试不同 size ===")
    for m in ("doubao-seedream-4.5", "doubao-seedream-5.0-lite"):
        for sz in ("1024x1024", "2048x2048", "1K", "2K"):
            post(cli, k, m, {"prompt": "a small red circle on white", "size": sz, "n": 1})
    cli.close()


if __name__ == "__main__":
    main()
