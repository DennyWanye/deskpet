# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations

import pytest

from deskpet.agent.assembler.bundle import (
    AssemblyPolicy,
    ContextBundle,
    MemoryPolicy,
)
from deskpet.agent.assembler.components.base import ComponentContext
from deskpet.agent.assembler.components.memory import (
    MemoryComponent,
    _topic_similarity,
)
from deskpet.agent.assembler.policy import _to_policy


L2_LABEL = "以下为较早的对话记录，可能涉及其他话题，仅供背景参考。"
NUDGE = "当前请求是本轮唯一任务；先前对话仅为背景，若与当前请求冲突，以当前请求为准。"


class _Vec:
    def __init__(self, value: float):
        self.value = value

    def __matmul__(self, other: "_Vec") -> float:
        return self.value * other.value


class _Vecs:
    shape = [2]

    def __init__(self, sim: float):
        self._sim = sim

    def __getitem__(self, index: int) -> _Vec:
        return _Vec(self._sim if index == 0 else 1.0)


class _Embedder:
    def __init__(self, sim: float, *, ready: bool = True, mock: bool = False):
        self.sim = sim
        self.ready = ready
        self.mock = mock
        self.calls: list[list[str]] = []

    def is_ready(self) -> bool:
        return self.ready

    def is_mock(self) -> bool:
        return self.mock

    async def encode(self, texts: list[str]) -> _Vecs:
        self.calls.append(texts)
        return _Vecs(self.sim)


class _Retriever:
    def __init__(self, embedder):
        self._embedder = embedder


class _MemoryManager:
    def __init__(self, *, l2=None, embedder=None):
        self._l2 = list(l2 or [])
        if embedder != "missing":
            self._retriever = _Retriever(embedder)

    async def recall(self, query, policy):
        return {"l1": {}, "l2": self._l2, "l3": []}


def _ctx(mm, memory_policy: MemoryPolicy, user_message: str) -> ComponentContext:
    return ComponentContext(
        task_type="chat",
        policy=AssemblyPolicy(task_type="chat", memory=memory_policy),
        user_message=user_message,
        session_id="default",
        memory_manager=mm,
    )


def _rows(count: int = 3) -> list[dict[str, str]]:
    return [
        {"role": "user", "content": f"old topic user {i}"}
        for i in range(count)
    ]


def test_build_messages_injects_late_system_nudge_between_history_and_user():
    bundle = ContextBundle(task_type="chat", frozen_system="base")
    history = [{"role": "assistant", "content": "old"}]

    messages = bundle.build_messages(
        history=history,
        late_system_nudge="X",
        user_message="new",
    )

    assert messages == [
        {"role": "system", "content": "base"},
        {"role": "assistant", "content": "old"},
        {"role": "system", "content": "X"},
        {"role": "user", "content": "new"},
    ]


def test_build_messages_omits_late_system_nudge_when_none():
    bundle = ContextBundle(task_type="chat", frozen_system="base")
    messages = bundle.build_messages(
        history=[{"role": "assistant", "content": "old"}],
        late_system_nudge=None,
        user_message="new",
    )

    assert messages == [
        {"role": "system", "content": "base"},
        {"role": "assistant", "content": "old"},
        {"role": "user", "content": "new"},
    ]


@pytest.mark.asyncio
async def test_memory_component_relabels_l2_when_enabled():
    component = MemoryComponent()
    ctx = _ctx(
        _MemoryManager(l2=_rows(1), embedder=None),
        MemoryPolicy(relabel_l2=True),
        "current",
    )

    sl = await component.provide(ctx)

    assert sl.meta["l2_history"][0] == {
        "role": "system",
        "content": L2_LABEL,
    }
    assert sl.meta["late_system_nudge"] == NUDGE


@pytest.mark.asyncio
async def test_memory_component_does_not_relabel_l2_when_disabled():
    component = MemoryComponent()
    ctx = _ctx(
        _MemoryManager(l2=_rows(1), embedder=None),
        MemoryPolicy(relabel_l2=False, anchor_current=False),
        "current",
    )

    sl = await component.provide(ctx)

    assert sl.meta["l2_history"] == [{"role": "user", "content": "old topic user 0"}]
    assert "late_system_nudge" not in sl.meta


def test_to_policy_reads_new_memory_fields_and_defaults():
    policy = _to_policy(
        "chat",
        {
            "memory": {
                "l1": "off",
                "l2_top_k": 7,
                "l3_top_k": 8,
                "relabel_l2": False,
                "anchor_current": False,
                "topic_shift_gate": True,
                "topic_shift_threshold": 0.2,
                "l2_keep_on_shift": 2,
            }
        },
    )

    assert policy.memory == MemoryPolicy(
        l1="off",
        l2_top_k=7,
        l3_top_k=8,
        relabel_l2=False,
        anchor_current=False,
        topic_shift_gate=True,
        topic_shift_threshold=0.2,
        l2_keep_on_shift=2,
    )
    assert _to_policy("chat", {}).memory == MemoryPolicy()


@pytest.mark.asyncio
async def test_topic_similarity_degrades_for_missing_or_mock_embedder():
    assert await _topic_similarity(None, "current", "history") is None

    mock_embedder = _Embedder(0.0, mock=True)
    assert await _topic_similarity(mock_embedder, "current", "history") is None


@pytest.mark.asyncio
async def test_topic_similarity_real_numpy_dot_product_contract():
    """Lock the production type contract: real Embedder.encode returns a
    numpy (N,1024) L2-normalized array, and `vecs[0] @ vecs[1]` must yield
    a Python float cosine. Guards against a numpy/`@` regression the mock
    _Vec can't catch."""
    np = pytest.importorskip("numpy")

    class _NumpyEmbedder:
        def is_ready(self) -> bool:
            return True

        def is_mock(self) -> bool:
            return False

        async def encode(self, texts: list[str]):
            # Two L2-normalized 4-d vectors with a known cosine of 0.5.
            a = np.array([1.0, 0.0, 0.0, 0.0], dtype="float32")
            b = np.array([0.5, (3 ** 0.5) / 2, 0.0, 0.0], dtype="float32")
            return np.stack([a, b])

    sim = await _topic_similarity(_NumpyEmbedder(), "current", "history")
    assert isinstance(sim, float)
    assert abs(sim - 0.5) < 1e-5


@pytest.mark.asyncio
async def test_topic_shift_gate_lexical_fallback_truncates_when_embedder_down():
    """Real E2E (2026-06-20): the BGE-M3 subprocess is lock-contended right
    after boot (vector-worker backfill) so the live encode times out and
    gate_sim=None. Instead of failing open (drift survives), Tier 2 falls
    back to a zero-latency lexical-overlap signal. A cross-domain request
    (no token overlap with the "old topic user" L2) is still truncated."""
    component = MemoryComponent()
    ctx = _ctx(
        _MemoryManager(l2=_rows(3), embedder=None),
        MemoryPolicy(topic_shift_gate=True, l2_keep_on_shift=1),
        "请帮我深入研究 Rust Tokio 异步运行时的调度器、IO 驱动、任务模型、生态定位和竞品对比。",
    )

    sl = await component.provide(ctx)

    assert sl.meta.get("l2_history")  # relabel + kept tail present
    assert [m["content"] for m in sl.meta["l2_history"][1:]] == ["old topic user 2"]


@pytest.mark.asyncio
async def test_topic_shift_gate_lexical_fallback_keeps_topic_overlap():
    """Embedder down + a message that shares content tokens with the recent
    L2 (a real on-topic follow-up) must be KEPT by the lexical fallback —
    only genuine topic changes get truncated."""
    component = MemoryComponent()
    ctx = _ctx(
        _MemoryManager(l2=_rows(3), embedder=None),
        MemoryPolicy(topic_shift_gate=True, l2_keep_on_shift=1),
        "请再详细说说 old topic user 这个话题的更多要点和背景",
    )

    sl = await component.provide(ctx)

    assert [m["content"] for m in sl.meta["l2_history"][1:]] == [
        "old topic user 0",
        "old topic user 1",
        "old topic user 2",
    ]


@pytest.mark.asyncio
async def test_topic_shift_gate_truncates_on_low_similarity_and_keeps_high_similarity():
    component = MemoryComponent()
    current = (
        "请帮我深入研究 Rust Tokio 异步运行时的调度器、IO 驱动、任务模型、生态定位、"
        "竞品对比、生产案例和未来演进趋势。"
    )

    low_ctx = _ctx(
        _MemoryManager(l2=_rows(3), embedder=_Embedder(0.1)),
        MemoryPolicy(
            topic_shift_gate=True,
            topic_shift_threshold=0.35,
            l2_keep_on_shift=1,
        ),
        current,
    )
    low = await component.provide(low_ctx)
    assert [m["content"] for m in low.meta["l2_history"][1:]] == [
        "old topic user 2"
    ]

    high_ctx = _ctx(
        _MemoryManager(l2=_rows(3), embedder=_Embedder(0.9)),
        MemoryPolicy(
            topic_shift_gate=True,
            topic_shift_threshold=0.35,
            l2_keep_on_shift=1,
        ),
        current,
    )
    high = await component.provide(high_ctx)
    assert [m["content"] for m in high.meta["l2_history"][1:]] == [
        "old topic user 0",
        "old topic user 1",
        "old topic user 2",
    ]


@pytest.mark.asyncio
async def test_topic_shift_gate_truncates_short_explicit_new_task():
    """Regression for real E2E (2026-06-20): a terse but explicit new-task
    command of 32 chars ("帮我深度调研 Rust 异步运行时 Tokio 的架构与竞品对比")
    drifted because the old length gate was >50. With the calibrated
    topic_shift_min_len=16 it is eligible and the off-topic CATL L2 is
    truncated."""
    component = MemoryComponent()
    current = "帮我深度调研 Rust 异步运行时 Tokio 的架构与竞品对比"
    assert 16 <= len(current) <= 50  # would have failed the old >50 gate

    ctx = _ctx(
        _MemoryManager(l2=_rows(3), embedder=_Embedder(0.1)),
        MemoryPolicy(
            topic_shift_gate=True,
            topic_shift_threshold=0.35,
            l2_keep_on_shift=1,
            topic_shift_min_len=16,
        ),
        current,
    )
    sl = await component.provide(ctx)
    assert [m["content"] for m in sl.meta["l2_history"][1:]] == ["old topic user 2"]


@pytest.mark.asyncio
async def test_topic_shift_gate_default_off_does_not_call_embedder():
    component = MemoryComponent()
    embedder = _Embedder(0.1)
    ctx = _ctx(
        _MemoryManager(l2=_rows(3), embedder=embedder),
        MemoryPolicy(topic_shift_gate=False, l2_keep_on_shift=1),
        "请帮我深入研究 Rust Tokio 异步运行时的调度器、IO 驱动、任务模型、生态定位和竞品对比。",
    )

    sl = await component.provide(ctx)

    assert embedder.calls == []
    assert [m["content"] for m in sl.meta["l2_history"][1:]] == [
        "old topic user 0",
        "old topic user 1",
        "old topic user 2",
    ]


@pytest.mark.asyncio
async def test_topic_shift_gate_keeps_short_or_anaphora_followups():
    component = MemoryComponent()
    ctx = _ctx(
        _MemoryManager(l2=_rows(3), embedder=_Embedder(0.1)),
        MemoryPolicy(topic_shift_gate=True, l2_keep_on_shift=1),
        "它的竞品呢",
    )

    sl = await component.provide(ctx)

    assert [m["content"] for m in sl.meta["l2_history"][1:]] == [
        "old topic user 0",
        "old topic user 1",
        "old topic user 2",
    ]
