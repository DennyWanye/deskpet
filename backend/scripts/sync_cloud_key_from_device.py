"""Sync the backend's cloud-llm keychain slot from the (re-login-refreshed) relay device_key.

Root cause (2026-06-25): RelayAuthAdapter rotates `device_key.deskpet-relay` but never mirrors
it into `default.deskpet-cloud-llm` (the slot Tauri injects as DESKPET_CLOUD_API_KEY). After a
re-login the device_key is fresh+funded while cloud-llm stays on the original (now credit-depleted)
token → backend 403s. This copies device_key → cloud-llm so the spawned backend uses the funded key.

Safe: backs up the old cloud-llm value to a local file, writes UTF-16LE (matching keyring-rs),
re-reads via the backend's own path, and relay-tests before declaring success.
"""
from __future__ import annotations

import os
import sys

import httpx
import win32cred  # type: ignore

CLOUD_TARGET = "default.deskpet-cloud-llm"
DEVICE_TARGET = "device_key.deskpet-relay"
BACKUP = os.path.join(os.path.dirname(__file__), "_cloud_key_backup.txt")
BASE = "https://relay.example.com/v1"


def read(target: str) -> str | None:
    try:
        c = win32cred.CredRead(target, win32cred.CRED_TYPE_GENERIC)
        blob = c["CredentialBlob"]
        try:
            return blob.decode("utf-16-le")
        except Exception:  # noqa: BLE001
            return blob.decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        print(f"read {target} -> {exc}")
        return None


def write(target: str, value: str, username: str = "default") -> None:
    cred = {
        "Type": win32cred.CRED_TYPE_GENERIC,
        "TargetName": target,
        "UserName": username,
        "CredentialBlob": value,  # pywin32 takes a str and stores it UTF-16LE (matches keyring-rs)
        "Persist": win32cred.CRED_PERSIST_LOCAL_MACHINE,
    }
    win32cred.CredWrite(cred, 0)


def relay_ok(key: str) -> str:
    cli = httpx.Client(timeout=httpx.Timeout(connect=8, read=30, write=8, pool=8), trust_env=False)
    r = cli.post(
        f"{BASE}/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"model": "deepseek-v4-pro", "messages": [{"role": "user", "content": "好"}],
              "stream": False, "max_tokens": 8},
    )
    return f"HTTP {r.status_code} {r.text[:90].strip()!r}"


def main() -> int:
    device = read(DEVICE_TARGET)
    old_cloud = read(CLOUD_TARGET)
    if not device:
        print("FATAL: device_key 读不到")
        return 2
    print(f"device_key  prefix={device[:10]!r} len={len(device)}")
    print(f"old cloud   prefix={(old_cloud or '')[:10]!r} len={len(old_cloud or '')}")
    if old_cloud:
        with open(BACKUP, "w", encoding="utf-8") as f:
            f.write(old_cloud)
        print(f"backed up old cloud key -> {BACKUP}")
    if device == old_cloud:
        print("already in sync — nothing to do")
    else:
        write(CLOUD_TARGET, device)
        print("wrote device_key -> cloud-llm slot")
    # verify round-trip via the backend's own read path
    check = read(CLOUD_TARGET)
    print(f"re-read cloud prefix={(check or '')[:10]!r} len={len(check or '')} match_device={check == device}")
    print(f"relay test  -> {relay_ok(check or '')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
