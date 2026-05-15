"""Regression: companion-session context hijack (2026-05-16 01:02).

Real bug (evidence in backend/userdata/data/state.db):
  - User in `default` (companion) session: "你能帮我生成一个海报图片嘛？"
  - Agent did memory_search "VPN Python CLI scaffold project" then built
    backend/vpn-cli/ (17 files). Root cause = BGE-M3 mock→real
    regression: cross-session L3 recall surfaced an 8-day-old VPN code
    project memory into an unrelated companion request + no graceful
    refuse for an unfulfillable (image-gen) request.

This test locks the fix: the two independent guards that BOTH had to
fail for the hijack to happen must now each hold.

OpenSpec 2026-05-16-companion-context-isolation, tasks.md §4.2.
"""
from __future__ import annotations

import pytest

from deskpet.memory.retriever import _session_affinity
from agent.capability_gate import classify_request, Verdict


# The actual sessions/text from the incident (state.db).
_VPN_MEM = {
    "session_id": "code-tyfbt62t",          # the 05-08 C-VPN code project
    "content": "我想要开发一个C端用户的VPN，要求就是稳定，好用，容易使用",
    "is_summary": 0,
    "tool_calls": '[{"name":"write_file"}]',  # project/task-class signal
}
_COMPANION_SID = "default"
_IMAGE_REQUEST = "你能帮我生成一个海报图片嘛？"


def test_a_vpn_code_memory_is_decayed_in_companion_session():
    """(a) The VPN code-session project memory MUST be down-weighted
    when recalled for the companion `default` session."""
    aff = _session_affinity(
        _VPN_MEM, cur_sid=_COMPANION_SID, cur_kind="companion", decay=0.15
    )
    assert aff <= 0.15, (
        f"VPN code-project memory not decayed for companion session "
        f"(affinity={aff}); cross-session hijack still possible"
    )


def test_a_strangler_fig_decay_one_restores_legacy():
    """decay=1.0 → no down-weight (rollback safety)."""
    aff = _session_affinity(
        _VPN_MEM, cur_sid=_COMPANION_SID, cur_kind="companion", decay=1.0
    )
    assert aff == 1.0


@pytest.mark.asyncio
async def test_b_image_request_is_gracefully_refused():
    """(b) The image-generation request MUST be REFUSED (no image tool
    available), so it never enters the agent loop."""
    tools = ["read_file", "write_file", "run_shell", "memory_search"]  # no image gen
    v = await classify_request(
        _IMAGE_REQUEST, available_tools=tools, enabled=True, llm_registry=None
    )
    assert v.verdict is Verdict.REFUSE, (
        f"image request not refused (verdict={v.verdict}); agent would "
        f"enter the loop and could drift again"
    )
    assert v.reason  # honest "can't" message present


@pytest.mark.asyncio
async def test_b_gate_disabled_restores_legacy_pass():
    """capability_gate_enabled=false → always PASS (rollback safety)."""
    v = await classify_request(
        _IMAGE_REQUEST, available_tools=[], enabled=False, llm_registry=None
    )
    assert v.verdict is Verdict.PASS


@pytest.mark.asyncio
async def test_c_normal_code_request_still_passes():
    """Guard must NOT over-trigger: a legit code request passes."""
    v = await classify_request(
        "重构 server/db.js 的连接池逻辑",
        available_tools=["read_file", "write_file", "run_shell"],
        enabled=True,
        llm_registry=None,
    )
    assert v.verdict is Verdict.PASS


def test_c_person_memory_still_recalled_cross_session():
    """The fix must NOT kill 'pet remembers you' — person/preference
    memory from a code session is only lightly reduced (~0.8), not
    decayed to 0.15."""
    person_mem = {
        "session_id": "code-tyfbt62t",
        "content": "我喜欢简洁的中文回答",
        "is_summary": 0,
        "tool_calls": None,
    }
    aff = _session_affinity(
        person_mem, cur_sid=_COMPANION_SID, cur_kind="companion", decay=0.15
    )
    assert aff == pytest.approx(0.8), (
        f"person/preference memory wrongly decayed (affinity={aff}); "
        f"pet would forget the user across sessions"
    )
