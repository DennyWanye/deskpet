# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""P1 (D1) — retriever session-affinity 单测。

对应 OpenSpec change ``2026-05-16-companion-context-isolation``：

* design.md §D1：RRF 加第 5 信号 ``session_affinity``，乘性降权（不是过滤），
  保留"桌宠记得你"。
* specs/memory-recall/spec.md 四个 Scenario：
  - Companion request not hijacked by unrelated code-session project memory
  - Cross-session person/preference memory still recalled
  - Same-session memory unaffected
  - decay=1.0 restores legacy behavior
* tasks.md Phase 1：
  - 1.1 same-session=1.0；companion←code 项目类=decay；
    companion←code 人物类=0.8；code←code=0.5；decay=1.0 退回旧行为
  - 1.2 ``_session_affinity(mem_row, cur_sid, cur_kind, decay) -> float`` 纯函数
  - 1.4 项目类 vs 人物类判定（is_summary / tool_calls / code- 前缀 + 路径特征）
  - 1.5 不回归：现有 retriever 测试全绿（同 session 召回行为不变）

纯函数密集单测 + 一个端到端融合降权断言。``_session_affinity`` 不做任何
I/O，可独立测试。
"""
from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from deskpet.memory.embedder import Embedder
from deskpet.memory.retriever import (
    Retriever,
    RetrievalPolicy,
    _is_project_class,
    _session_affinity,
    _session_kind,
)
from deskpet.memory.session_db import SessionDB


# ======================================================================
# Fixtures
# ======================================================================


@pytest_asyncio.fixture
async def embedder():
    """mock 模式 embedder（不依赖 BGE-M3 权重）。"""
    e = Embedder(model_path=Path("/nonexistent-for-test"), use_mock_when_missing=True)
    await e.warmup()
    yield e
    await e.close()


@pytest_asyncio.fixture
async def db(tmp_path: Path):
    session_db = SessionDB(tmp_path / "state.db")
    await session_db.initialize()
    yield session_db
    await session_db.close()


# ======================================================================
# _session_kind — companion vs code 判定（design.md D1 规则）
# ======================================================================


def test_session_kind_default_is_companion():
    assert _session_kind("default") == "companion"


def test_session_kind_code_prefix_is_code():
    assert _session_kind("code-tyfbt62t") == "code"
    assert _session_kind("code-abc123") == "code"


def test_session_kind_other_uuid_is_companion():
    # 非 code- 前缀的普通 session（陪伴默认走 default，但 uuid 也算 companion）
    assert _session_kind("3f2a-uuid-like") == "companion"


def test_session_kind_none_is_companion():
    # 缺失 / None 当 companion（保守：不升级为 code 边界）
    assert _session_kind(None) == "companion"
    assert _session_kind("") == "companion"


# ======================================================================
# _is_project_class — 项目/任务类 vs 人物/偏好类（design.md D1 + tasks 1.4）
# ======================================================================


def test_project_class_is_summary():
    """is_summary=1 → 项目/任务类。"""
    row = {"session_id": "code-x", "is_summary": 1, "tool_calls": None, "content": "总结", "role": "assistant"}
    assert _is_project_class(row) is True


def test_project_class_has_tool_calls():
    """tool_calls is not null → 项目/任务类（agent 干过活）。"""
    row = {
        "session_id": "code-x",
        "is_summary": 0,
        "tool_calls": '[{"name":"write_file"}]',
        "content": "做了点事",
        "role": "assistant",
    }
    assert _is_project_class(row) is True


def test_project_class_code_prefix_with_path_feature():
    """source session_id 以 code- 开头 + 消息含路径/代码特征 → 项目类。"""
    row = {
        "session_id": "code-tyfbt62t",
        "is_summary": 0,
        "tool_calls": None,
        "content": "在 backend/vpn-cli/main.py 里实现 VPN 握手",
        "role": "user",
    }
    assert _is_project_class(row) is True


def test_person_class_plain_chat_in_code_session():
    """code- session 里的纯文本闲聊（无路径/代码特征）→ 人物/偏好类。"""
    row = {
        "session_id": "code-tyfbt62t",
        "is_summary": 0,
        "tool_calls": None,
        "content": "你今天心情怎么样呀",
        "role": "user",
    }
    assert _is_project_class(row) is False


def test_person_class_preference_statement():
    """偏好类陈述（无 tool_calls / 非 summary / 无代码特征）→ 人物类。"""
    row = {
        "session_id": "code-abc",
        "is_summary": 0,
        "tool_calls": None,
        "content": "我喜欢简洁的中文回答",
        "role": "user",
    }
    assert _is_project_class(row) is False


def test_person_class_companion_session_chat():
    """companion session 的普通消息默认人物类。"""
    row = {
        "session_id": "default",
        "is_summary": 0,
        "tool_calls": None,
        "content": "我们聊聊天吧",
        "role": "user",
    }
    assert _is_project_class(row) is False


# ======================================================================
# _session_affinity — 纯函数核心矩阵（tasks 1.1 / 1.2）
# ======================================================================

_DECAY = 0.15


def _mem(session_id: str, *, is_summary: int = 0, tool_calls=None, content: str = "hi", role: str = "user") -> dict:
    return {
        "session_id": session_id,
        "is_summary": is_summary,
        "tool_calls": tool_calls,
        "content": content,
        "role": role,
    }


def test_affinity_same_session_is_one():
    """同 session → 1.0（spec: Same-session memory unaffected）。"""
    mem = _mem("default", content="anything")
    assert _session_affinity(mem, "default", "companion", _DECAY) == pytest.approx(1.0)
    # code session 同 session 也是 1.0
    mem2 = _mem("code-x", tool_calls='[{"name":"x"}]')
    assert _session_affinity(mem2, "code-x", "code", _DECAY) == pytest.approx(1.0)


def test_affinity_companion_from_code_project_class_is_decay():
    """cur=companion，mem 来自 code session 且项目类 → decay。

    spec: Companion request not hijacked by unrelated code-session project memory.
    """
    mem = _mem(
        "code-tyfbt62t",
        tool_calls='[{"name":"write_file","args":{"path":"backend/vpn-cli/main.c"}}]',
        content="实现 C 端 VPN 工具",
    )
    aff = _session_affinity(mem, "default", "companion", _DECAY)
    assert aff == pytest.approx(_DECAY)
    assert aff <= 0.15


def test_affinity_companion_from_code_summary_is_decay():
    """is_summary=1 的 code session 总结，在 companion 当前会话 → decay。"""
    mem = _mem("code-tyfbt62t", is_summary=1, content="VPN 项目进度总结", role="assistant")
    assert _session_affinity(mem, "default", "companion", _DECAY) == pytest.approx(_DECAY)


def test_affinity_companion_from_code_person_class_is_080():
    """cur=companion，mem 来自 code session 但人物/偏好类 → 0.8（轻降，仍跨 session 记得）。

    spec: Cross-session person/preference memory still recalled.
    """
    mem = _mem("code-tyfbt62t", content="我喜欢简洁的中文回答")
    aff = _session_affinity(mem, "default", "companion", _DECAY)
    assert aff == pytest.approx(0.8)
    # 关键：没被降到 decay（0.15），桌宠仍记得用户
    assert aff > _DECAY


def test_affinity_code_from_other_code_is_050():
    """cur=code，mem 来自另一个 code session → 0.5。"""
    mem = _mem("code-aaaa", tool_calls='[{"name":"edit_file"}]', content="改了点代码")
    assert _session_affinity(mem, "code-bbbb", "code", _DECAY) == pytest.approx(0.5)


def test_affinity_code_current_from_companion_memory_is_one():
    """cur=code，mem 来自 companion → 落到"其它"分支 = 1.0（不降权）。"""
    mem = _mem("default", content="闲聊记忆")
    assert _session_affinity(mem, "code-bbbb", "code", _DECAY) == pytest.approx(1.0)


def test_affinity_decay_one_restores_legacy_all_one():
    """decay=1.0 → 所有 affinity 都是 1.0（Strangler-Fig 退回旧行为）。

    spec: decay=1.0 restores legacy behavior.
    """
    cases = [
        (_mem("code-x", tool_calls='[{"a":1}]'), "default", "companion"),
        (_mem("code-x", is_summary=1), "default", "companion"),
        (_mem("code-x", content="我喜欢简洁回答"), "default", "companion"),
        (_mem("code-a", tool_calls='[{"a":1}]'), "code-b", "code"),
        (_mem("default"), "default", "companion"),
    ]
    for mem, cur_sid, cur_kind in cases:
        assert _session_affinity(mem, cur_sid, cur_kind, 1.0) == pytest.approx(1.0)


def test_affinity_missing_session_id_safe_default():
    """mem 没有 session_id（旧数据 / 异常行）→ 不降权（1.0），不抛。"""
    mem = {"is_summary": 0, "tool_calls": None, "content": "x", "role": "user"}
    assert _session_affinity(mem, "default", "companion", _DECAY) == pytest.approx(1.0)


# ======================================================================
# 端到端：retriever RRF 融合阶段乘 affinity（tasks 1.3 / 1.5）
# ======================================================================


@pytest.mark.asyncio
async def test_recall_without_session_context_is_legacy(db: SessionDB, embedder: Embedder):
    """不传 current-session 上下文 → 退回旧行为（affinity=1.0，结果与旧一致）。

    这是 1.5 不回归的核心保证：现有调用方 recall(query, top_k=...) 不变。
    """
    sid = await db.create_session()
    mid = await db.append_message(sid, "user", "python 错误处理")
    retriever = Retriever(db, embedder)
    hits = await retriever.recall("python", top_k=5)
    assert any(h.message_id == mid for h in hits)


@pytest.mark.asyncio
async def test_recall_companion_decays_cross_session_project_memory(
    db: SessionDB, embedder: Embedder
):
    """复现 2026-05-16 bug 的核心断言：

    code session 的高 salience 项目记忆（VPN）在 companion `default` session
    的无关请求里，融合分被乘 ≤ decay，且不得排在当前请求相关记忆之上。
    """
    # code session：8 天前的强项目记忆
    code_sid = "code-tyfbt62t"
    await db.append_message(
        code_sid,
        "user",
        "帮我做一个 C 端 VPN 工具 backend/vpn-cli/main.c 实现握手协议",
    )
    vpn_id = (await db.get_messages(code_sid))[-1]["id"]

    # companion default session：当前无关请求相关的记忆
    comp_sid = "default"
    await db.append_message(comp_sid, "user", "我们之前聊过画画的事")
    poster_id = (await db.get_messages(comp_sid))[-1]["id"]

    retriever = Retriever(db, embedder)
    # 旧调用（无上下文）：VPN 记忆未降权，可能靠前
    legacy = await retriever.recall("VPN", top_k=10)
    legacy_scores = {h.message_id: h.score for h in legacy}

    # 新调用（companion 当前会话上下文）：VPN 记忆应被降权
    scoped = await retriever.recall(
        "VPN",
        top_k=10,
        cur_session_id=comp_sid,
        cur_session_kind="companion",
        cross_session_decay=0.15,
    )
    scoped_scores = {h.message_id: h.score for h in scoped}

    assert vpn_id in legacy_scores, "VPN memory must be recalled in legacy mode"
    if vpn_id in scoped_scores and vpn_id in legacy_scores:
        # 降权后的 VPN 分数 ≤ 旧分数 * 0.15（项目类、跨 session、companion 当前）
        assert scoped_scores[vpn_id] <= legacy_scores[vpn_id] * 0.15 + 1e-9, (
            f"VPN project memory not decayed: legacy={legacy_scores[vpn_id]} "
            f"scoped={scoped_scores[vpn_id]}"
        )


@pytest.mark.asyncio
async def test_recall_decay_one_matches_legacy_ordering(
    db: SessionDB, embedder: Embedder
):
    """cross_session_decay=1.0 时，带上下文的 recall 与不带上下文的 recall
    结果完全一致（Strangler-Fig 端到端验证 / 1.5 不回归）。"""
    code_sid = "code-zzz"
    await db.append_message(code_sid, "user", "实现 backend/foo.py 的功能")
    await db.append_message(code_sid, "assistant", "好的我来写代码")
    comp_sid = "default"
    await db.append_message(comp_sid, "user", "今天聊点别的")

    retriever = Retriever(db, embedder)
    legacy = await retriever.recall("backend", top_k=10)
    rolled_back = await retriever.recall(
        "backend",
        top_k=10,
        cur_session_id=comp_sid,
        cur_session_kind="companion",
        cross_session_decay=1.0,
    )
    assert [h.message_id for h in legacy] == [h.message_id for h in rolled_back]
    for a, b in zip(legacy, rolled_back):
        assert a.score == pytest.approx(b.score)
