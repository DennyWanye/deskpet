# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Dump messages for the p4s24-smoke session — debug helper."""
import os
import sqlite3
import sys

candidates = [
    os.path.join(os.environ.get("APPDATA", ""), "deskpet", "data", "state.db"),
    os.path.join(os.environ.get("APPDATA", ""), "deskpet", "state.db"),
]
db_path = next((c for c in candidates if os.path.exists(c)), None)
if not db_path:
    print("Could not locate state.db. Tried:", candidates)
    sys.exit(1)

print(f"DB: {db_path}")
conn = sqlite3.connect(db_path)
tables = [r[0] for r in conn.execute(
    "SELECT name FROM sqlite_master WHERE type='table'"
).fetchall()]
print("Tables:", tables)
print("user_version:", conn.execute("PRAGMA user_version").fetchone()[0])
print()
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

print("--- assistant rows WITHOUT reasoning_content (would 400 thinking-mode) ---")
cursor = conn.execute(
    """SELECT session_id, COUNT(*)
       FROM messages
       WHERE role='assistant' AND (reasoning_content IS NULL OR reasoning_content = '')
       GROUP BY session_id
       ORDER BY session_id"""
)
total_bad = 0
for sid, cnt in cursor.fetchall():
    print(f"  {sid!r}: {cnt} bad rows")
    total_bad += cnt
print(f"total: {total_bad} assistant rows without reasoning_content")

print()
print("--- assistant rows WITH reasoning_content ---")
cursor = conn.execute(
    """SELECT session_id, COUNT(*)
       FROM messages
       WHERE role='assistant' AND reasoning_content IS NOT NULL AND reasoning_content != ''
       GROUP BY session_id
       ORDER BY session_id"""
)
for sid, cnt in cursor.fetchall():
    print(f"  {sid!r}: {cnt} good rows")
