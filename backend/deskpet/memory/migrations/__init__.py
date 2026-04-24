"""Memory DB schema migrations (P4-S1, tasks 2.1-2.5).

Schema versioning is tracked via ``PRAGMA user_version``. Each migration
module owns a single version step; the startup guard picks up the current
value and runs all pending steps in order.

- ``v8_to_v9.py``   — P4 initial migration:
  * ``.bak`` the existing DB before any ALTER.
  * ALTER TABLE messages ADD COLUMN embedding BLOB
      / salience REAL DEFAULT 0.5
      / decay_last_touch REAL
      / user_emotion TEXT
      / audio_file_path TEXT
  * CREATE VIRTUAL TABLE messages_vec USING vec0(
      message_id INTEGER PRIMARY KEY,
      embedding FLOAT[1024] distance_metric=cosine)
  * On disk-full / write failure: restore from ``.bak`` and boot in
    degraded mode (no L3, L1+L2 still work).
"""
