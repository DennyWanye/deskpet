# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""WI-G1 — Multi-Agent Team workflow (Companion+Code v2).

Upgrades the v1 ``agent_parallel`` (hub-and-spoke fire-and-forget) into a
**Team** model:

* **Shared task list** (SQLite WAL, per-team db file) — teammates claim
  pending tasks atomically via ``UPDATE ... WHERE status='pending'``
  with ``RETURNING``.
* **5 Teammate tools** (``team_task_create`` / ``_claim`` / ``_update``
  / ``_list`` / ``team_send_message``) exposed via a subset registry —
  never globally registered.
* **Mailbox + permission queue** persisted in SQLite tables (not
  separate JSON files) for the same crash-resume guarantee.
* **Recursion guard**: teammate tool subset NEVER includes ``agent``,
  ``agent_parallel``, or ``spawn_team``.

Public surface:

* :class:`TeamStore` — async SQLite-backed store
* :class:`TeamTask` / :class:`TeamMessage` / :class:`TeamPermissionRequest`
* :func:`spawn_team` — orchestrate N ephemeral teammate subagents
* :func:`build_teammate_tools` — produce the 5 tool schemas + handlers
"""
from deskpet.agent.team.team_store import (
    TeamMessage,
    TeamPermissionRequest,
    TeamStore,
    TeamTask,
)
from deskpet.agent.team.teammate_tools import (
    build_teammate_tools,
    FORBIDDEN_TEAMMATE_TOOLS,
)
from deskpet.agent.team.spawn_team import spawn_team

__all__ = [
    "TeamStore",
    "TeamTask",
    "TeamMessage",
    "TeamPermissionRequest",
    "build_teammate_tools",
    "FORBIDDEN_TEAMMATE_TOOLS",
    "spawn_team",
]
