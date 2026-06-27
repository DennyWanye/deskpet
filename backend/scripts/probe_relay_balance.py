"""Query the relay (relay.example.com) for the account balance behind the current key.
Tries common one-api/new-api/openai billing endpoints. Read-only diagnostic.
"""
from __future__ import annotations

import os
import sys

import httpx

BASE = "https://relay.example.com"
sys.path.insert(0, os.path.dirname(__file__))
from relay_diag import get_key  # noqa: E402

ENDPOINTS = [
    "/v1/dashboard/billing/credit_grants",
    "/v1/dashboard/billing/subscription",
    "/dashboard/billing/credit_grants",
    "/api/user/self",
    "/v1/me",
    "/api/status",
]


def main() -> int:
    key = get_key()
    print(f"key prefix: {key[:8]}... len={len(key)}")
    cli = httpx.Client(timeout=httpx.Timeout(connect=8, read=20, write=8, pool=8), trust_env=False)
    h = {"Authorization": f"Bearer {key}"}
    for ep in ENDPOINTS:
        try:
            r = cli.get(f"{BASE}{ep}", headers=h)
            print(f"GET {ep:42s} -> {r.status_code}  {r.text[:200].strip()!r}")
        except Exception as exc:  # noqa: BLE001
            print(f"GET {ep:42s} -> EXC {type(exc).__name__}: {str(exc)[:100]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
