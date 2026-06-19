# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""WI-1.4 — spawn_team parent-goal checkpoint tests.

Covers:
* BC: no parent_goal_text → charter identical (no "## Parent Goal", no stray blank lines)
* parent_goal_text → charter contains the goal block
* _classify_by_goal helper: splits tasks into aligned vs flagged by [off-goal] marker
"""
from __future__ import annotations

import pytest

from deskpet.agent.team.spawn_team import (
    _build_charter,
    _classify_by_goal,
)


# ---------------------------------------------------------------------------
# test_charter_bc_when_no_goal
# ---------------------------------------------------------------------------

def test_charter_bc_when_no_goal() -> None:
    """No parent_goal_text → output must NOT contain '## Parent Goal' and
    must be byte-identical to the pre-change charter (no stray blank lines
    introduced by the empty placeholder)."""
    charter = _build_charter(
        team_id="t",
        teammate_id="tm",
        initial_pool_summary="(empty)",
    )
    assert "## Parent Goal" not in charter

    # Must not have more than one consecutive blank line anywhere
    # (the empty placeholder must render as "" with no extra newline).
    import re
    assert not re.search(r"\n{3,}", charter), (
        "Empty parent_goal_block introduced extra blank lines in the charter"
    )

    # Spot-check the known structure still present.
    assert "## Workflow" in charter
    assert "## Coordination" in charter
    assert "## Initial pool" in charter
    assert "(empty)" in charter


# ---------------------------------------------------------------------------
# test_charter_includes_parent_goal
# ---------------------------------------------------------------------------

def test_charter_includes_parent_goal() -> None:
    """parent_goal_text non-empty → charter contains the goal block."""
    charter = _build_charter(
        team_id="t",
        teammate_id="tm",
        initial_pool_summary="1. task A",
        parent_goal_text="整理纪要",
    )
    assert "## Parent Goal" in charter
    assert "整理纪要" in charter
    # Block should appear BEFORE "## Workflow"
    goal_pos = charter.index("## Parent Goal")
    workflow_pos = charter.index("## Workflow")
    assert goal_pos < workflow_pos, (
        "## Parent Goal block must appear before ## Workflow"
    )
    # Sanity: the self-check phrasing should also appear
    assert "[off-goal]" in charter


# ---------------------------------------------------------------------------
# test_recycle_flags_offgoal
# ---------------------------------------------------------------------------

def test_recycle_flags_offgoal() -> None:
    """_classify_by_goal splits task dicts: those whose result contains
    '[off-goal]' go to 'flagged'; all others go to 'aligned'."""
    tasks = [
        {"task_id": "1", "result": "done ok"},
        {"task_id": "2", "result": "[off-goal] unrelated step"},
        {"task_id": "3", "result": None},
        {"task_id": "4", "result": "also done [off-goal] but partially"},
    ]
    out = _classify_by_goal(tasks)
    aligned = out["aligned"]
    flagged = out["flagged"]

    assert [t["task_id"] for t in aligned] == ["1", "3"]
    assert [t["task_id"] for t in flagged] == ["2", "4"]

    # No data dropped — all tasks appear in exactly one bucket
    assert len(aligned) + len(flagged) == len(tasks)


def test_recycle_flags_offgoal_all_aligned() -> None:
    """All tasks aligned → flagged is empty list."""
    tasks = [
        {"task_id": "a", "result": "great"},
        {"task_id": "b", "result": "also great"},
    ]
    out = _classify_by_goal(tasks)
    assert out["flagged"] == []
    assert len(out["aligned"]) == 2


def test_recycle_flags_offgoal_all_flagged() -> None:
    """All tasks flagged → aligned is empty list."""
    tasks = [
        {"task_id": "x", "result": "[off-goal] nope"},
    ]
    out = _classify_by_goal(tasks)
    assert out["aligned"] == []
    assert len(out["flagged"]) == 1


def test_recycle_flags_offgoal_empty_input() -> None:
    """Empty task list → both buckets empty."""
    out = _classify_by_goal([])
    assert out == {"aligned": [], "flagged": []}
