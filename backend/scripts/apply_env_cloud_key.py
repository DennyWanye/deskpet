"""Write the root .env DESKPET_CLOUD_API_KEY into the keychain slot the backend reads
(default.deskpet-cloud-llm), using CRED_PERSIST_ENTERPRISE to match keyring-rs (the Rust
crate Tauri uses), so the value the backend reads is exactly what we wrote.

Does NOT print the key value. Verifies by relay test only.
"""
from __future__ import annotations

import os

import httpx
import win32cred  # type: ignore

ENV = r"/path/to/deskpet\.env"
TARGET = "default.deskpet-cloud-llm"
BASE = "https://relay.example.com/v1"


def read_env_key() -> str | None:
    with open(ENV, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("DESKPET_CLOUD_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def write_enterprise(target: str, value: str, username: str = "default") -> None:
    cred = {
        "Type": win32cred.CRED_TYPE_GENERIC,
        "TargetName": target,
        "UserName": username,
        "CredentialBlob": value,  # pywin32 str -> UTF-16LE
        "Persist": win32cred.CRED_PERSIST_ENTERPRISE,
    }
    win32cred.CredWrite(cred, 0)


def read_back(target: str) -> str | None:
    c = win32cred.CredRead(target, win32cred.CRED_TYPE_GENERIC)
    blob = c["CredentialBlob"]
    try:
        return blob.decode("utf-16-le")
    except Exception:  # noqa: BLE001
        return blob.decode("utf-8", "replace")


def relay_test(key: str) -> str:
    cli = httpx.Client(timeout=httpx.Timeout(connect=8, read=30, write=8, pool=8), trust_env=False)
    r = cli.post(
        f"{BASE}/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"model": "deepseek-v4-pro", "messages": [{"role": "user", "content": "好"}],
              "stream": False, "max_tokens": 8},
    )
    return f"HTTP {r.status_code} {r.text[:90].strip()!r}"


def main() -> int:
    key = read_env_key()
    if not key:
        print("FATAL: .env 里没读到 DESKPET_CLOUD_API_KEY")
        return 2
    print(f"env key prefix={key[:7]!r} len={len(key)}")
    print(f"env key relay test -> {relay_test(key)}")
    write_enterprise(TARGET, key)
    back = read_back(TARGET)
    print(f"wrote + re-read match={back == key} (slot len={len(back or '')})")
    print(f"slot relay test -> {relay_test(back or '')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
