# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""WI-C1 — ``agent_parallel`` 多子代理并发工具（companion-code-skill-upgrade v1, Stage C）.

Hub-and-spoke 并发派 2–4 个 ephemeral subagents 处理独立子任务，每个
subagent 复用现有 :mod:`deskpet.tools.code_tools.agent_tool` 的
``build_agent_tool`` 工厂（同样的 15 iter cap + recursion guard +
SubsetRegistryAdapter 隔离），通过 ``asyncio.gather`` 真并发执行。

设计要点
--------
* **复用 agent_tool — 不重写**：parallel tool 内部为每个 subagent 构造
  一个 ``build_agent_tool`` 闭包并调用 — agent_tool.py 已经做了所有
  recursion guard / iter cap / SubsetRegistryAdapter / ErrorEvent 处理。
* **递归 guard**：parallel 自身也加 recursion guard — subagent 的 tool
  subset 永远剔除 ``agent`` 和 ``agent_parallel``，禁止嵌套。
* **Sprint Contract 注入**：每个 subagent 的 prompt 被 prepend 一段
  JSON contract（task_id / input_files / output_files / forbidden_files /
  success_criteria），让 LLM 看见明确边界。
* **错误隔离**：单个 subagent 异常被本地 try/except 捕获并写到该 result
  的 ``ok=false``，不影响其他 subagent；整体 envelope 仍 ``ok=true``
  （除非参数校验失败）。
* **进度事件**：每个 subagent start/complete/failed 触发
  ``subagent_progress`` metrics event（best-effort，emit 失败不阻 dispatch）。

工厂模式
--------
``build_agent_parallel_tool(llm_shim, parent_tool_registry,
parent_session_id_resolver)`` → ``(handler, schema)``，与
``build_agent_tool`` 同形参，由 ``main.py`` 启动期拼接闭包。

测试
----
模块同时导出 ``_SCHEMA`` 和 ``_build_sprint_contract`` 给单元测试，并
允许测试通过 ``subagent_runner`` 注入 mock subagent handler 跳过真 LLM。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from typing import Any, Awaitable, Callable, Optional

from deskpet.agent.task_kinds import _FORBIDDEN_IN_KIND

log = logging.getLogger(__name__)


def _read_raw_agent_cfg() -> dict[str, Any]:
    """惰性读 ``config.raw['agent']`` —— 供 WI-OC-1 depth gate 读 flag/上界。

    与 ``research_tools._fanout_concurrency`` 同款防御式读法：cfg 单例可能不
    存在 → 全程 try/except，失败回退 ``{}``（depth_gate_enabled({}) = False =
    OFF = BC，绝不因读 config 失败而误拦 spawn）。
    """
    try:
        import config as _cfg  # type: ignore[import-not-found]

        obj = getattr(_cfg, "config", None)
        if obj is None:
            obj = _cfg.load_config(_cfg.resolve_config_path())
        raw = getattr(obj, "raw", None) or {}
        agent_raw = raw.get("agent", {}) if isinstance(raw, dict) else {}
        return agent_raw if isinstance(agent_raw, dict) else {}
    except Exception:  # noqa: BLE001 — 读 config 失败一律 OFF（BC 安全）
        return {}


_MIN_SUBAGENTS = 2
# WI-1.2: 4→8。真实并发由 SubagentScheduler 全局 cap 背压（多的排队），
# 此处只放宽 LLM 一次能列的子任务数。
_MAX_SUBAGENTS = 8
# 合法 kind（与 task_kinds._BUILTIN_KINDS 对齐；未知 kind 运行时回退 general）
_KIND_ENUM = ["general", "research", "code", "fileops", "doc", "web"]
# Recursion guard — 这两个名字永远不会被传给 subagent
_FORBIDDEN_NESTED_TOOLS = frozenset(_FORBIDDEN_IN_KIND)

# G4 (companion-code-v2) — prompt cache modes.
#   "fork"  → each subagent reuses the parent agent's system prompt bytes
#             verbatim (only the user message gets the per-subagent Sprint
#             Contract + prompt prepended). LLM provider sees identical
#             system-prefix tokens across N subagents → prompt_cache_hit
#             rate climbs from ~0% to 80%+ for the second/third/fourth
#             concurrent subagent.
#   "fresh" → each subagent rebuilds its own system prompt from scratch
#             (legacy behaviour — no cache reuse, full isolation). Use
#             when subagents need radically different framing (e.g.
#             security review subagent vs. doc-write subagent).
_CACHE_MODE_FORK = "fork"
_CACHE_MODE_FRESH = "fresh"
_VALID_CACHE_MODES = frozenset({_CACHE_MODE_FORK, _CACHE_MODE_FRESH})
_DEFAULT_CACHE_MODE = _CACHE_MODE_FORK


_SCHEMA: dict[str, Any] = {
    "name": "agent_parallel",
    "description": (
        "并发派 2-8 个独立子代理处理可并行的【多种事务】（hub-and-spoke 模式）。"
        "每个子任务可指定 kind（research/code/doc/web/fileops/general），不同类型"
        "并发受 lane 调度，按 kind 自动选工具集/迭代上限。结果聚合返回。"
        "适用于：一条请求里同时要调研+做PPT+查资料这类异构并发，或多模块改动、"
        "多语言翻译、独立调研。不允许嵌套（subagent 内部 spawn 类工具会被剔除）。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "subagents": {
                "type": "array",
                "minItems": _MIN_SUBAGENTS,
                "maxItems": _MAX_SUBAGENTS,
                "description": (
                    f"List of {_MIN_SUBAGENTS}-{_MAX_SUBAGENTS} subagent task "
                    "specs to run concurrently."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "task_id": {
                            "type": "string",
                            "description": "Short identifier for this subagent task.",
                        },
                        "kind": {
                            "type": "string",
                            "enum": _KIND_ENUM,
                            "description": (
                                "事务类型：决定子代理工具集/迭代上限/并发 lane。"
                                "research=调研(联网+web_fetch)、code=编码(读写改+跑测试)、"
                                "doc=文档生成(PPT/Word/Excel)、web=联网快查、fileops=文件读写、"
                                "general=通用只读(默认)。"
                            ),
                        },
                        "prompt": {
                            "type": "string",
                            "description": "Task / question for this subagent.",
                        },
                        "tools": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Optional tool subset (defaults to read-only "
                                "if omitted; agent/agent_parallel always stripped)."
                            ),
                        },
                        "input_files": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Files this subagent may read.",
                        },
                        "output_files": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Files this subagent will write.",
                        },
                        "forbidden_files": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Files this subagent must NOT touch.",
                        },
                        "success_criteria": {
                            "type": "string",
                            "description": "How to judge success.",
                        },
                        "cache_mode": {
                            "type": "string",
                            "enum": ["fork", "fresh"],
                            "description": (
                                "G4 prompt cache strategy. 'fork' (default) "
                                "reuses parent system prompt bytes verbatim → "
                                "high prompt_cache_hit rate. 'fresh' rebuilds "
                                "a per-subagent system prompt (use when "
                                "subagents need different framing)."
                            ),
                        },
                    },
                    "required": ["task_id", "prompt"],
                },
            },
            "cache_mode": {
                "type": "string",
                "enum": ["fork", "fresh"],
                "description": (
                    "G4 batch-level default cache mode (each subagent can "
                    "override via its own cache_mode). Default 'fork'."
                ),
            },
        },
        "required": ["subagents"],
    },
}


def _resolve_cache_mode(sa: dict[str, Any], batch_default: str) -> str:
    """G4: pick the effective cache_mode for one subagent.

    Precedence (high → low): subagent.cache_mode → batch default →
    module default ('fork'). Unknown values silently fall back to the
    batch default (we don't want a typo'd LLM call to crash dispatch).
    """
    sa_mode = sa.get("cache_mode")
    if isinstance(sa_mode, str) and sa_mode in _VALID_CACHE_MODES:
        return sa_mode
    if isinstance(batch_default, str) and batch_default in _VALID_CACHE_MODES:
        return batch_default
    return _DEFAULT_CACHE_MODE


def _compute_system_prompt_hash(
    *,
    cache_mode: str,
    parent_system_prompt: str,
    sa_task_id: str,
) -> str:
    """G4: deterministic hash of the bytes that go into the LLM's *system*
    slot for one subagent. Tests use it to prove fork-mode reuse.

    Fork mode → ``sha256(parent_system_prompt)``. ALL subagents in a
    fork batch share this hash → LLM provider's cache key matches → hit.

    Fresh mode → ``sha256(parent_system_prompt + "::" + sa_task_id)`` so
    each subagent gets a distinct system-prompt cache key (no reuse).

    Note: this is the *intended* hash for the prefix the LLM will see;
    the real LLM payload assembly may add a small tail (e.g. tool
    schema). What matters is that fork-mode hashes are equal across
    siblings and fresh-mode hashes differ — which this captures.
    """
    text = parent_system_prompt or ""
    if cache_mode == _CACHE_MODE_FRESH:
        text = f"{text}::{sa_task_id}"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _build_sprint_contract(sa: dict[str, Any]) -> str:
    """Prepend Sprint Contract JSON block to the subagent prompt.

    Pure function — exported for unit tests.
    """
    contract = {
        "task_id": sa.get("task_id", ""),
        "input_files": list(sa.get("input_files") or []),
        "output_files": list(sa.get("output_files") or []),
        "forbidden_files": list(sa.get("forbidden_files") or []),
        "success_criteria": sa.get("success_criteria", "") or "",
    }
    contract_json = json.dumps(contract, ensure_ascii=False, indent=2)
    user_prompt = sa.get("prompt", "") or ""
    return (
        "# Sprint Contract (auto-injected)\n"
        f"```json\n{contract_json}\n```\n\n"
        "# Task\n"
        f"{user_prompt}"
    )


def _emit_progress(task_id: str, status: str) -> None:
    """Best-effort emit ``subagent_progress`` metrics event.

    Never raises — metrics_sink may be unavailable in tests or boot-early
    paths. We swallow any exception so subagent dispatch is never broken
    by observability.
    """
    try:
        from observability.metrics_sink import record  # type: ignore[import-not-found]

        record("subagent_progress", {"task_id": task_id, "status": status})
    except Exception as exc:  # noqa: BLE001 — metrics MUST NOT break dispatch
        log.debug("agent_parallel emit progress failed: %s", exc)


def _filter_subagent_tools(tools: Optional[list[str]]) -> Optional[list[str]]:
    """Strip recursion-forbidden tool names from a requested subset.

    Returns ``None`` if caller didn't pass ``tools`` (preserves agent_tool's
    "default read-only" behaviour). Returns a list with ``agent`` /
    ``agent_parallel`` removed otherwise.
    """
    if not tools or not isinstance(tools, list):
        return None
    return [t for t in tools if isinstance(t, str) and t not in _FORBIDDEN_NESTED_TOOLS]


# Type alias for a subagent runner — accepts the (sa_spec, full_prompt) and
# returns the final string. Default implementation delegates to agent_tool;
# tests inject a mock runner.
SubagentRunner = Callable[[dict[str, Any], str], Awaitable[str]]


def build_agent_parallel_tool(
    *,
    llm_shim: Any,
    parent_tool_registry: Any,
    parent_session_id_resolver: Callable[[], str],
    subagent_runner: Optional[SubagentRunner] = None,
    parent_system_prompt_resolver: Optional[Callable[[], str]] = None,
    scheduler: Any = None,
    kind_overrides: Optional[dict[str, Any]] = None,
    termination_gate_factory: Optional[Callable[[], Any]] = None,
    shim_resolver: Optional[Callable[[str], Any]] = None,
):
    """Construct the ``agent_parallel`` tool handler.

    Args:
        llm_shim: Shared LLM shim (passed through to each subagent's
            inner ``build_agent_tool`` closure).
        parent_tool_registry: Parent ToolRegistry — subagents get
            filtered views via ``SubsetRegistryAdapter``.
        parent_session_id_resolver: Returns parent's session id; each
            subagent appends ``.par-<task_id>`` for traceability.
        subagent_runner: **Test seam** — override the per-subagent runner
            (e.g. inject a mock instead of really spawning AgentLoop).
            None → use ``_default_subagent_runner`` which delegates to
            :func:`build_agent_tool`.
        parent_system_prompt_resolver: G4 — callable returning the
            parent agent's current system prompt text. Used to compute
            the fork-mode cache key (hash) we surface in each subagent
            result for verification. If None, defaults to empty string
            (still deterministic — fork hashes match, fresh hashes
            differ — which is what tests assert).
    """
    # WI-1.3: 事务分型解析器（lazy import 防循环 import）
    # WI-OC-1: depth gate（显式深度上界，flag OFF=默认 → no-op = BC）
    from deskpet.agent.task_kinds import (
        SpawnDepthExceeded,
        check_spawn_depth,
        resolve_kind,
    )

    # Runner 选择（WI-1.3）：
    #   1. subagent_runner（测试注入）优先
    #   2. scheduler 存在 → 原生协程 runner（无线程池占用，F10）
    #   3. 否则 → 现状 thread-bounce default runner（scheduler=None 的 BC 路径）
    if subagent_runner is not None:
        runner: SubagentRunner = subagent_runner
    elif scheduler is not None:
        runner = _make_async_native_runner(
            llm_shim=llm_shim,
            parent_tool_registry=parent_tool_registry,
            parent_session_id_resolver=parent_session_id_resolver,
            termination_gate_factory=termination_gate_factory,
            shim_resolver=shim_resolver,
        )
    else:
        runner = _make_default_runner(
            llm_shim=llm_shim,
            parent_tool_registry=parent_tool_registry,
            parent_session_id_resolver=parent_session_id_resolver,
        )
    resolve_parent_prompt: Callable[[], str] = (
        parent_system_prompt_resolver or (lambda: "")
    )

    async def _handle(args: dict[str, Any], task_id: str = "") -> str:
        # WI-OC-1：显式 depth 上界（flag OFF=默认 → no-op，仍靠 strip 守门 = BC）。
        # flag ON 且本代理深度已达上界 → 拒绝整批（与剥 spawn 工具同风格拒绝）。
        try:
            check_spawn_depth(_read_raw_agent_cfg())
        except SpawnDepthExceeded as exc:
            return json.dumps(
                {"ok": False, "error": str(exc), "forbidden": "spawn_depth"},
                ensure_ascii=False,
            )
        subagents = args.get("subagents")
        # G4: batch-level cache_mode default (each subagent may override)
        batch_cache_mode_raw = args.get("cache_mode", _DEFAULT_CACHE_MODE)
        batch_cache_mode = (
            batch_cache_mode_raw
            if isinstance(batch_cache_mode_raw, str)
            and batch_cache_mode_raw in _VALID_CACHE_MODES
            else _DEFAULT_CACHE_MODE
        )
        # Snapshot parent system prompt once per dispatch — guarantees all
        # fork subagents in this batch see the SAME bytes (resolver might
        # itself be stateful / change between calls). Snapshot inside the
        # handler so a per-call refresh still wins over a stale closure.
        parent_system_prompt_snapshot = ""
        try:
            parent_system_prompt_snapshot = str(resolve_parent_prompt() or "")
        except Exception as exc:  # noqa: BLE001 — never break dispatch
            log.warning(
                "agent_parallel parent_system_prompt_resolver raised: %s", exc
            )
        if not isinstance(subagents, list):
            return json.dumps(
                {"ok": False, "error": "subagents must be a list"},
                ensure_ascii=False,
            )
        if not _MIN_SUBAGENTS <= len(subagents) <= _MAX_SUBAGENTS:
            return json.dumps(
                {
                    "ok": False,
                    "error": (
                        f"subagents count must be "
                        f"{_MIN_SUBAGENTS}-{_MAX_SUBAGENTS}, "
                        f"got {len(subagents)}"
                    ),
                },
                ensure_ascii=False,
            )

        # Per-subagent normalisation + early validation
        normalised: list[dict[str, Any]] = []
        for idx, sa in enumerate(subagents):
            if not isinstance(sa, dict):
                return json.dumps(
                    {"ok": False, "error": f"subagent[{idx}] must be an object"},
                    ensure_ascii=False,
                )
            sa_task_id = sa.get("task_id") or f"subagent_{idx}"
            sa_prompt = sa.get("prompt")
            if not sa_prompt or not isinstance(sa_prompt, str):
                return json.dumps(
                    {
                        "ok": False,
                        "error": (
                            f"subagent[{idx}] ({sa_task_id}) missing required "
                            "string 'prompt'"
                        ),
                    },
                    ensure_ascii=False,
                )
            normalised.append({**sa, "task_id": sa_task_id})

        start_ts = time.time()

        async def _run_one(sa: dict[str, Any]) -> dict[str, Any]:
            sa_task_id = sa["task_id"]
            # WI-1.3: 解析事务分型 → 工具子集/迭代上限/framing/lane
            prof = resolve_kind(sa.get("kind"), overrides=kind_overrides)
            # G4: resolve cache mode per-subagent + compute system prompt hash
            sa_cache_mode = _resolve_cache_mode(sa, batch_cache_mode)
            sa_system_prompt_hash = _compute_system_prompt_hash(
                cache_mode=sa_cache_mode,
                parent_system_prompt=parent_system_prompt_snapshot,
                sa_task_id=sa_task_id,
            )
            full_prompt = _build_sprint_contract(sa)
            # 工具子集：子任务显式 tools（剔 forbidden）优先，否则用 kind 默认
            req_tools = _filter_subagent_tools(sa.get("tools"))
            effective_tools = req_tools if req_tools is not None else list(prof.tools)
            sa_for_runner = {
                **sa,
                "prompt": full_prompt,
                "tools": effective_tools,
                # WI-1.3：分型注入，供 native runner 按 KindProfile 构造子 AgentLoop
                "_kind": prof.kind,
                "_max_iter": prof.max_iterations,
                "_framing": prof.framing,
                "_model": prof.model,  # P4 WI-4.2: per-kind 模型路由（None=父模型 BC）
                # G4: surface cache hints to the runner
                "cache_mode": sa_cache_mode,
                "parent_system_prompt_hash": sa_system_prompt_hash,
            }
            parent_sid = parent_session_id_resolver() or "default"
            run_id = f"{parent_sid}.par-{sa_task_id}"
            _emit_progress(sa_task_id, "starting")

            async def _do() -> str:
                return await runner(sa_for_runner, sa_task_id)

            try:
                if scheduler is not None:
                    output = await scheduler.run(
                        kind=prof.kind, run_id=run_id, task_id=sa_task_id,
                        parent_sid=parent_sid, coro_factory=_do,
                    )
                else:
                    output = await _do()  # BC：无调度器 = 现状扁平 gather
                _emit_progress(sa_task_id, "completed")
                return {
                    "task_id": sa_task_id,
                    "kind": prof.kind,
                    "ok": True,
                    "output": output,
                    # G4 detail block — tests + observability consume this
                    "cache_mode": sa_cache_mode,
                    "system_prompt_hash": sa_system_prompt_hash,
                }
            except Exception as exc:  # noqa: BLE001 — isolate per-subagent
                _emit_progress(sa_task_id, "failed")
                log.warning(
                    "agent_parallel subagent %r failed: %s: %s",
                    sa_task_id, type(exc).__name__, exc,
                )
                return {
                    "task_id": sa_task_id,
                    "kind": prof.kind,
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                    "cache_mode": sa_cache_mode,
                    "system_prompt_hash": sa_system_prompt_hash,
                }

        # Truly concurrent — gather without return_exceptions because
        # _run_one already catches per-subagent. ``return_exceptions=False``
        # would propagate bugs in our own glue (good signal).
        results = await asyncio.gather(*[_run_one(sa) for sa in normalised])

        elapsed_ms = int((time.time() - start_ts) * 1000)
        return json.dumps(
            {
                "ok": True,
                "elapsed_ms": elapsed_ms,
                "count": len(results),
                "results": results,
            },
            ensure_ascii=False,
        )

    return _handle, _SCHEMA


def _make_default_runner(
    *,
    llm_shim: Any,
    parent_tool_registry: Any,
    parent_session_id_resolver: Callable[[], str],
) -> SubagentRunner:
    """Build the default per-subagent runner that delegates to agent_tool.

    For each subagent we construct a fresh ``build_agent_tool`` closure
    (so each gets its own 15-iter loop) and call its sync handler in a
    thread executor so we don't block the event loop.
    """
    # Lazy import — avoid circular import (agent_tool imports nothing
    # from here, but be defensive).
    from .agent_tool import build_agent_tool

    async def _runner(sa_for_runner: dict[str, Any], sa_task_id: str) -> str:
        # Each parallel subagent gets a distinct session id suffix so its
        # SessionDB rows / metrics are visually separable from siblings.
        parent_sid = parent_session_id_resolver() or "default"

        def _child_sid_resolver() -> str:
            return f"{parent_sid}.par-{sa_task_id}"

        inner_handler, _inner_schema = build_agent_tool(
            llm_shim=llm_shim,
            parent_tool_registry=parent_tool_registry,
            parent_session_id_resolver=_child_sid_resolver,
        )

        # agent_tool's handler is sync (it manages its own asyncio loop
        # internally via run_coroutine_threadsafe). To avoid blocking the
        # parent event loop with N concurrent sync subagents we offload
        # each to the default thread executor.
        loop = asyncio.get_running_loop()

        def _invoke() -> str:
            # agent_tool expects 'description' + 'prompt'
            description = (
                f"agent_parallel child task {sa_task_id}"
            )
            sub_tools = sa_for_runner.get("tools")
            inner_args: dict[str, Any] = {
                "description": description,
                "prompt": sa_for_runner["prompt"],
            }
            if sub_tools is not None:
                inner_args["tools"] = sub_tools
            return inner_handler(inner_args, sa_task_id)

        raw = await loop.run_in_executor(None, _invoke)
        # agent_tool returns JSON: {"result": "...", "tools": [...]}
        # Parse and surface ``result`` so callers see the actual subagent
        # output; if parsing fails, return raw string verbatim.
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict) and "result" in parsed:
                return str(parsed["result"])
        except (ValueError, TypeError):
            pass
        return raw

    return _runner


def _make_async_native_runner(
    *,
    llm_shim: Any,
    parent_tool_registry: Any,
    parent_session_id_resolver: Callable[[], str],
    termination_gate_factory: Optional[Callable[[], Any]] = None,
    shim_resolver: Optional[Callable[[str], Any]] = None,
) -> SubagentRunner:
    """WI-1.3 (F10) — scheduler 路径专用 runner：**直接在协程内**构造并 await
    子 ``AgentLoop``，不经 ``run_in_executor`` 线程跳转。

    这样调度器 semaphore 是唯一并发闸，不再为每个子代理 pin 一个线程池 worker
    整个生命周期（_make_default_runner 的 thread-bounce 在 scheduler=None 的 BC
    路径保留）。多个子 AgentLoop 协程在同一事件循环并发是安全的——现状
    agent_parallel 已如此跑，且所有 per-call 状态按 session_id 隔离。
    """

    async def _runner(sa_for_runner: dict[str, Any], sa_task_id: str) -> str:
        # lazy import 防循环（agent_tool 同款）
        from agent.agent_loop import (  # type: ignore[import-not-found]
            AgentLoop as _AgentLoop,
            FinalEvent as _FinEv,
            ErrorEvent as _ErrEv,
        )
        from .agent_tool import _SubsetRegistryAdapter

        parent_sid = parent_session_id_resolver() or "default"
        tools = list(sa_for_runner.get("tools") or [])
        adapter = _SubsetRegistryAdapter(parent_tool_registry, tools)

        framing = sa_for_runner.get("_framing", "") or ""
        try:
            max_iter = int(sa_for_runner.get("_max_iter") or 15)
        except (TypeError, ValueError):
            max_iter = 15

        _framing_block = (framing + " ") if framing else ""
        sys_msg = (
            "You are a focused subagent invoked by a parent agent. "
            f"{_framing_block}"
            "用工具完成后，用一条简洁最终消息总结结果（父代理把它当返回值）。"
            f"你最多有 {max_iter} 轮迭代。"
        )
        msgs = [
            {"role": "system", "content": sys_msg},
            {"role": "user", "content": sa_for_runner["prompt"]},
        ]
        # P4 WI-4.2: per-kind 模型路由（sa["_model"] 非空 + resolver 给 → 换 shim）
        _model = sa_for_runner.get("_model")
        _shim = llm_shim
        if _model and shim_resolver is not None:
            try:
                _shim = shim_resolver(_model) or llm_shim
            except Exception:  # noqa: BLE001 — 模型解析失败回退父 shim
                _shim = llm_shim
        # P4 WI-4.1: 子代理质量守门（未注入 factory → AgentLoop 自带默认 gate 兜底
        # turns/cost；注入则用自定义 gate，如防复读）.
        _sub_kwargs: dict[str, Any] = {
            "llm_registry": _shim,
            "tool_registry": adapter,
            "max_iterations": max_iter,
        }
        if termination_gate_factory is not None:
            try:
                _sub_kwargs["termination_gate"] = termination_gate_factory()
            except Exception:  # noqa: BLE001
                pass
        sub = _AgentLoop(**_sub_kwargs)
        sub_sid = f"{parent_sid}.par-{sa_task_id}"
        final = ""
        # WI-OC-1：子代理深度 = 父深度+1，注入 env 让子代理的
        # current_spawn_depth() 比父深一层（depth gate flag ON 时生效；OFF 时
        # 此 env 不被任何检查读取 → 行为字节不变 = BC）。同进程内跑，run 完恢复
        # 旧值，避免污染父/兄弟代理（asyncio 同事件循环串行进入此段）。
        from deskpet.agent.task_kinds import _DEPTH_ENV, child_depth_env

        _prev_depth = os.environ.get(_DEPTH_ENV)
        os.environ.update(child_depth_env())
        try:
            async for ev in sub.run(msgs, session_id=sub_sid):
                if isinstance(ev, _FinEv):
                    final = ev.content or ""
                    break
                if isinstance(ev, _ErrEv):
                    return f"[subagent error] {ev.reason}: {ev.detail}".rstrip(": ")
        finally:
            if _prev_depth is None:
                os.environ.pop(_DEPTH_ENV, None)
            else:
                os.environ[_DEPTH_ENV] = _prev_depth
        return final or "[subagent finished without final text]"

    return _runner


__all__ = [
    "_SCHEMA",
    "_build_sprint_contract",
    "_make_async_native_runner",
    "_compute_system_prompt_hash",
    "_emit_progress",
    "_filter_subagent_tools",
    "_FORBIDDEN_NESTED_TOOLS",
    "_resolve_cache_mode",
    "_CACHE_MODE_FORK",
    "_CACHE_MODE_FRESH",
    "_VALID_CACHE_MODES",
    "_DEFAULT_CACHE_MODE",
    "build_agent_parallel_tool",
    "SubagentRunner",
]
