"""P4-S11 IPC handlers — MemoryPanel + ContextTrace endpoints.

Four message types the front-end drives:

- ``skills_list``         → ``skills_list_response``
- ``decisions_list``      → ``decisions_list_response``
- ``memory_search``       → ``memory_search_response``
- ``memory_l1_list``      → ``memory_l1_list_response``
- ``memory_l1_delete``    → ``memory_l1_delete_ack``

Every handler tolerates "service not registered" gracefully (empty
payload + warning log) so the S11 front-end can ship before the S12
wire-in flips on `SkillLoader` / `ContextAssembler` / `MemoryManager`.

The dispatcher below is called from ``backend/main.py``'s control
channel after auth. It never raises — protocol-level errors round-trip
as ``{"type": "error", "payload": {"message": "..."}}``.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable, Iterable, Optional

import structlog

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Message types this module owns. main.py dispatches here via membership check.
# ---------------------------------------------------------------------------
P4_IPC_MESSAGE_TYPES = frozenset(
    {
        "skills_list",
        "decisions_list",
        "memory_search",
        "memory_l1_list",
        "memory_l1_delete",
        # P4-S16: SettingsPanel "BGE-M3 状态" 卡片探针。
        "embedder_status",
    }
)


# ---------------------------------------------------------------------------
# Public dispatch entrypoint
# ---------------------------------------------------------------------------
async def handle(
    ws: Any,
    session_id: str,
    msg_type: str,
    payload: dict[str, Any],
    service_context: Any,
) -> None:
    """Route an S11 message to its handler. Never raises."""
    try:
        if msg_type == "skills_list":
            await _handle_skills_list(ws, payload, service_context)
        elif msg_type == "decisions_list":
            await _handle_decisions_list(ws, payload, service_context)
        elif msg_type == "memory_search":
            await _handle_memory_search(ws, session_id, payload, service_context)
        elif msg_type == "memory_l1_list":
            await _handle_memory_l1_list(ws, payload, service_context)
        elif msg_type == "memory_l1_delete":
            await _handle_memory_l1_delete(ws, payload, service_context)
        elif msg_type == "embedder_status":
            await _handle_embedder_status(ws, payload, service_context)
        else:
            # Shouldn't happen — membership check is done by caller.
            await _send_error(ws, f"unknown P4 message type: {msg_type}")
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning(
            "p4_ipc.handler_failed",
            msg_type=msg_type,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        await _send_error(ws, f"p4 {msg_type} failed: {exc}")


# ---------------------------------------------------------------------------
# Individual handlers
# ---------------------------------------------------------------------------
async def _handle_skills_list(
    ws: Any, payload: dict[str, Any], sc: Any
) -> None:
    loader = _get_service(sc, "skill_loader")
    if loader is None:
        await ws.send_json(
            {
                "type": "skills_list_response",
                "payload": {"skills": [], "reason": "skill_loader_not_registered"},
            }
        )
        return
    try:
        skills = loader.list_skills()
    except Exception as exc:
        logger.warning("p4_ipc.skills_list_failed", error=str(exc))
        skills = []
    await ws.send_json(
        {
            "type": "skills_list_response",
            "payload": {"skills": list(skills)},
        }
    )


async def _handle_decisions_list(
    ws: Any, payload: dict[str, Any], sc: Any
) -> None:
    raw_limit = payload.get("limit")
    if raw_limit is None:
        limit = 50
    else:
        try:
            limit = max(1, min(int(raw_limit), 200))
        except (TypeError, ValueError):
            limit = 50

    assembler = _get_service(sc, "context_assembler")
    if assembler is None:
        await ws.send_json(
            {
                "type": "decisions_list_response",
                "payload": {
                    "decisions": [],
                    "reason": "context_assembler_not_registered",
                },
            }
        )
        return
    try:
        decisions = assembler.recent_decisions(n=limit)
    except Exception as exc:
        logger.warning("p4_ipc.decisions_list_failed", error=str(exc))
        decisions = []
    await ws.send_json(
        {
            "type": "decisions_list_response",
            "payload": {"decisions": list(decisions)},
        }
    )


async def _handle_memory_search(
    ws: Any, session_id: str, payload: dict[str, Any], sc: Any
) -> None:
    query = str(payload.get("query") or "").strip()
    if not query:
        await _send_error(ws, "memory_search requires non-empty query")
        return
    raw_top_k = payload.get("top_k")
    if raw_top_k is None:
        top_k = 10
    else:
        try:
            top_k = max(1, min(int(raw_top_k), 50))
        except (TypeError, ValueError):
            top_k = 10

    manager = _get_service(sc, "memory_manager")
    if manager is None:
        await ws.send_json(
            {
                "type": "memory_search_response",
                "payload": {
                    "query": query,
                    "hits": [],
                    "reason": "memory_manager_not_registered",
                },
            }
        )
        return
    try:
        recall = await manager.recall(
            query,
            policy={
                "l1": "skip",
                "l2_top_k": 0,
                "l3_top_k": top_k,
                "session_id": session_id,
            },
        )
    except Exception as exc:
        logger.warning(
            "p4_ipc.memory_search_failed", error=str(exc), query_len=len(query)
        )
        await ws.send_json(
            {
                "type": "memory_search_response",
                "payload": {"query": query, "hits": [], "error": str(exc)},
            }
        )
        return

    hits = _recall_to_hits(recall)
    await ws.send_json(
        {
            "type": "memory_search_response",
            "payload": {"query": query, "hits": hits},
        }
    )


async def _handle_memory_l1_list(
    ws: Any, payload: dict[str, Any], sc: Any
) -> None:
    target = (payload.get("target") or "memory").strip()
    if target not in ("memory", "user"):
        await _send_error(ws, "target must be 'memory' or 'user'")
        return

    file_memory = _get_file_memory(sc)
    if file_memory is None:
        await ws.send_json(
            {
                "type": "memory_l1_list_response",
                "payload": {
                    "target": target,
                    "entries": [],
                    "reason": "file_memory_not_registered",
                },
            }
        )
        return
    try:
        entries = await file_memory.list_entries(target)
    except Exception as exc:
        logger.warning(
            "p4_ipc.memory_l1_list_failed", target=target, error=str(exc)
        )
        entries = []
    # Stamp with list index so the UI can drive delete without server-side IDs.
    indexed = [
        {"index": i, "text": e.get("text", ""), "salience": e.get("salience", 0.5)}
        for i, e in enumerate(entries)
    ]
    await ws.send_json(
        {
            "type": "memory_l1_list_response",
            "payload": {"target": target, "entries": indexed},
        }
    )


async def _handle_memory_l1_delete(
    ws: Any, payload: dict[str, Any], sc: Any
) -> None:
    target = (payload.get("target") or "memory").strip()
    if target not in ("memory", "user"):
        await _send_error(ws, "target must be 'memory' or 'user'")
        return
    index = payload.get("index")
    if not isinstance(index, int) or index < 0:
        await _send_error(ws, "memory_l1_delete requires integer index >= 0")
        return

    file_memory = _get_file_memory(sc)
    if file_memory is None:
        await ws.send_json(
            {
                "type": "memory_l1_delete_ack",
                "payload": {
                    "target": target,
                    "index": index,
                    "deleted": False,
                    "reason": "file_memory_not_registered",
                },
            }
        )
        return
    try:
        deleted = await file_memory.delete_entry(target, index)
    except Exception as exc:
        logger.warning(
            "p4_ipc.memory_l1_delete_failed",
            target=target,
            index=index,
            error=str(exc),
        )
        deleted = False
    await ws.send_json(
        {
            "type": "memory_l1_delete_ack",
            "payload": {"target": target, "index": index, "deleted": deleted},
        }
    )


async def _handle_embedder_status(
    ws: Any, payload: dict[str, Any], sc: Any
) -> None:
    """P4-S16: 查询当前 Embedder 状态供 SettingsPanel 渲染。

    返回 ``{is_ready, is_mock, model_path, reason?}``。Embedder 走
    ServiceContext 正式注册路径（``_VALID_SERVICES`` 含 ``embedder``）。
    任何阶段失败都退到 "未注册" 形态而不是抛错——前端拿到 reason 字段
    就知道为什么不能用。
    """
    embedder = _get_service(sc, "embedder")
    if embedder is None:
        await ws.send_json(
            {
                "type": "embedder_status_response",
                "payload": {
                    "is_ready": False,
                    "is_mock": False,
                    "model_path": "",
                    "reason": "embedder_not_registered",
                },
            }
        )
        return
    try:
        is_ready = bool(embedder.is_ready())
        is_mock = bool(embedder.is_mock())
        # _model_path 是 Path 对象；str() 兼容缺失情况
        model_path = str(getattr(embedder, "_model_path", "") or "")
    except Exception as exc:
        logger.warning(
            "p4_ipc.embedder_status_failed",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        await ws.send_json(
            {
                "type": "embedder_status_response",
                "payload": {
                    "is_ready": False,
                    "is_mock": False,
                    "model_path": "",
                    "reason": f"embedder_error: {type(exc).__name__}",
                },
            }
        )
        return
    await ws.send_json(
        {
            "type": "embedder_status_response",
            "payload": {
                "is_ready": is_ready,
                "is_mock": is_mock,
                "model_path": model_path,
            },
        }
    )


# ---------------------------------------------------------------------------
# Service lookup helpers
# ---------------------------------------------------------------------------
def _get_service(sc: Any, name: str) -> Optional[Any]:
    """Read a registered service via getter method or attribute, tolerantly."""
    if sc is None:
        return None
    # ServiceContext exposes .get(name) and dynamic attribute access.
    getter = getattr(sc, "get", None)
    if callable(getter):
        try:
            return getter(name)
        except Exception:
            pass
    return getattr(sc, name, None)


def _get_file_memory(sc: Any) -> Optional[Any]:
    """Resolve FileMemory either directly or via MemoryManager.file_memory."""
    direct = _get_service(sc, "file_memory")
    if direct is not None:
        return direct
    manager = _get_service(sc, "memory_manager")
    if manager is None:
        return None
    # MemoryManager wraps FileMemory; expose the inner handle if present.
    return getattr(manager, "file_memory", None)


# ---------------------------------------------------------------------------
# Payload shaping
# ---------------------------------------------------------------------------
def _recall_to_hits(recall: Any) -> list[dict[str, Any]]:
    """Normalise MemoryManager.recall() output for the UI.

    The recall object shape varies by version — it may be a list of dicts,
    an object with a ``.l3`` attribute, or a dict with ``"l3"`` key. We
    prefer the L3 vector hits (query is a vector search) and fall back to
    whatever iterable is present.
    """
    if recall is None:
        return []

    # Object-with-.l3 path (current MemoryManager).
    l3 = getattr(recall, "l3", None)
    if l3 is None and isinstance(recall, dict):
        l3 = recall.get("l3")
    candidates: Iterable[Any]
    if l3:
        candidates = l3
    elif isinstance(recall, (list, tuple)):
        candidates = recall
    else:
        return []

    hits: list[dict[str, Any]] = []
    for item in candidates:
        hits.append(_hit_to_dict(item))
    return hits


def _hit_to_dict(item: Any) -> dict[str, Any]:
    if isinstance(item, dict):
        return {
            "text": str(item.get("text") or item.get("content") or ""),
            "score": float(item.get("score") or 0.0),
            "source": str(item.get("source") or item.get("src") or ""),
            "created_at": item.get("created_at"),
            "session_id": item.get("session_id"),
        }
    return {
        "text": str(getattr(item, "text", getattr(item, "content", "")) or ""),
        "score": float(getattr(item, "score", 0.0) or 0.0),
        "source": str(getattr(item, "source", "") or ""),
        "created_at": getattr(item, "created_at", None),
        "session_id": getattr(item, "session_id", None),
    }


# ---------------------------------------------------------------------------
# Error helper
# ---------------------------------------------------------------------------
async def _send_error(ws: Any, message: str) -> None:
    try:
        await ws.send_json({"type": "error", "payload": {"message": message}})
    except Exception:  # pragma: no cover
        # WebSocket probably dead — nothing else to do.
        pass
