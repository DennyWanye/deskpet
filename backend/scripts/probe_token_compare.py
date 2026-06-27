"""Read both relay keychain tokens and test each against relay.example.com.
Helps diagnose: did re-login update access_token but not the cloud-llm key the backend uses?
Read-only. Does NOT print full tokens (only prefix/len/hash).
"""
from __future__ import annotations

import hashlib
import sys

import httpx

try:
    import win32cred  # type: ignore
except Exception:  # noqa: BLE001
    win32cred = None

BASE = "https://relay.example.com/v1"
TARGETS = [
    "default.deskpet-cloud-llm",
    "access_token.deskpet-relay",
    "device_key.deskpet-relay",
]


def read_cred(target: str) -> str | None:
    if win32cred is None:
        return None
    try:
        c = win32cred.CredRead(target, win32cred.CRED_TYPE_GENERIC)
        blob = c["CredentialBlob"]
        if isinstance(blob, bytes):
            try:
                return blob.decode("utf-16-le")
            except Exception:  # noqa: BLE001
                return blob.decode("utf-8", "replace")
        return str(blob)
    except Exception as exc:  # noqa: BLE001
        print(f"  read {target} ERR {exc}")
        return None


def test_key(key: str) -> str:
    cli = httpx.Client(timeout=httpx.Timeout(connect=8, read=30, write=8, pool=8), trust_env=False)
    try:
        r = cli.post(
            f"{BASE}/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"model": "deepseek-v4-pro", "messages": [{"role": "user", "content": "好"}],
                  "stream": False, "max_tokens": 8},
        )
        return f"HTTP {r.status_code} {r.text[:120].strip()!r}"
    except Exception as exc:  # noqa: BLE001
        return f"EXC {type(exc).__name__}: {str(exc)[:80]}"


def main() -> int:
    for t in TARGETS:
        v = read_cred(t)
        if not v:
            print(f"{t:32s} -> (empty/missing)")
            continue
        h = hashlib.sha256(v.encode("utf-8", "replace")).hexdigest()[:10]
        print(f"{t:32s} prefix={v[:10]!r} len={len(v)} sha={h}")
        print(f"  relay test -> {test_key(v)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
