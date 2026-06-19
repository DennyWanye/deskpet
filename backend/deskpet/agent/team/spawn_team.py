# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""WI-G1 — ``spawn_team`` orchestrator (Companion+Code v2 Multi-Agent Team).

Replaces the v1 hub-and-spoke ``agent_parallel`` fire-and-forget pattern
with a true shared-task-list Team model:

1. Caller passes ``task_descriptions: list[str]`` — each becomes a row
   in :class:`TeamStore`'s ``tasks`` table.
2. Spawn N ephemeral teammate subagents concurrently via
   ``asyncio.gather``. Each gets:
   - The default read-only parent-tool subset (no agent / agent_parallel
     / spawn_team — :data:`FORBIDDEN_TEAMMATE_TOOLS` enforced).
   - The 5 teammate tools bound to ``(team_id, teammate_id_i)``.
   - A **Team Charter** prompt injection explaining the workflow:
     claim → work → update → repeat until pool empty.
3. Poll the store until all tasks are ``done``/``failed`` or the
   wall-clock timeout fires.
4. Return ``{ok, elapsed_ms, results: [TeamTask dict, ...]}``.

Test seam
---------

``teammate_runner`` parameter lets tests inject a fake runner that
just calls ``claim_task`` + ``update_task`` synchronously, without
spinning up a real :class:`agent.agent_loop.AgentLoop`. The default
runner delegates to :func:`_default_teammate_runner`, which builds a
SubsetRegistry from the parent tool registry and runs an AgentLoop
with the 5 teammate tools merged in.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Awaitable, Callable, Optional

from deskpet.agent.team.team_store import TeamStore, TeamTask
from deskpet.agent.team.teammate_tools import (
    FORBIDDEN_TEAMMATE_TOOLS,
    build_teammate_tools,
)

log = logging.getLogger(__name__)


# Hard caps — same spirit as agent_parallel's 2-4 spoke bounds.
_MIN_TEAMMATES = 1
_MAX_TEAMMATES = 8
_DEFAULT_NUM_TEAMMATES = 3
_DEFAULT_TIMEOUT_SECONDS = 300.0
_POLL_INTERVAL_SECONDS = 0.5


# A teammate runner accepts (charter_prompt, teammate_id, tool_set)
# and returns when that teammate has stopped (either pool empty or
# its loop hit some cap). Errors are caught in :func:`_run_teammate`.
TeammateRunner = Callable[
    [str, str, list[tuple[str, dict[str, Any], Any]]],
    Awaitable[None],
]


_TEAM_CHARTER_TEMPLATE = """\
# Team Charter (auto-injected)

You are teammate **{teammate_id}** on team **{team_id}**.

{parent_goal_block}## Workflow

1. Call `team_task_claim()` to atomically grab the next pending task.
2. If the response `task` is null, the pool is empty — **stop**: write a
   short final message and end your turn.
3. Otherwise, do the work described in `task.description`. You may use
   any tool in your tool set EXCEPT the team-protected ones (you don't
   have them).
4. When done, call `team_task_update(task_id=<your task_id>, status="done",
   result="<summary>")`. On failure call it with `status="failed"`.
5. Go back to step 1 (claim another task) until pool is empty.

## Coordination

- `team_task_list()` lets you see what other teammates are doing.
- `team_send_message(to_id="leader", content="...")` posts to mailbox.
- Do NOT try to spawn more agents — those tools are not in your set.

## Initial pool

{initial_pool_summary}
"""


def _build_charter(
    *,
    team_id: str,
    teammate_id: str,
    initial_pool_summary: str,
    parent_goal_text: str | None = None,
) -> str:
    """Render the Team Charter for one teammate. Pure — exported for tests."""
    if parent_goal_text:
        parent_goal_block = (
            "## Parent Goal (do not drift)\n"
            f"本团队服务于用户的总目标：{parent_goal_text}\n"
            "你领取的每个任务都是该目标的子步骤。完成任务前自检：这个产出是否真的推进了上述目标？\n"
            "若发现任务与总目标无关或冲突，在 result 里标注 \"[off-goal]\" 并说明。\n\n"
        )
    else:
        parent_goal_block = ""
    return _TEAM_CHARTER_TEMPLATE.format(
        team_id=team_id,
        teammate_id=teammate_id,
        initial_pool_summary=initial_pool_summary,
        parent_goal_block=parent_goal_block,
    )


def _summarise_pool(task_descriptions: list[str]) -> str:
    """Render the initial pool as a numbered list (truncated long ones)."""
    if not task_descriptions:
        return "(empty)"
    lines = []
    for i, d in enumerate(task_descriptions, 1):
        short = d if len(d) <= 200 else d[:197] + "..."
        lines.append(f"{i}. {short}")
    return "\n".join(lines)


async def _all_done(store: TeamStore, team_id: str) -> bool:
    """True iff every task is in a terminal state (done|failed)."""
    tasks = await store.list_tasks(team_id)
    if not tasks:
        return True
    return all(t.status in ("done", "failed") for t in tasks)


async def _run_teammate(
    runner: TeammateRunner,
    charter: str,
    teammate_id: str,
    tool_set: list[tuple[str, dict[str, Any], Any]],
) -> Optional[str]:
    """Run one teammate inside a try/except so a single LLM crash
    doesn't drag down the gather. Returns error str on failure, None
    on success."""
    try:
        await runner(charter, teammate_id, tool_set)
        return None
    except Exception as exc:  # noqa: BLE001 — isolate per-teammate
        log.warning(
            "spawn_team teammate %r crashed: %s: %s",
            teammate_id, type(exc).__name__, exc,
        )
        return f"{type(exc).__name__}: {exc}"


async def spawn_team(
    *,
    team_id: str,
    task_descriptions: list[str],
    num_teammates: int = _DEFAULT_NUM_TEAMMATES,
    store: TeamStore,
    teammate_runner: Optional[TeammateRunner] = None,
    parent_tool_registry: Any = None,
    llm_shim: Any = None,
    parent_session_id: str = "default",
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    parent_goal_text: str | None = None,
    parent_goal_id: str | None = None,
    task_graph_store: Any = None,
) -> dict[str, Any]:
    """Orchestrate a multi-teammate team.

    Args:
        team_id: Caller-supplied short id (used for SQLite db filename
            and metrics correlation).
        task_descriptions: One entry per initial pending task.
        num_teammates: How many ephemeral subagents to spawn.
        store: Pre-constructed :class:`TeamStore` (caller owns lifecycle
            + cleanup).
        teammate_runner: Optional test seam. When None we build the
            default runner that spins up a real AgentLoop with the
            merged tool set.
        parent_tool_registry / llm_shim / parent_session_id: Passed
            through to the default runner. Ignored when ``teammate_runner``
            is supplied.
        timeout_seconds: Wall-clock cap on the whole team. On timeout
            we return what's done so far + ``timed_out=True``.
        parent_goal_text: Optional user-level goal text injected into each
            teammate's charter as a drift-prevention anchor.
        parent_goal_id: Optional goal ID threaded into build_teammate_tools
            as ``goal_id`` so sub-agents can read/write the shared task graph.
        task_graph_store: Optional :class:`TaskGraphStore` instance.
            When provided together with ``parent_goal_id``, the two extra
            ``goal_task_list``/``goal_task_update`` tools are added to each
            teammate's tool set (BC: None → original 5-tool set).

    Returns:
        ``{ok, team_id, elapsed_ms, results: [task_dict, ...], timed_out,
        aligned: [...], flagged: [...]}``.
    """
    if not _MIN_TEAMMATES <= num_teammates <= _MAX_TEAMMATES:
        return {
            "ok": False,
            "error": (
                f"num_teammates must be {_MIN_TEAMMATES}-{_MAX_TEAMMATES},"
                f" got {num_teammates}"
            ),
        }
    if not task_descriptions:
        return {"ok": False, "error": "task_descriptions must be non-empty"}
    for i, d in enumerate(task_descriptions):
        if not isinstance(d, str) or not d.strip():
            return {
                "ok": False,
                "error": f"task_descriptions[{i}] must be a non-empty string",
            }

    # 1. Seed the task pool.
    for desc in task_descriptions:
        await store.create_task(team_id, desc)

    pool_summary = _summarise_pool(task_descriptions)

    # 2. Resolve a runner. Default = real AgentLoop; injectable = test.
    if teammate_runner is None:
        teammate_runner = _make_default_runner(
            parent_tool_registry=parent_tool_registry,
            llm_shim=llm_shim,
            parent_session_id=parent_session_id,
            store=store,
            team_id=team_id,
        )

    # 3. Spawn N teammates concurrently.
    start_ts = time.time()
    teammate_coros = []
    for i in range(num_teammates):
        tm_id = f"teammate_{i+1}"
        charter = _build_charter(
            team_id=team_id,
            teammate_id=tm_id,
            initial_pool_summary=pool_summary,
            parent_goal_text=parent_goal_text,
        )
        tool_set = build_teammate_tools(
            store=store,
            team_id=team_id,
            teammate_id=tm_id,
            task_graph_store=task_graph_store,
            goal_id=parent_goal_id,
        )
        teammate_coros.append(
            _run_teammate(teammate_runner, charter, tm_id, tool_set)
        )

    timed_out = False
    try:
        await asyncio.wait_for(
            asyncio.gather(*teammate_coros, return_exceptions=False),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError:
        timed_out = True
        log.warning(
            "spawn_team team=%s timed out after %.1fs", team_id, timeout_seconds
        )

    # 4. Collect final state.
    final_tasks = await store.list_tasks(team_id)
    elapsed_ms = int((time.time() - start_ts) * 1000)
    results = [t.to_dict() for t in final_tasks]
    classified = _classify_by_goal(results)
    return {
        "ok": True,
        "team_id": team_id,
        "elapsed_ms": elapsed_ms,
        "timed_out": timed_out,
        "results": results,
        "aligned": classified["aligned"],
        "flagged": classified["flagged"],
    }


def _classify_by_goal(tasks: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Split task dicts into aligned vs flagged based on the [off-goal] marker.

    A task is *flagged* if its ``result`` field (str) contains the literal
    substring ``"[off-goal]"``.  All other tasks are *aligned*.

    No data is dropped: every element in *tasks* appears in exactly one of
    the two output lists.  The ``results`` key in the ``spawn_team`` return
    value still carries the full unfiltered list.
    """
    aligned: list[dict[str, Any]] = []
    flagged: list[dict[str, Any]] = []
    for t in tasks:
        result_val = t.get("result")
        if isinstance(result_val, str) and "[off-goal]" in result_val:
            flagged.append(t)
        else:
            aligned.append(t)
    return {"aligned": aligned, "flagged": flagged}


def _make_default_runner(
    *,
    parent_tool_registry: Any,
    llm_shim: Any,
    parent_session_id: str,
    store: TeamStore,
    team_id: str,
) -> TeammateRunner:
    """Build a TeammateRunner that spins up a real ``agent.AgentLoop``
    with the merged subset tool registry.

    The runner is **lazy-import** so tests that only use the injected
    test seam never pull in ``agent.agent_loop``.
    """

    async def _runner(
        charter: str,
        teammate_id: str,
        team_tools: list[tuple[str, dict[str, Any], Any]],
    ) -> None:
        # Lazy imports so tests can run without the agent runtime.
        from agent.agent_loop import (  # type: ignore[import-not-found]
            AgentLoop,
            FinalEvent,
            ErrorEvent,
        )

        # Build a subset registry that combines:
        #   (a) the parent registry's read-only default tools, filtered
        #       to strip FORBIDDEN_TEAMMATE_TOOLS as defence-in-depth
        #   (b) the 5 team_* tools wired to (team_id, teammate_id)
        adapter = _TeamSubsetRegistry(
            parent_registry=parent_tool_registry,
            team_tools=team_tools,
        )

        messages = [
            {"role": "system", "content": charter},
            {
                "role": "user",
                "content": (
                    "Begin the workflow now. Loop: claim → work → update. "
                    "Stop when claim returns null."
                ),
            },
        ]

        # AgentLoop cap per teammate; team-level cap is the outer timeout.
        sub_loop = AgentLoop(
            llm_registry=llm_shim,
            tool_registry=adapter,
            max_iterations=30,
        )
        sub_sid = f"{parent_session_id}.team-{team_id}.{teammate_id}"

        async for ev in sub_loop.run(messages, session_id=sub_sid):
            if isinstance(ev, FinalEvent):
                return
            if isinstance(ev, ErrorEvent):
                # Don't raise — teammate exits cleanly, _run_teammate
                # already isolates exceptions but this avoids an extra
                # warning row.
                log.info(
                    "team teammate %s ended on ErrorEvent: %s",
                    teammate_id, getattr(ev, "reason", "?"),
                )
                return

    return _runner


class _TeamSubsetRegistry:
    """Read-only wrapper exposing parent's default read-only tools
    PLUS the 5 team_* tools for one teammate.

    Mirrors the shape used by :class:`agent.agent_loop.AgentLoop`
    (``schemas()`` + ``execute_tool(name, args, ...)``).
    Strict filter: any parent tool whose name is in
    :data:`FORBIDDEN_TEAMMATE_TOOLS` is hidden AND blocked at execute
    time — defence-in-depth.
    """

    _DEFAULT_READONLY = frozenset(
        {"read_file", "list_directory", "glob", "grep", "web_search"}
    )

    def __init__(
        self,
        *,
        parent_registry: Any,
        team_tools: list[tuple[str, dict[str, Any], Any]],
    ) -> None:
        self._parent = parent_registry
        # Map name → (schema, handler) for the team tools.
        self._team: dict[str, tuple[dict[str, Any], Any]] = {
            name: (schema, handler) for name, schema, handler in team_tools
        }
        # Strict allowlist for parent passthrough.
        self._parent_allowed = (
            self._DEFAULT_READONLY - FORBIDDEN_TEAMMATE_TOOLS
        )

    def schemas(self, enabled_toolsets: Any = None) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        # Parent read-only tools (if registry provides .schemas).
        if self._parent is not None and hasattr(self._parent, "schemas"):
            try:
                parent_schemas = self._parent.schemas(
                    enabled_toolsets=enabled_toolsets
                )
            except TypeError:
                # registries that don't take kwargs
                parent_schemas = self._parent.schemas()
            for s in parent_schemas:
                fn = (s.get("function") if isinstance(s, dict) else None) or {}
                name = fn.get("name") or s.get("name")
                if name in self._parent_allowed:
                    out.append(s)
        # Team tools — wrap to OpenAI function-call envelope.
        for name, (schema, _handler) in self._team.items():
            out.append({"type": "function", "function": schema})
        return out

    async def execute_tool(self, *args: Any, **kwargs: Any) -> str:
        # AgentLoop typically calls execute_tool(name, args_dict, ...).
        name: Optional[str] = None
        if args:
            name = args[0]
        else:
            name = kwargs.get("name") or kwargs.get("tool_name")
        if name in FORBIDDEN_TEAMMATE_TOOLS:
            raise PermissionError(
                f"tool {name!r} is forbidden for teammates"
            )
        if name in self._team:
            tool_args = (
                args[1] if len(args) > 1 else kwargs.get("args", {})
            )
            task_id = (
                args[2] if len(args) > 2 else kwargs.get("task_id", "")
            )
            _schema, handler = self._team[name]
            return await handler(tool_args, task_id)
        if name in self._parent_allowed and self._parent is not None:
            return await self._parent.execute_tool(*args, **kwargs)
        raise PermissionError(
            f"tool {name!r} not in teammate's allowed set"
        )


__all__ = [
    "spawn_team",
    "TeammateRunner",
    "_build_charter",
    "_classify_by_goal",
    "_summarise_pool",
    "_TeamSubsetRegistry",
]
