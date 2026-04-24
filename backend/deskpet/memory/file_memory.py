"""L1 file-backed memory (MEMORY.md + USER.md).

P4-S4 deliverable. Two markdown files under ``base_dir``:

- ``MEMORY.md`` — pet-side observations about the user / world (cap 50KB)
- ``USER.md``   — stable user profile traits (cap 20KB)

Entries on disk are separated by ``\n§\n`` — a deliberately human-visible
boundary. Each entry MAY optionally embed a salience hint via trailing
``{{salience=0.7}}``; the parser falls back to 0.5 if the tag is missing
or malformed. When the file grows past its cap we evict the lowest-salience
entries (older first on tie) until the serialized form fits again.

**Frozen-snapshot pattern** (see
``openspec/changes/p4-poseidon-agent-harness/specs/memory-system/spec.md``
§"Frozen Snapshot Pattern"):

- ``read_snapshot()`` is a point-in-time read. The session boot path calls
  it ONCE and caches the returned strings inside the system prompt —
  subsequent ``append()`` calls hit disk but do NOT mutate the cached
  snapshot. Prompt cache therefore stays hot for the whole session.
- The next session boot calls ``read_snapshot()`` again and picks up the
  newly appended entries.

**Concurrency**: an ``asyncio.Lock`` per-file guards the read-modify-write
cycle (append → evict → rewrite). Cross-process concurrency is out of scope
for S4 (backend owns the file); higher layers may add file locking later.

**I/O**: uses ``loop.run_in_executor`` with synchronous ``pathlib`` ops
(we deliberately avoid ``aiofiles`` since it's not in ``pyproject.toml``).
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

_DEFAULT_SALIENCE = 0.5
_SALIENCE_TAG_RE = re.compile(r"\{\{\s*salience\s*=\s*([0-9]*\.?[0-9]+)\s*\}\}\s*$")
_VALID_TARGETS = ("memory", "user")


def _target_to_filename(target: str) -> str:
    if target == "memory":
        return "MEMORY.md"
    if target == "user":
        return "USER.md"
    raise ValueError(
        f"target must be one of {_VALID_TARGETS!r}, got {target!r}"
    )


class FileMemory:
    """L1 file-backed memory with size-cap + salience eviction.

    Parameters
    ----------
    base_dir:
        Directory that holds MEMORY.md / USER.md. Caller resolves this
        (typically ``platformdirs.user_data_dir("deskpet") / "data"``);
        the constructor does NOT ``mkdir`` — use :meth:`initialize` or
        :meth:`ensure_base_dir` for that.
    memory_md_max_kb / user_md_max_kb:
        Size caps in kilobytes (1 KB = 1024 bytes).
    separator:
        Entry separator written to disk. Default ``"\\n§\\n"``.
    """

    def __init__(
        self,
        base_dir: Path,
        memory_md_max_kb: int = 50,
        user_md_max_kb: int = 20,
        separator: str = "\n§\n",
    ) -> None:
        self._base_dir = Path(base_dir)
        self._caps_bytes = {
            "memory": int(memory_md_max_kb) * 1024,
            "user": int(user_md_max_kb) * 1024,
        }
        self._separator = separator
        # Per-target lock — two files don't block each other but serial
        # within one file so append → evict → rewrite stays atomic.
        self._locks: dict[str, asyncio.Lock] = {
            "memory": asyncio.Lock(),
            "user": asyncio.Lock(),
        }

    # ------------------------------------------------------------------
    # Paths / bootstrap
    # ------------------------------------------------------------------
    def _path(self, target: str) -> Path:
        return self._base_dir / _target_to_filename(target)

    def ensure_base_dir(self) -> None:
        """Create ``base_dir`` if missing (sync — used by initialize)."""
        self._base_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public async API
    # ------------------------------------------------------------------
    async def read_snapshot(self) -> dict[str, str]:
        """Return a point-in-time ``{"memory": str, "user": str}``.

        Missing files return empty strings (no exception, no mkdir).
        Callers (session boot) pin this dict into the system prompt and
        MUST NOT re-read it mid-session — that's the frozen-snapshot
        contract protecting prompt cache.
        """
        loop = asyncio.get_running_loop()
        results = await asyncio.gather(
            loop.run_in_executor(None, self._read_text_sync, "memory"),
            loop.run_in_executor(None, self._read_text_sync, "user"),
        )
        return {"memory": results[0], "user": results[1]}

    async def append(
        self, target: str, content: str, salience: float = _DEFAULT_SALIENCE
    ) -> None:
        """Append one entry then enforce size cap.

        The write path is always: parse current file → append new entry →
        evict lowest-salience entries until size ≤ cap → rewrite atomically.
        Missing file is treated as empty.
        """
        if target not in self._VALID_TARGETS_SET:
            raise ValueError(
                f"target must be one of {_VALID_TARGETS!r}, got {target!r}"
            )
        clean = (content or "").strip()
        if not clean:
            # Silently ignore empty payloads — we don't want blank
            # separators littering the file.
            return
        sal = _clamp_salience(salience)

        async with self._locks[target]:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None, self._append_sync, target, clean, sal
            )

    async def list_entries(self, target: str) -> list[dict]:
        """Return parsed entries: ``[{"text": str, "salience": float}, ...]``.

        Text is the user-visible body with any trailing ``{{salience=...}}``
        tag stripped. Corrupt / missing salience tags fall back to 0.5.
        """
        if target not in self._VALID_TARGETS_SET:
            raise ValueError(
                f"target must be one of {_VALID_TARGETS!r}, got {target!r}"
            )
        loop = asyncio.get_running_loop()
        raw = await loop.run_in_executor(None, self._read_text_sync, target)
        return _parse_entries(raw, self._separator)

    # ------------------------------------------------------------------
    # Sync helpers (run in executor)
    # ------------------------------------------------------------------
    _VALID_TARGETS_SET = frozenset(_VALID_TARGETS)

    def _read_text_sync(self, target: str) -> str:
        path = self._path(target)
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""
        except OSError as exc:
            logger.warning(
                "file_memory.read_failed",
                target=target,
                path=str(path),
                error=str(exc),
            )
            return ""

    def _append_sync(self, target: str, text: str, salience: float) -> None:
        path = self._path(target)
        cap = self._caps_bytes[target]

        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            raw = ""

        entries = _parse_entries(raw, self._separator)
        entries.append({"text": text, "salience": salience})

        kept = self._evict_to_fit(entries, cap)
        serialized = _serialize_entries(kept, self._separator)

        # Write atomically via temp-file rename so a crash mid-write
        # doesn't corrupt the file. mkdir parent first in case the dir
        # got swept away post-initialize (rare).
        #
        # NOTE: write in *binary* mode so Python does NOT translate "\n"
        # into platform-native line endings on Windows (\r\n adds a byte
        # per entry and silently busts the size cap).
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(serialized.encode("utf-8"))
        tmp.replace(path)

    # ------------------------------------------------------------------
    # Eviction
    # ------------------------------------------------------------------
    def _evict_to_fit(
        self, entries: list[dict], max_bytes: int
    ) -> list[dict]:
        """Drop lowest-salience entries until the serialized form fits.

        Ties break by insertion order (older wins — i.e. gets kept — when
        two entries share salience we drop the *newer* low-salience one
        LAST; specifically we evict the one with the lowest salience, then
        among equals the one with the lowest insertion index (oldest).
        This keeps behaviour deterministic and surfaces long-lived lessons
        over ephemeral observations.

        NOTE: spec ("older first on tie") dictates older is evicted on tie —
        interpreted as "the one that has been around longer loses", giving
        fresh context the edge. Tests lock the behaviour down.
        """
        if _serialized_size(entries, self._separator) <= max_bytes:
            return entries

        # Attach stable index so equal-salience ties sort oldest-first.
        ordered = list(enumerate(entries))

        while _serialized_size(
            [e for _, e in ordered], self._separator
        ) > max_bytes and ordered:
            # Evict lowest salience; on tie evict the one with the smallest
            # index (i.e. oldest = "older first on tie").
            victim_pos = 0
            victim_sal = ordered[0][1]["salience"]
            victim_idx = ordered[0][0]
            for pos, (idx, entry) in enumerate(ordered):
                sal = entry["salience"]
                if sal < victim_sal or (sal == victim_sal and idx < victim_idx):
                    victim_pos = pos
                    victim_sal = sal
                    victim_idx = idx
            ordered.pop(victim_pos)

        return [e for _, e in ordered]


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------
def _clamp_salience(value: float) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return _DEFAULT_SALIENCE
    if v != v:  # NaN
        return _DEFAULT_SALIENCE
    if v < 0.0:
        return 0.0
    if v > 1.0:
        return 1.0
    return v


def _parse_entries(raw: str, separator: str) -> list[dict]:
    """Split raw file content on ``separator`` → entry dicts.

    Each block MAY end with a ``{{salience=0.7}}`` tag; parse or default
    to 0.5. Empty blocks are skipped (e.g. leading/trailing blank lines).
    """
    if not raw:
        return []
    out: list[dict] = []
    for block in raw.split(separator):
        stripped = block.strip()
        if not stripped:
            continue
        match = _SALIENCE_TAG_RE.search(stripped)
        if match:
            try:
                sal = _clamp_salience(float(match.group(1)))
            except (TypeError, ValueError):
                sal = _DEFAULT_SALIENCE
            text = stripped[: match.start()].rstrip()
        else:
            sal = _DEFAULT_SALIENCE
            text = stripped
        if not text:
            continue
        out.append({"text": text, "salience": sal})
    return out


def _serialize_entries(entries: list[dict], separator: str) -> str:
    """Render entries back to disk format — preserves salience tags."""
    if not entries:
        return ""
    blocks: list[str] = []
    for e in entries:
        text = e["text"].rstrip()
        sal = _clamp_salience(e.get("salience", _DEFAULT_SALIENCE))
        # Only write the salience tag when it differs from the default —
        # keeps casual markdown entries clean when users write their own.
        if abs(sal - _DEFAULT_SALIENCE) > 1e-6:
            block = f"{text} {{{{salience={sal:g}}}}}"
        else:
            block = text
        blocks.append(block)
    return separator.join(blocks)


def _serialized_size(entries: list[dict], separator: str) -> int:
    return len(_serialize_entries(entries, separator).encode("utf-8"))
