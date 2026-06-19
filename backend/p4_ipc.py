# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

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

import tomllib
from dataclasses import asdict
from pathlib import Path
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
        # Phase 1.1.6（context-1m-rearch）: SettingsPanel「模型上下文」卡片。
        "model_context_get",
        "model_context_set",
        # Stage 2 WI-S2.1a / E3 v2 — MemoryPanel facts view + 🗑 + undo
        "memory_facts_list",
        "memory_forget",
        "memory_forget_undo",
        # Option A (2026-06-05) — 瘦包首启模型下载进度探针。
        "model_provision_status",
        # FP-4 WI-3.3 — 用户 Pin/Unpin 钉住事实（跳过 daily_decay 衰减）。
        "memory_pin",
        "memory_unpin",
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
        elif msg_type == "model_provision_status":
            await _handle_model_provision_status(ws, payload, service_context)
        elif msg_type == "model_context_get":
            await _handle_model_context_get(ws, payload)
        elif msg_type == "model_context_set":
            await _handle_model_context_set(ws, payload)
        elif msg_type == "memory_facts_list":
            await _handle_memory_facts_list(ws, payload, service_context)
        elif msg_type == "memory_forget":
            await _handle_memory_forget_ws(ws, payload, service_context)
        elif msg_type == "memory_forget_undo":
            await _handle_memory_forget_undo(ws, payload, service_context)
        elif msg_type == "memory_pin":
            await _handle_memory_pin(ws, payload, service_context, pinned=True)
        elif msg_type == "memory_unpin":
            await _handle_memory_pin(ws, payload, service_context, pinned=False)
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


async def _handle_model_provision_status(
    ws: Any, payload: dict[str, Any], sc: Any
) -> None:
    """Option A: 首启模型下载进度探针，供前端进度卡片轮询。

    返回 provisioner.status()（state/current/index/total/downloaded_bytes/
    total_bytes/error）。未注册（如非瘦包构建从不下载）→ state="ready"，前端
    据此不显示进度卡片。任何异常都退到 ready，绝不阻断前端。
    """
    prov = _get_service(sc, "model_provisioner")
    if prov is None:
        await ws.send_json(
            {
                "type": "model_provision_status_response",
                "payload": {"state": "ready", "total": 0},
            }
        )
        return
    try:
        status = prov.status()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "p4_ipc.model_provision_status_failed",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        status = {"state": "ready", "total": 0}
    await ws.send_json(
        {"type": "model_provision_status_response", "payload": status}
    )


# ---------------------------------------------------------------------------
# Stage 2 / WI-S2.1a — MemoryPanel facts view + memory_forget UI bridge
# ---------------------------------------------------------------------------
async def _handle_memory_facts_list(
    ws: Any, payload: dict[str, Any], sc: Any,
) -> None:
    """返回 active facts 给前端 facts view 渲染。

    payload: ``{limit?: int, subject?: str, category?: str}``
    Response: ``{type: "memory_facts_list_response", payload: {facts: [...]}}``
    """
    facts_store = _get_service(sc, "facts_store")
    if facts_store is None:
        await ws.send_json({
            "type": "memory_facts_list_response",
            "payload": {
                "facts": [],
                "reason": "facts_store_not_registered",
            },
        })
        return
    raw_limit = payload.get("limit")
    try:
        limit = max(1, min(int(raw_limit) if raw_limit is not None else 200, 500))
    except (TypeError, ValueError):
        limit = 200
    subject = payload.get("subject")
    category = payload.get("category")
    try:
        rows = await facts_store.list_active(
            subject=str(subject) if subject else None,
            category=str(category) if category else None,
            limit=limit,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("p4_ipc.memory_facts_list_failed", error=str(exc))
        rows = []
    # 序列化 BLOB embedding 列（前端用不上 + JSON 不好放二进制）。
    out_rows: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        d.pop("embedding", None)
        out_rows.append(d)
    await ws.send_json({
        "type": "memory_facts_list_response",
        "payload": {"facts": out_rows},
    })


async def _handle_memory_forget_ws(
    ws: Any, payload: dict[str, Any], sc: Any,
) -> None:
    """前端点 🗑 按钮的桥接 —— UI 已确认过来源，直调工具实现。

    payload: ``{fact_id?: int, query?: str}``
    Response: ``{type: "memory_forget_response", payload: {status, op_id?, forgotten_ids?, reason?}}``
    """
    facts_store = _get_service(sc, "facts_store")
    if facts_store is None:
        await ws.send_json({
            "type": "memory_forget_response",
            "payload": {
                "status": "error",
                "reason": "facts_store_not_registered",
            },
        })
        return
    try:
        from deskpet.tools.memory_tools import (
            _forget_by_id as _do_forget_by_id,
            _forget_by_query as _do_forget_by_query,
            is_bound as _memory_tool_bound,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("p4_ipc.memory_forget_import_failed", error=str(exc))
        await ws.send_json({
            "type": "memory_forget_response",
            "payload": {"status": "error", "reason": str(exc)},
        })
        return
    if not _memory_tool_bound():
        await ws.send_json({
            "type": "memory_forget_response",
            "payload": {
                "status": "error",
                "reason": "memory_forget_tool_not_bound",
            },
        })
        return
    fact_id = payload.get("fact_id")
    query = payload.get("query")
    try:
        if fact_id is not None:
            try:
                fid = int(fact_id)
            except (TypeError, ValueError):
                raise ValueError(f"fact_id must be int, got {fact_id!r}")
            result_str = await _do_forget_by_id(fid)
        elif query:
            result_str = await _do_forget_by_query(str(query))
        else:
            await ws.send_json({
                "type": "memory_forget_response",
                "payload": {
                    "status": "error",
                    "reason": "需 fact_id 或 query 之一",
                },
            })
            return
    except Exception as exc:  # noqa: BLE001
        logger.warning("p4_ipc.memory_forget_failed", error=str(exc))
        await ws.send_json({
            "type": "memory_forget_response",
            "payload": {"status": "error", "reason": str(exc)},
        })
        return
    import json as _json
    try:
        result = _json.loads(result_str)
    except Exception:
        result = {"status": "error", "reason": "invalid tool response"}
    await ws.send_json({
        "type": "memory_forget_response",
        "payload": result,
    })


async def _handle_memory_forget_undo(
    ws: Any, payload: dict[str, Any], sc: Any,
) -> None:
    """5 秒 undo 窗口内恢复 forgotten fact。

    payload: ``{op_id: str, max_age_seconds?: float}``
    Response: ``{type: "memory_forget_undo_response", payload: {status, restored_ids: [...]}}``
    """
    facts_store = _get_service(sc, "facts_store")
    op_id = str(payload.get("op_id") or "").strip()
    if not op_id:
        await _send_error(ws, "memory_forget_undo requires op_id")
        return
    if facts_store is None:
        await ws.send_json({
            "type": "memory_forget_undo_response",
            "payload": {
                "status": "error",
                "restored_ids": [],
                "reason": "facts_store_not_registered",
            },
        })
        return
    try:
        max_age = float(payload.get("max_age_seconds") or 5.0)
    except (TypeError, ValueError):
        max_age = 5.0
    try:
        restored = await facts_store.restore_from_undo(
            op_id, max_age_seconds=max_age,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("p4_ipc.memory_forget_undo_failed", error=str(exc))
        restored = []
    status = "ok" if restored else "expired"
    await ws.send_json({
        "type": "memory_forget_undo_response",
        "payload": {
            "status": status,
            "restored_ids": [int(i) for i in restored],
        },
    })


# ---------------------------------------------------------------------------
# FP-4 WI-3.3 — Pin / Unpin fact (skip daily_decay)
# ---------------------------------------------------------------------------

async def _handle_memory_pin(
    ws: Any, payload: dict[str, Any], sc: Any, *, pinned: bool,
) -> None:
    """Pin 或 Unpin 一条 fact（pinned=True → 跳过 daily_decay 衰减）。

    verb ``memory_pin``   payload: ``{fact_id: int}``
    verb ``memory_unpin`` payload: ``{fact_id: int}``
    Response: ``{type: "memory_pin_response" | "memory_unpin_response",
                 payload: {status: "ok" | "error", reason?: str}}``
    """
    resp_type = "memory_pin_response" if pinned else "memory_unpin_response"
    facts_store = _get_service(sc, "facts_store")
    if facts_store is None:
        await ws.send_json({
            "type": resp_type,
            "payload": {
                "status": "error",
                "reason": "facts_store_not_registered",
            },
        })
        return
    raw_id = payload.get("fact_id")
    if raw_id is None:
        await ws.send_json({
            "type": resp_type,
            "payload": {"status": "error", "reason": "fact_id required"},
        })
        return
    try:
        fact_id = int(raw_id)
    except (TypeError, ValueError):
        await ws.send_json({
            "type": resp_type,
            "payload": {
                "status": "error",
                "reason": f"fact_id must be int, got {raw_id!r}",
            },
        })
        return
    try:
        await facts_store.set_pinned(fact_id, pinned)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "p4_ipc.memory_pin_failed",
            fact_id=fact_id, pinned=pinned, error=str(exc),
        )
        await ws.send_json({
            "type": resp_type,
            "payload": {"status": "error", "reason": str(exc)},
        })
        return
    await ws.send_json({
        "type": resp_type,
        "payload": {"status": "ok", "fact_id": fact_id, "pinned": pinned},
    })


# ---------------------------------------------------------------------------
# Phase 1.1.6 — 模型上下文配置卡片（SettingsPanel）
#
# get：当前 model 三层 resolve 后的窗口/compact/source + builtin 全表，
#      供前端渲染"来源链"+下拉。
# set：把单个 model 的字段覆盖深合并写回 global / project TOML。无
#      tomli_w 依赖，手写 TOML（同 provider_registry._format_*）。
# ---------------------------------------------------------------------------

# resolve() 允许 override 的字段白名单（与 model_info._OVERRIDABLE_FIELDS
# 对齐——这里独立列一份避免 p4_ipc 反向依赖 model_info 的私有常量）。
_MODEL_CTX_OVERRIDABLE = (
    "context_window",
    "effective_pct",
    "compact_at_pct",
    "recall_sweet_tokens",
)


def _builtin_table_payload() -> dict[str, Any]:
    """把 model_info.BUILTIN 摊平成 JSON 友好 dict（去掉 model/source）。"""
    from llm.model_info import BUILTIN

    out: dict[str, Any] = {}
    for name, info in BUILTIN.items():
        d = asdict(info)
        d.pop("model", None)
        d.pop("source", None)
        out[name] = d
    return out


async def _handle_model_context_get(ws: Any, payload: dict[str, Any]) -> None:
    """返回 ``model`` 三层 resolve 结果 + builtin 全表。

    payload: ``{model?: str, project_root?: str}``。缺 model → ``_default``
    （resolve 内部已兜底）。project_root 给定 → 走项目层（code mode）。
    """
    from llm.model_info import resolve

    model = str(payload.get("model") or "_default")
    proot_raw = payload.get("project_root")
    project_root = Path(proot_raw) if proot_raw else None
    try:
        info = resolve(model, project_root=project_root)
    except Exception as exc:  # noqa: BLE001 — 解析必须健壮
        logger.warning(
            "p4_ipc.model_context_get_failed",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        await ws.send_json(
            {
                "type": "model_context_get_response",
                "payload": {
                    "model": model,
                    "resolved": {},
                    "builtin": {},
                    "reason": f"resolve_error: {type(exc).__name__}",
                },
            }
        )
        return
    await ws.send_json(
        {
            "type": "model_context_get_response",
            "payload": {
                "model": info.model,
                "resolved": {
                    "context_window": info.context_window,
                    "effective_pct": info.effective_pct,
                    "compact_at_pct": info.compact_at_pct,
                    "recall_sweet_tokens": info.recall_sweet_tokens,
                    "source": info.source,
                },
                "builtin": _builtin_table_payload(),
            },
        }
    )


def _format_model_overrides_toml(models: dict[str, dict[str, Any]]) -> str:
    """把 ``{model: {field: value}}`` 渲染成 ``[models."x"]`` TOML 文本。

    手写（无 tomli_w 依赖，同 provider_registry）。字段值只可能是 int /
    float（白名单字段都是数值），不需要字符串转义。
    """
    lines: list[str] = [
        "# DeskPet per-model context overrides — Phase 1.1（context-1m-rearch）",
        "# 由 SettingsPanel「模型上下文」卡片就地编辑写回；可手编。",
        "",
    ]
    for model in sorted(models):
        fields = models[model]
        if not fields:
            continue
        lines.append(f'[models."{model}"]')
        for key in sorted(fields):
            val = fields[key]
            if isinstance(val, bool):
                lines.append(f"{key} = {'true' if val else 'false'}")
            elif isinstance(val, float):
                lines.append(f"{key} = {val}")
            else:
                lines.append(f"{key} = {int(val)}")
        lines.append("")
    return "\n".join(lines)


def _merge_model_override(
    target: Path, model: str, fields: dict[str, Any]
) -> None:
    """深合并：读现有 TOML → 仅替换该 model 的白名单字段 → 重写整文件。

    其他 model 段、该 model 未改的字段全部保留（深合并语义）。
    """
    existing: dict[str, Any] = {}
    if target.is_file():
        try:
            existing = tomllib.loads(target.read_text("utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            existing = {}
    models = dict(existing.get("models") or {})
    cur = dict(models.get(model) or {})
    for key, value in fields.items():
        if key not in _MODEL_CTX_OVERRIDABLE:
            logger.warning(
                "p4_ipc.model_context_set_ignored_field",
                model=model,
                field=key,
            )
            continue
        cur[key] = value
    models[model] = cur
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_format_model_overrides_toml(models), encoding="utf-8")


async def _handle_model_context_set(ws: Any, payload: dict[str, Any]) -> None:
    """把单个 model 的字段覆盖写回 global / project TOML（深合并）。

    payload: ``{scope: "global"|"project", model: str,
                fields: {field: value}, project_root?: str}``
    """
    scope = str(payload.get("scope") or "global")
    model = str(payload.get("model") or "")
    fields = payload.get("fields") or {}
    if not model or not isinstance(fields, dict):
        await ws.send_json(
            {
                "type": "model_context_set_ack",
                "payload": {"ok": False, "reason": "model/fields required"},
            }
        )
        return
    try:
        if scope == "project":
            proot_raw = payload.get("project_root")
            if not proot_raw:
                await ws.send_json(
                    {
                        "type": "model_context_set_ack",
                        "payload": {
                            "ok": False,
                            "reason": "project scope requires project_root",
                        },
                    }
                )
                return
            target = Path(proot_raw) / ".deskpet" / "context.toml"
        else:
            import paths as _paths

            target = _paths.user_data_dir() / "model_overrides.toml"
        _merge_model_override(target, model, fields)
    except OSError as exc:
        logger.warning(
            "p4_ipc.model_context_set_write_failed",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        await ws.send_json(
            {
                "type": "model_context_set_ack",
                "payload": {"ok": False, "reason": f"write_error: {exc}"},
            }
        )
        return
    await ws.send_json(
        {
            "type": "model_context_set_ack",
            "payload": {"ok": True, "scope": scope, "model": model},
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
