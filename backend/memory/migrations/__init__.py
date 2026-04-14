"""Versioned SQLite migrations for the memory store.

V5 §7.3: `每次启动时检查 schema_version，自动执行迁移脚本`.

Layout:
    001_initial.sql
    002_add_xxx.sql
    ...

Each file is raw SQL, applied in lexicographic order. The runner tracks
applied versions in the ``schema_migrations`` table so re-runs are no-ops.

The numbered prefix matters: we sort by filename and the prefix is the
canonical version number. Edits to a file after deployment are forbidden —
introduce a new file instead.
"""
