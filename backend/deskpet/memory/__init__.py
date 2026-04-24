"""P4 three-layer memory system.

- L1 (file memory, P4-S4): ``file_memory.py``
    MEMORY.md + USER.md under %APPDATA%\\deskpet\\, ``\\n§\\n`` separated,
    50KB / 20KB caps, frozen snapshot at session start.
- L2 (session DB, P4-S1 lower): ``session_db.py``
    aiosqlite WAL-mode SQLite with FTS5 messages index. Lifted from
    Hermes ``hermes_state.py`` with multi-tenant columns stripped.
- L3 (vector layer, P4-S2 / S3): ``embedder.py`` + ``retriever.py``
    BGE-M3 1024-dim embeddings written async via ``embedding_queue``;
    ``messages_vec`` virtual table (sqlite-vec); hybrid RRF recall
    (vec 0.5 + fts 0.3 + recency 0.15 + salience 0.05).

The unified entrypoint is ``manager.py`` (``MemoryManager.recall`` /
``write``). DB schema migrations live in ``migrations/``.
"""
