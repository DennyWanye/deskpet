# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""Real-config verification for the additive feature-flag backfill.

Simulates a 存量 install whose %APPDATA%\\deskpet\\config.toml predates the
WI-OH-4 `[memory.v2] curation_nudge / auto_learnings` flags, using the ACTUAL
repo config.toml as the bundle default. Confirms one upgrade run backfills
the flags while preserving the user's other content + comments.

Run from backend/:  .venv/Scripts/python.exe scripts/verify_feature_flag_backfill.py
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
REPO_CONFIG = BACKEND.parent / "config.toml"

import tomli  # noqa: E402

NEW_FLAGS = ("curation_nudge", "curation_nudge_every_n_turns", "auto_learnings")


def make_stale_user_config(real_text: str) -> str:
    """Strip the 3 new flag lines → faithful pre-flag 存量 config."""
    out = []
    for line in real_text.splitlines(keepends=True):
        stripped = line.lstrip()
        if any(stripped.startswith(f) for f in NEW_FLAGS):
            continue
        out.append(line)
    return "".join(out)


def main() -> int:
    real_text = REPO_CONFIG.read_text(encoding="utf-8")
    stale_text = make_stale_user_config(real_text)

    # Sanity: the stale config must actually be missing the flags.
    stale_raw = tomli.loads(stale_text)
    v2_before = stale_raw.get("memory", {}).get("v2", {})
    assert "curation_nudge" not in v2_before, "stale fixture still has the flag"
    print(f"[setup] stale 存量 config has NO curation_nudge "
          f"(memory.v2 keys before = {sorted(v2_before)})")

    with tempfile.TemporaryDirectory() as td:
        user_dir = Path(td) / "deskpet"
        user_dir.mkdir()
        user_target = user_dir / "config.toml"
        user_target.write_text(stale_text, encoding="utf-8")

        # Point the backend at our fake AppData + the real repo config as bundle.
        os.environ["DESKPET_USER_DATA_DIR"] = str(user_dir)
        import config as cfg
        cfg._bundle_default_config_path = lambda: REPO_CONFIG  # type: ignore[assignment]

        resolved = cfg.resolve_config_path()
        print(f"[run]   resolve_config_path() -> {resolved}")
        assert resolved == user_target, resolved

        after = tomli.loads(user_target.read_text(encoding="utf-8"))
        v2_after = after["memory"]["v2"]

        ok = True
        for f in NEW_FLAGS:
            present = f in v2_after
            print(f"[check] memory.v2.{f} present={present} value={v2_after.get(f)!r}")
            ok = ok and present
        # The headline assertion.
        assert v2_after.get("curation_nudge") is True, "curation_nudge not True after upgrade"
        assert v2_after.get("auto_learnings") is True

        # Preservation checks: a sampling of unrelated sections must be intact.
        assert after["backend"]["port"] == stale_raw["backend"]["port"]
        assert after["llm"]["model"] == stale_raw["llm"]["model"]
        # facts_extract was already in the stale config → value preserved.
        assert v2_after["facts_extract"] == v2_before["facts_extract"]

        # A user comment from the real config survived the tomlkit round-trip.
        text_after = user_target.read_text(encoding="utf-8")
        assert "Strangler-Fig" in text_after, "comments lost in round-trip"

        # Backup written.
        assert user_target.with_suffix(".pre-migrate-bak").exists()

        # Idempotency: second run is a no-op.
        mtime = user_target.stat().st_mtime_ns
        cfg.resolve_config_path()
        assert user_target.stat().st_mtime_ns == mtime, "2nd run rewrote the file"

    print("\nRESULT: PASS — 存量 config upgraded; curation_nudge now live; "
          "user values + comments preserved; idempotent." if ok else "\nRESULT: FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
