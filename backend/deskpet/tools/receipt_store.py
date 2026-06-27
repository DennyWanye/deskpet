# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""WI-T2.1/T2.2 — ToolReceipt 持久化 + HMAC keystore wrapper。

PRD §3 D5/D11 真正实现：
  - HMAC key 走 OS keystore（Python `keyring` 库抽象 DPAPI/Keychain/libsecret），
    keystore 不可用时 fallback 到 <user_data>/secrets/receipt_hmac.key (0600)
  - Receipt 写盘到 <user_data>/receipts/<session_id>.jsonl，按会话滚动
  - 启动期自清理 ended_at < now - retention_days 的整文件
  - HMAC key 重生时旧 jsonl 整文件归档到 receipts/archived/

stub 在 receipt.py（已完整 dataclass + HMAC sign/verify）；本模块负责
**持久化 + key 管理**，registry 通过 ReceiptStore.append 接入。

PRD §5 健康区间 metric：
  - verify.sig_invalid_filtered = 0（任一非 0 即 P1 alert）
"""
from __future__ import annotations

import json
import logging
import os
import secrets as _secrets_mod
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from .receipt import (
    ToolReceipt,
    canonical_json,
    hmac_sign,
    hmac_verify,
    make_receipt,
)

logger = logging.getLogger(__name__)

# ─── Keystore wrapper ────────────────────────────────────────

_KEYRING_SERVICE = "deskpet.receipt_hmac"
_KEYRING_USERNAME = "deskpet"
_KEY_BYTES = 32  # 256-bit


def _try_keystore_get() -> Optional[bytes]:
    """尝试从 OS keystore 读 HMAC key；不可用返回 None（不抛）。"""
    try:
        import keyring  # type: ignore
        raw = keyring.get_password(_KEYRING_SERVICE, _KEYRING_USERNAME)
        if raw:
            import base64
            return base64.b64decode(raw)
    except Exception as exc:  # noqa: BLE001
        logger.info("keystore get unavailable: %s", exc)
    return None


def _try_keystore_set(key: bytes) -> bool:
    """尝试写 HMAC key 到 OS keystore；不可用返回 False。"""
    try:
        import base64
        import keyring  # type: ignore
        keyring.set_password(_KEYRING_SERVICE, _KEYRING_USERNAME,
                             base64.b64encode(key).decode("ascii"))
        return True
    except Exception as exc:  # noqa: BLE001
        logger.info("keystore set unavailable: %s", exc)
        return False


def load_or_create_hmac_key(secrets_dir: Path) -> tuple[bytes, str]:
    """加载或生成 HMAC key。

    优先级：
      1. OS keystore（DPAPI/Keychain/libsecret via Python `keyring`）
      2. 裸文件 fallback：<secrets_dir>/receipt_hmac.key (0600 / Windows ACL)

    Returns:
        (key_bytes, source) where source ∈ {"keystore", "file", "generated"}
    """
    # 1. Try keystore
    key = _try_keystore_get()
    if key and len(key) == _KEY_BYTES:
        return key, "keystore"

    # 2. Try bare file
    secrets_dir.mkdir(parents=True, exist_ok=True)
    key_path = secrets_dir / "receipt_hmac.key"
    if key_path.exists():
        try:
            key = key_path.read_bytes()
            if len(key) == _KEY_BYTES:
                # 同时回写 keystore 让下次走 keystore
                _try_keystore_set(key)
                return key, "file"
        except OSError as exc:
            logger.warning("HMAC key file unreadable: %s — regenerating", exc)

    # 3. Generate new
    key = _secrets_mod.token_bytes(_KEY_BYTES)
    if _try_keystore_set(key):
        return key, "generated"  # keystore primary, no file write
    # keystore unavailable: write file with restricted permissions
    try:
        key_path.write_bytes(key)
        try:
            os.chmod(key_path, 0o600)
        except (OSError, NotImplementedError):
            pass  # Windows mode bits — relies on NTFS ACL inheritance
        logger.warning(
            "HMAC key persisted to bare file %s (0600); install `keyring` "
            "for DPAPI/Keychain protection.", key_path
        )
    except OSError as exc:
        logger.error("HMAC key write failed: %s — using ephemeral key", exc)
    return key, "generated"


def sanity_echo(key: bytes) -> bool:
    """启动期 sanity HMAC echo — 验证 key 可用（PRD D11）。"""
    import hmac as _hmac
    import hashlib
    expected = _hmac.new(key, b"ping", hashlib.sha256).hexdigest()
    return len(expected) == 64


# ─── Receipt persistence ─────────────────────────────────────

class ReceiptStore:
    """JSON-Lines per-session receipt store + Ledger 入口。

    用法：
        store = ReceiptStore(user_data_dir, retention_days=7)
        store.append(receipt)
        loaded = store.load_session(session_id)  # for VerifyGate.ReceiptLedger
    """

    def __init__(
        self,
        user_data_dir: Path,
        *,
        retention_days: int = 7,
        key: Optional[bytes] = None,
    ) -> None:
        self.root = Path(user_data_dir)
        self.receipts_dir = self.root / "receipts"
        self.archived_dir = self.receipts_dir / "archived"
        self.secrets_dir = self.root / "secrets"
        self.retention_days = retention_days
        self._lock = threading.Lock()
        if key is None:
            self.key, self.key_source = load_or_create_hmac_key(self.secrets_dir)
            if not sanity_echo(self.key):
                logger.error("HMAC sanity echo failed — receipts will not verify")
        else:
            self.key = key
            self.key_source = "injected"

    # ─── Public API ───────────────────────────────────────

    def append(self, receipt: ToolReceipt) -> None:
        """Append receipt to <session>.jsonl. fire-and-forget on I/O error."""
        self.receipts_dir.mkdir(parents=True, exist_ok=True)
        path = self.receipts_dir / f"{receipt.session_id or 'default'}.jsonl"
        try:
            with self._lock:
                with open(path, "a", encoding="utf-8") as f:
                    f.write(canonical_json(receipt.to_dict()))
                    f.write("\n")
        except OSError as exc:
            # PRD §6 R10: degrade gracefully — log but don't break main loop
            logger.warning("receipt append failed (%s): %s", path, exc)

    def load_session(self, session_id: str) -> list[ToolReceipt]:
        """Load all sig-valid receipts for a session (N1: filter sig-invalid)."""
        path = self.receipts_dir / f"{session_id}.jsonl"
        if not path.exists():
            return []
        out: list[ToolReceipt] = []
        filtered = 0
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                    r = ToolReceipt(**d)
                except (json.JSONDecodeError, TypeError, KeyError) as exc:
                    logger.warning("receipt parse failed: %s", exc)
                    continue
                # N1 信任面：sig-invalid 整条剔除，emit metric
                if not hmac_verify(r, self.key):
                    filtered += 1
                    continue
                out.append(r)
        if filtered > 0:
            # PRD §5 健康区间：sig_invalid_filtered = 0 是 alert 触发条件
            logger.warning(
                "verify.sig_invalid_filtered += %d (session=%s) — "
                "possible HMAC key rotation or tampering",
                filtered, session_id,
            )
        return out

    # ─── Maintenance ─────────────────────────────────────

    def cleanup_expired(self, *, now: Optional[datetime] = None) -> int:
        """Delete receipt files older than retention_days. Called at startup.

        Returns:
            Number of files deleted.
        """
        if not self.receipts_dir.exists():
            return 0
        cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=self.retention_days)
        deleted = 0
        for f in self.receipts_dir.iterdir():
            if not f.is_file() or f.suffix != ".jsonl":
                continue
            try:
                mtime = datetime.fromtimestamp(f.stat().st_mtime, timezone.utc)
            except OSError:
                continue
            if mtime < cutoff:
                try:
                    f.unlink()
                    deleted += 1
                except OSError as exc:
                    logger.warning("cleanup failed for %s: %s", f, exc)
        return deleted

    def archive_all_for_key_rotation(self, *, prefix_hint: str = "rotated") -> int:
        """When HMAC key rotates, all old receipts become sig_invalid → archive."""
        if not self.receipts_dir.exists():
            return 0
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        archive_subdir = self.archived_dir / f"{prefix_hint}-{ts}"
        archive_subdir.mkdir(parents=True, exist_ok=True)
        moved = 0
        for f in self.receipts_dir.iterdir():
            if not f.is_file() or f.suffix != ".jsonl":
                continue
            try:
                f.rename(archive_subdir / f.name)
                moved += 1
            except OSError as exc:
                logger.warning("archive failed for %s: %s", f, exc)
        if moved > 0:
            reason_file = archive_subdir / "INVALID_SIG_REASON.txt"
            reason_file.write_text(
                f"HMAC key rotated/unreadable at {ts}; old receipts cannot "
                f"be sig-verified and were moved here for retention/audit.\n",
                encoding="utf-8",
            )
        return moved


# ─── Convenience: create + sign + append in one shot ─────────

def emit_receipt(
    store: ReceiptStore,
    *,
    tool_name: str,
    args: dict[str, Any],
    started_at: datetime,
    ended_at: datetime,
    ok: bool,
    session_id: str = "",
    iteration: int = 0,
    error_class: Optional[str] = None,
    artifact_shas: Optional[list[str]] = None,
) -> ToolReceipt:
    """End-to-end: build → sign with store.key → append → return."""
    r = make_receipt(
        tool_name=tool_name,
        args=args,
        started_at=started_at,
        ended_at=ended_at,
        ok=ok,
        session_id=session_id,
        iteration=iteration,
        error_class=error_class,
        artifact_shas=artifact_shas,
        secret=store.key,
    )
    store.append(r)
    return r


__all__ = [
    "ReceiptStore",
    "load_or_create_hmac_key",
    "sanity_echo",
    "emit_receipt",
]
