"""Unified three-layer memory entrypoint.

P4-S4 **skeleton**. Wires L1 file memory + L2 session DB + (future) L3
retriever behind one ``recall`` / ``write`` surface. Goal: callers
(agent loop, ContextAssembler) never special-case layer failures — the
manager degrades gracefully when any layer misbehaves.

Current state
-------------
- L1 (``FileMemory``) is live (this package).
- L2 (``SessionDB`` from ``deskpet.memory.session_db``) is live (S1). The
  manager duck-types both S1's ``append_message`` / ``get_messages`` names
  and the legacy ``write_message`` / ``recent_messages`` / ``append`` shapes
  so tests and future adapters both work.
- L3 (``retriever.py``) is being built in parallel (S3). The ``retriever``
  slot is ``None`` in the default S4 path; ``recall`` degrades to L1+L2-only.
  A post-merge Lead integration commit wires real retriever in.

Graceful degradation contract
-----------------------------
``recall`` MUST NOT raise. Layer failures are caught, logged via
``structlog``, and replaced with sensible empties (``{"memory": "", "user": ""}``
for L1, ``[]`` for L2/L3).

No circular imports
-------------------
``Retriever`` is referenced via ``TYPE_CHECKING`` only. The runtime
type is ``Optional[object]`` and we call ``.recall`` by duck-typing.
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Optional

import structlog

from deskpet.memory.file_memory import FileMemory

if TYPE_CHECKING:  # pragma: no cover — import-only for type hints
    from deskpet.memory.retriever import Retriever  # noqa: F401

logger = structlog.get_logger(__name__)

_DEFAULT_L2_TOP_K = 10
_DEFAULT_L3_TOP_K = 10
_VALID_WRITE_TARGETS = frozenset({"memory", "user", "session"})


class MemoryManager:
    """Three-layer memory façade.

    Parameters
    ----------
    file_memory:
        L1 :class:`FileMemory` instance (already constructed; base_dir
        resolved by the caller).
    session_db:
        L2 backing store. Duck-typed: manager accepts any of S1's
        ``append_message`` / ``get_messages`` (preferred, matches
        ``deskpet.memory.session_db.SessionDB``), the legacy
        ``write_message`` / ``recent_messages`` shape, or the P3
        ``append`` / ``get_recent`` shape. Picks whichever is present.
    retriever:
        L3 hybrid retriever (RRF fusion of FTS5 + vector + recency +
        salience). ``None`` in default S4 path — post-merge Lead wires it.
    """

    def __init__(
        self,
        file_memory: FileMemory,
        session_db: Any,
        retriever: Optional["Retriever"] = None,
    ) -> None:
        self._file_memory = file_memory
        self._session_db = session_db
        self._retriever = retriever

    # ------------------------------------------------------------------
    # Bootstrap
    # ------------------------------------------------------------------
    async def initialize(self) -> None:
        """Create base dir + empty MEMORY.md/USER.md if missing.

        Does NOT initialize L2/L3 — caller owns those lifecycles
        (``SessionDB.initialize``, embedder warm-up, vec schema, etc.).
        """
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._file_memory.ensure_base_dir)
        for target in ("memory", "user"):
            path = self._file_memory._path(target)  # noqa: SLF001 — owned type
            if not path.exists():
                await loop.run_in_executor(
                    None, lambda p=path: p.write_text("", encoding="utf-8")
                )
        logger.info(
            "memory_manager.initialized",
            base_dir=str(self._file_memory._base_dir),  # noqa: SLF001
            retriever_available=self._retriever is not None,
        )

    # ------------------------------------------------------------------
    # Recall
    # ------------------------------------------------------------------
    async def recall(
        self, query: str, policy: Optional[dict] = None
    ) -> dict:
        """Query the three layers per ``policy``; never raises.

        ``policy`` keys (all optional):

        - ``l1``: ``"snapshot"`` → return ``{"memory": str, "user": str}``.
          Any other value (or omitted) → L1 skipped (returned as ``None``).
        - ``l2_top_k``: int, default 10. Number of recent L2 messages to
          fetch. Set 0 to skip L2.
        - ``l3_top_k``: int, default 10. Number of L3 hits. Skipped when
          retriever is ``None`` or 0.
        - ``session_id``: optional str used to scope L2 reads.

        Return shape: ``{"l1": {...} | None, "l2": [...], "l3": [...]}``.
        """
        policy = policy or {}
        want_l1 = policy.get("l1") == "snapshot"
        l2_top_k = int(policy.get("l2_top_k", _DEFAULT_L2_TOP_K))
        l3_top_k = int(policy.get("l3_top_k", _DEFAULT_L3_TOP_K))
        session_id = policy.get("session_id")

        # Kick off layer calls in parallel — each is independently
        # shielded so one failing layer doesn't cancel the others.
        tasks: dict[str, asyncio.Task] = {}
        if want_l1:
            tasks["l1"] = asyncio.create_task(self._safe_l1())
        if l2_top_k > 0:
            tasks["l2"] = asyncio.create_task(
                self._safe_l2(session_id, l2_top_k)
            )
        if self._retriever is not None and l3_top_k > 0:
            tasks["l3"] = asyncio.create_task(
                self._safe_l3(query, l3_top_k, policy)
            )

        if tasks:
            await asyncio.gather(*tasks.values(), return_exceptions=True)

        l1_result = tasks["l1"].result() if "l1" in tasks else None
        l2_result = tasks["l2"].result() if "l2" in tasks else []
        l3_result = tasks["l3"].result() if "l3" in tasks else []

        return {
            "l1": l1_result,
            "l2": l2_result,
            "l3": l3_result,
        }

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------
    async def write(
        self,
        content: str,
        target: str,
        salience: float = 0.5,
        session_id: Optional[str] = None,
        role: str = "assistant",
    ) -> None:
        """Route a write to the correct layer.

        - ``target in {"memory", "user"}`` → :class:`FileMemory.append`.
          Falls back silently on empty content (FileMemory handles that).
        - ``target == "session"`` → delegates to the session DB. Prefers
          S1's ``append_message``, then legacy ``write_message`` /
          ``append``. ``session_id`` is required for session writes.
        """
        if target not in _VALID_WRITE_TARGETS:
            raise ValueError(
                f"target must be one of {sorted(_VALID_WRITE_TARGETS)}, "
                f"got {target!r}"
            )

        if target in ("memory", "user"):
            await self._file_memory.append(target, content, salience=salience)
            return

        # target == "session"
        if session_id is None:
            raise ValueError("session_id is required when target='session'")
        db = self._session_db
        # Prefer the S1 SessionDB shape, then write_message, then the P3
        # conversation store's .append(). Order reflects "use the most
        # correct / most recent API available".
        if hasattr(db, "append_message"):
            await db.append_message(
                session_id=session_id, role=role, content=content
            )
        elif hasattr(db, "write_message"):
            await db.write_message(
                session_id=session_id, role=role, content=content
            )
        elif hasattr(db, "append"):
            await db.append(session_id, role, content)
        else:
            raise AttributeError(
                "session_db exposes none of: append_message, "
                "write_message, append"
            )

    # ------------------------------------------------------------------
    # Internal: per-layer safe wrappers
    # ------------------------------------------------------------------
    async def _safe_l1(self) -> dict:
        try:
            return await self._file_memory.read_snapshot()
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning(
                "memory_manager.l1_failed", error=str(exc), error_type=type(exc).__name__
            )
            return {"memory": "", "user": ""}

    async def _safe_l2(
        self, session_id: Optional[str], top_k: int
    ) -> list[dict]:
        try:
            db = self._session_db
            # Honour whichever read method the backend exposes.
            # Preferred: S1's get_messages(session_id, limit).
            if hasattr(db, "get_messages"):
                rows = await db.get_messages(
                    session_id=session_id, limit=top_k
                )
            elif hasattr(db, "recent_messages"):
                rows = await db.recent_messages(
                    session_id=session_id, limit=top_k
                )
            elif hasattr(db, "get_recent"):
                rows = await db.get_recent(session_id or "", limit=top_k)
            else:
                logger.warning(
                    "memory_manager.l2_no_read_method",
                    db_type=type(db).__name__,
                )
                return []
            return [_to_dict(row) for row in rows or []]
        except Exception as exc:
            logger.warning(
                "memory_manager.l2_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return []

    async def _safe_l3(
        self, query: str, top_k: int, policy: dict
    ) -> list[dict]:
        try:
            retriever = self._retriever
            if retriever is None:
                return []
            # S3's real Retriever has signature ``recall(query, top_k)``
            # (see deskpet.memory.retriever). Test doubles in
            # test_deskpet_memory_manager.py use ``recall(query, policy)``
            # because they predate the final signature. Try the real
            # keyword-arg shape first, then fall back for the fake.
            if hasattr(retriever, "recall"):
                try:
                    hits = await retriever.recall(query, top_k=top_k)
                except TypeError:
                    # Fake retriever in unit tests accepts (query, policy).
                    hits = await retriever.recall(query, {**policy, "top_k": top_k})
            elif hasattr(retriever, "search"):
                hits = await retriever.search(query, top_k=top_k)
            else:
                logger.warning(
                    "memory_manager.l3_no_method",
                    retriever_type=type(retriever).__name__,
                )
                return []
            return [_to_dict(hit) for hit in hits or []]
        except Exception as exc:
            logger.warning(
                "memory_manager.l3_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _to_dict(row: Any) -> dict:
    """Coerce a row/hit into a plain dict for the caller."""
    if isinstance(row, dict):
        return row
    # Dataclasses / namedtuples / simple objects with __dict__ — be tolerant.
    if hasattr(row, "_asdict"):
        return dict(row._asdict())
    if hasattr(row, "__dataclass_fields__"):
        return {f: getattr(row, f) for f in row.__dataclass_fields__}
    if hasattr(row, "__dict__"):
        return {k: v for k, v in vars(row).items() if not k.startswith("_")}
    # Last resort: wrap scalars so callers still get a dict shape.
    return {"value": row}
