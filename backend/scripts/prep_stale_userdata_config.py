# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""Seed an isolated DESKPET_USER_DATA_DIR with a *stale* config.toml that
predates the WI-OH-4 curation flags — a faithful 存量-install reproduction
for the additive-backfill GUI real-test.

UTF-8 safe (Python, NOT PowerShell Get-Content|Set-Content which mojibakes
the Chinese comments — see memory feedback_powershell_chinese_files).

Usage:  python prep_stale_userdata_config.py <dest_user_data_dir>
"""
from __future__ import annotations

import sys
from pathlib import Path

DROP_FLAGS = ("curation_nudge", "curation_nudge_every_n_turns", "auto_learnings")


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: prep_stale_userdata_config.py <dest_user_data_dir>")
        return 2
    dest_dir = Path(sys.argv[1])
    dest_dir.mkdir(parents=True, exist_ok=True)

    repo_cfg = Path(__file__).resolve().parent.parent.parent / "config.toml"
    real = repo_cfg.read_text(encoding="utf-8")

    kept = []
    dropped = []
    for line in real.splitlines(keepends=True):
        s = line.lstrip()
        if any(s.startswith(f) for f in DROP_FLAGS):
            dropped.append(s.rstrip())
            continue
        kept.append(line)
    stale = "".join(kept)

    out = dest_dir / "config.toml"
    out.write_text(stale, encoding="utf-8")

    # Sanity: stale fixture must NOT contain the flag.
    import tomli
    raw = tomli.loads(stale)
    v2 = raw.get("memory", {}).get("v2", {})
    assert "curation_nudge" not in v2, "stale fixture still has curation_nudge!"

    print(f"[prep] wrote stale config -> {out}")
    print(f"[prep] dropped {len(dropped)} flag line(s): {dropped}")
    print(f"[prep] memory.v2 keys now = {sorted(v2)}")
    print("[prep] OK — curation_nudge absent (faithful 存量 config)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
