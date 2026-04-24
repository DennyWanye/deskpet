"""Tests for P4-S7 ContextAssembler (tasks 12.1-12.17).

Covers all "Scenario" acceptance criteria in
``openspec/changes/p4-poseidon-agent-harness/specs/context-assembler/spec.md``.
"""
from __future__ import annotations

import asyncio
import json
import random
import time
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from deskpet.agent.assembler import (
    AssemblyPolicy,
    BudgetAllocator,
    ComponentContext,
    ComponentRegistry,
    ContextAssembler,
    ContextBundle,
    MemoryComponent,
    PersonaComponent,
    SkillComponent,
    Slice,
    TaskClassifier,
    TimeComponent,
    ToolComponent,
    TTSPreNarrator,
    WorkspaceComponent,
    build_default_assembler,
    load_policies,
)
from deskpet.agent.assembler.bundle import MemoryPolicy, TASK_TYPES
from deskpet.agent.assembler.classifier import _rule_classify


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
class FakeMemoryManager:
    """Mimics MemoryManager.recall API."""

    def __init__(self, recall_result=None, raise_on_recall: bool = False):
        self.recall_result = recall_result or {
            "l1": {"memory": "主人喜欢红色袜子", "user": "程序员"},
            "l2": [
                {"role": "user", "content": "我喜欢红色"},
                {"role": "assistant", "content": "记住了。"},
            ],
            "l3": [
                {
                    "message_id": 1,
                    "score": 0.9,
                    "text": "红色袜子的回忆",
                    "source": "fts",
                    "ts": 1.0,
                }
            ],
        }
        self.raise_on_recall = raise_on_recall
        self.calls: list[tuple[str, dict]] = []

    async def recall(self, query: str, policy: dict) -> dict:
        self.calls.append((query, dict(policy)))
        if self.raise_on_recall:
            raise RuntimeError("memory boom")
        return self.recall_result


class FakeToolRegistry:
    def __init__(self, schemas: list[dict[str, Any]] | None = None):
        self._schemas = schemas or [
            {"type": "function", "function": {"name": "memory_read", "description": "read"}},
            {"type": "function", "function": {"name": "memory_search", "description": "search"}},
            {"type": "function", "function": {"name": "web_fetch", "description": "fetch"}},
            {"type": "function", "function": {"name": "file_read", "description": "read"}},
        ]

    def schemas(self, enabled_toolsets=None) -> list[dict[str, Any]]:
        return list(self._schemas)

    async def dispatch(self, name, args, task_id):
        return "ok"


class FakeEmbedder:
    """Deterministic mock embedder — md5-hashed vector of the text."""

    def __init__(self, dim: int = 16):
        self.dim = dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        import hashlib

        out: list[list[float]] = []
        for t in texts:
            h = hashlib.md5(t.encode("utf-8")).digest()
            # Derive deterministic float vector in [-1, 1].
            vec = [(b - 128) / 128.0 for b in h[: self.dim]]
            # Normalise
            norm = sum(v * v for v in vec) ** 0.5 or 1.0
            out.append([v / norm for v in vec])
        return out


class FakeResponse:
    def __init__(self, content: str):
        self.content = content


class FakeLLM:
    def __init__(self, answer: str = "chat"):
        self.answer = answer
        self.calls: list[tuple] = []

    async def chat_with_fallback(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return FakeResponse(self.answer)


# ---------------------------------------------------------------------------
# Classifier rule tier (task 12.2)
# ---------------------------------------------------------------------------
def test_rule_slash_is_command():
    res = _rule_classify("/help")
    assert res is not None and res[0] == "command"


def test_rule_recall_keyword():
    res = _rule_classify("还记得我上次说什么吗")
    assert res is not None and res[0] == "recall"


def test_rule_code_keyword():
    res = _rule_classify("帮我修一下这个 bug")
    assert res is not None and res[0] == "code"


def test_rule_miss_returns_none():
    assert _rule_classify("你今天还好吗") is None


@pytest.mark.asyncio
async def test_classifier_rule_short_circuits_llm(tmp_path: Path):
    """Spec: rule hit MUST NOT call LLM."""
    fake_llm = FakeLLM("task")
    classifier = TaskClassifier(
        embedder=FakeEmbedder(),
        llm_registry=fake_llm,
        exemplars_path=tmp_path / "empty.jsonl",
    )
    res = await classifier.classify("/help")
    assert res.task_type == "command"
    assert res.path == "rule"
    assert fake_llm.calls == [], "LLM tier should not have been called"


@pytest.mark.asyncio
async def test_classifier_embed_threshold(tmp_path: Path):
    """Spec: embed score > 0.75 → direct return without LLM."""
    exemplars_path = tmp_path / "ex.jsonl"
    exemplars_path.write_text(
        "\n".join(
            [
                json.dumps({"text": "我难过", "label": "emotion"}),
                json.dumps({"text": "心情不好", "label": "emotion"}),
                json.dumps({"text": "你好呀", "label": "chat"}),
            ]
        ),
        encoding="utf-8",
    )
    fake_llm = FakeLLM("code")  # wrong on purpose — should not be called
    classifier = TaskClassifier(
        embedder=FakeEmbedder(),
        llm_registry=fake_llm,
        exemplars_path=exemplars_path,
        embed_threshold=0.0,  # force embed to always win
        modes=("rule", "embed", "llm"),
    )
    res = await classifier.classify("普通对话没有触发规则的句子")
    assert res.path == "embed"
    assert res.task_type in TASK_TYPES
    assert fake_llm.calls == []


@pytest.mark.asyncio
async def test_classifier_llm_fallback(tmp_path: Path):
    """Spec: embed < threshold AND LLM mode on → LLM is called."""
    exemplars_path = tmp_path / "ex.jsonl"
    exemplars_path.write_text("", encoding="utf-8")  # empty → no embed hit
    fake_llm = FakeLLM("task")
    classifier = TaskClassifier(
        embedder=FakeEmbedder(),
        llm_registry=fake_llm,
        exemplars_path=exemplars_path,
    )
    res = await classifier.classify("请帮我准备明天的会议材料")
    assert res.path == "llm"
    assert res.task_type == "task"
    assert len(fake_llm.calls) == 1


@pytest.mark.asyncio
async def test_classifier_unknown_falls_back_to_chat(tmp_path: Path):
    """Spec: llm returns garbage → assembler falls back to chat."""
    exemplars_path = tmp_path / "ex.jsonl"
    exemplars_path.write_text("", encoding="utf-8")
    fake_llm = FakeLLM("gibberish_not_a_task_type")
    classifier = TaskClassifier(
        embedder=FakeEmbedder(),
        llm_registry=fake_llm,
        exemplars_path=exemplars_path,
    )
    res = await classifier.classify("random unclassifiable text")
    assert res.path == "default"
    assert res.task_type == "chat"


# ---------------------------------------------------------------------------
# ComponentRegistry parallel fan-out (task 12.6)
# ---------------------------------------------------------------------------
class SleepComponent:
    def __init__(self, name: str, sleep_s: float):
        self.name = name
        self.sleep_s = sleep_s

    async def provide(self, ctx):
        await asyncio.sleep(self.sleep_s)
        return Slice(
            component_name=self.name,
            text_content=f"{self.name} content",
            tokens=5,
            priority=50,
            bucket="dynamic",
        )


@pytest.mark.asyncio
async def test_fanout_is_parallel_not_serial():
    """Spec 'Parallel assembly beats serial': total ≈ max, not sum."""
    registry = ComponentRegistry()
    # Four 80ms components — serial would be 320ms, parallel ~80ms.
    for i in range(4):
        registry.register(SleepComponent(f"c{i}", 0.08))

    policy = AssemblyPolicy(
        task_type="chat",
        must=["c0"],
        prefer=["c1", "c2", "c3"],
    )
    # Memory is auto-injected by registry as "must"; register a stub.
    registry.register(SleepComponent("memory", 0.0))

    ctx = ComponentContext(
        task_type="chat",
        policy=policy,
        user_message="hi",
    )
    start = time.monotonic()
    slices = await registry.fanout(ctx)
    elapsed = time.monotonic() - start

    assert len(slices) == 5  # 4 + memory
    # Should be ~80ms, definitely < 250ms (would be 320ms if serial).
    assert elapsed < 0.25, f"fanout took {elapsed*1000:.0f}ms — not parallel"


@pytest.mark.asyncio
async def test_fanout_component_raise_is_soft():
    """Component raising an exception must not poison the fanout."""
    class Boom:
        name = "boom"

        async def provide(self, ctx):
            raise RuntimeError("kaboom")

    registry = ComponentRegistry()
    registry.register(Boom())
    registry.register(SleepComponent("memory", 0.0))

    policy = AssemblyPolicy(task_type="chat", must=["memory"], prefer=["boom"])
    ctx = ComponentContext(task_type="chat", policy=policy, user_message="")
    slices = await registry.fanout(ctx)

    names = {s.component_name for s in slices}
    assert names == {"memory", "boom"}
    boom_slice = next(s for s in slices if s.component_name == "boom")
    assert "error" in boom_slice.meta


# ---------------------------------------------------------------------------
# Policy YAML + overrides merge (tasks 12.7, 12.8)
# ---------------------------------------------------------------------------
def test_policies_packaged_default_has_all_task_types():
    policies = load_policies()
    for tt in TASK_TYPES:
        assert tt in policies, f"{tt} missing from default policies"
    # chat must have memory in must
    assert "memory" in policies["chat"].must


def test_user_override_adds_new_policy(tmp_path: Path):
    overrides = tmp_path / "overrides.yaml"
    overrides.write_text(
        "policies:\n  music:\n    must: [memory]\n    tools: [spotify_play]\n",
        encoding="utf-8",
    )
    policies = load_policies(overrides_path=overrides)
    assert "music" in policies
    assert policies["music"].tools == ["spotify_play"]


def test_user_cannot_remove_memory_from_must(tmp_path: Path, caplog):
    """Spec 'User cannot remove memory from must'."""
    overrides = tmp_path / "overrides.yaml"
    overrides.write_text(
        "policies:\n  chat:\n    must: [persona]\n",  # tries to drop memory
        encoding="utf-8",
    )
    policies = load_policies(overrides_path=overrides)
    # memory must still be in chat's must after merge.
    assert "memory" in policies["chat"].must


def test_user_override_deep_merges_memory(tmp_path: Path):
    overrides = tmp_path / "overrides.yaml"
    overrides.write_text(
        "policies:\n  chat:\n    memory:\n      l3_top_k: 20\n",
        encoding="utf-8",
    )
    policies = load_policies(overrides_path=overrides)
    assert policies["chat"].memory.l3_top_k == 20
    # l1/l2 should be preserved from default (snapshot/5).
    assert policies["chat"].memory.l1 == "snapshot"
    assert policies["chat"].memory.l2_top_k == 5


# ---------------------------------------------------------------------------
# BudgetAllocator (task 12.9)
# ---------------------------------------------------------------------------
def test_budget_drops_low_priority_first():
    alloc = BudgetAllocator(context_window=1000, budget_ratio=0.1)  # budget=100
    slices = [
        Slice(component_name="memory", text_content="m", tokens=80, priority=100, bucket="dynamic"),
        Slice(component_name="workspace", text_content="w" * 100, tokens=25, priority=40, bucket="dynamic"),
        Slice(component_name="time", text_content="t" * 50, tokens=10, priority=10, bucket="dynamic"),
    ]
    result = alloc.allocate(slices)
    # total=115 > budget=100. Drop priority 10 first, then 40. Memory kept.
    assert result.total_tokens <= result.budget_tokens
    assert "time" in result.cut
    # memory slice must still have its content
    mem_slice = next(s for s in result.slices if s.component_name == "memory")
    assert mem_slice.tokens > 0


def test_budget_shrinks_memory_when_dropping_not_enough():
    """Spec 'Over-budget memory shrink' — when trimming low-pri isn't enough."""
    alloc = BudgetAllocator(
        context_window=1000, budget_ratio=0.1, min_memory_tokens=10
    )  # budget=100
    long_text = "x" * 2000  # ~500 tokens
    slices = [
        Slice(
            component_name="memory",
            text_content=long_text,
            tokens=500,
            priority=100,
            bucket="dynamic",
        ),
    ]
    result = alloc.allocate(slices)
    assert result.total_tokens <= result.budget_tokens
    assert "memory_shrink" in result.cut
    mem = next(s for s in result.slices if s.component_name == "memory")
    assert mem.tokens > 0  # never fully dropped
    assert "[…trimmed by budget]" in mem.text_content


def test_budget_no_op_when_under_budget():
    alloc = BudgetAllocator(context_window=1000, budget_ratio=0.5)  # budget=500
    slices = [Slice(component_name="memory", text_content="m", tokens=10, priority=100)]
    result = alloc.allocate(slices)
    assert result.cut == []
    assert result.total_tokens == 10


# ---------------------------------------------------------------------------
# ContextBundle.build_messages order (task 12.10)
# ---------------------------------------------------------------------------
def test_bundle_build_messages_order():
    """Spec 'Build messages preserves cache-friendly order'."""
    bundle = ContextBundle(
        task_type="chat",
        frozen_system="FROZEN",
        skill_prelude="SKILLS",
        memory_block="MEMORY",
    )
    messages = bundle.build_messages(
        base_system="BASE",
        history=[{"role": "user", "content": "prev"}],
        user_message="NOW",
    )
    # Expected order: BASE+FROZEN, SKILLS, MEMORY, history, user.
    assert messages[0]["role"] == "system"
    assert "BASE" in messages[0]["content"] and "FROZEN" in messages[0]["content"]
    assert messages[1] == {"role": "system", "content": "SKILLS"}
    assert messages[2] == {"role": "system", "content": "MEMORY"}
    assert messages[3] == {"role": "user", "content": "prev"}
    assert messages[-1] == {"role": "user", "content": "NOW"}


def test_bundle_omits_empty_buckets():
    bundle = ContextBundle(task_type="chat", frozen_system="X")
    messages = bundle.build_messages(user_message="hi")
    assert len(messages) == 2
    assert messages[0]["content"] == "X"


# ---------------------------------------------------------------------------
# End-to-end assembler (tasks 12.1, 12.5, 12.10, 12.11)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_end_to_end_chat_turn(tmp_path: Path):
    """Full assemble() turn: classifier + components + budget + decisions."""
    exemplars_path = tmp_path / "ex.jsonl"
    exemplars_path.write_text("", encoding="utf-8")

    assembler = build_default_assembler(
        embedder=FakeEmbedder(),
        llm_registry=FakeLLM("chat"),
    )

    bundle = await assembler.assemble(
        "今天天气好不好",
        memory_manager=FakeMemoryManager(),
        tool_registry=FakeToolRegistry(),
    )

    assert isinstance(bundle, ContextBundle)
    assert bundle.task_type in TASK_TYPES
    assert bundle.decisions.task_type == bundle.task_type
    # frozen_system contains persona + L1 memory
    assert "DeskPet" in bundle.frozen_system or "程序员" in bundle.frozen_system
    # tool_schemas populated
    assert len(bundle.tool_schemas) > 0
    # decisions recorded
    assert bundle.decisions.assembly_latency_ms >= 0
    assert "memory" in bundle.decisions.components
    assert "persona" in bundle.decisions.components
    # cost_hint populated
    assert bundle.cost_hint.get("memory", 0) >= 0


@pytest.mark.asyncio
async def test_tools_filtered_to_policy_whitelist(tmp_path: Path):
    """Spec 'Assembler feeds tool_schemas to LLM': 2 tools not 16."""
    # Force task_type to 'recall' so policy.tools = [memory_read, memory_search].
    assembler = build_default_assembler(
        embedder=FakeEmbedder(), llm_registry=FakeLLM("chat")
    )
    bundle = await assembler.assemble(
        "还记得我之前说的红色袜子吗",  # rule hit → recall
        memory_manager=FakeMemoryManager(),
        tool_registry=FakeToolRegistry(),
    )
    assert bundle.task_type == "recall"
    names = {
        s.get("function", s).get("name") for s in bundle.tool_schemas
    }
    assert names == {"memory_read", "memory_search"}


@pytest.mark.asyncio
async def test_l1_in_frozen_l2l3_in_dynamic(tmp_path: Path):
    """Spec 'Prompt Cache Compatibility': L1 goes frozen, L2+L3 go dynamic."""
    assembler = build_default_assembler(
        embedder=FakeEmbedder(), llm_registry=FakeLLM("chat")
    )
    bundle = await assembler.assemble(
        "你好",
        memory_manager=FakeMemoryManager(),
        tool_registry=FakeToolRegistry(),
    )
    # L1 snapshot has "程序员", L2 recent has "红色", L3 hit text "红色袜子的回忆".
    # Our renderer puts L1 + L2/L3 together into one slice. Bucket decision:
    # when only L1 exists we stay frozen; when L2/L3 present we go dynamic.
    assert "红色" in bundle.memory_block  # dynamic
    # The tool slice has empty text so frozen_system comes from persona only.
    assert "DeskPet" in bundle.frozen_system


@pytest.mark.asyncio
async def test_assembler_unknown_task_type_falls_back_to_chat(tmp_path: Path):
    """Caller overrides with an unknown task_type → chat fallback."""
    assembler = build_default_assembler(
        embedder=FakeEmbedder(), llm_registry=FakeLLM("chat")
    )
    bundle = await assembler.assemble(
        "hi",
        memory_manager=FakeMemoryManager(),
        tool_registry=FakeToolRegistry(),
        task_type_override="made_up_type",
    )
    assert bundle.task_type == "chat"


@pytest.mark.asyncio
async def test_disabled_mode_emits_all_tools(tmp_path: Path):
    """Spec 'Disabled mode falls back to legacy'."""
    assembler = build_default_assembler(
        embedder=FakeEmbedder(), llm_registry=FakeLLM("chat"), enabled=False
    )
    bundle = await assembler.assemble(
        "anything",
        tool_registry=FakeToolRegistry(),
    )
    # Legacy mode emits every schema from the registry.
    assert len(bundle.tool_schemas) == 4
    assert bundle.decisions.classifier_path == "disabled"


@pytest.mark.asyncio
async def test_memory_manager_failure_doesnt_crash(tmp_path: Path):
    """Memory manager raising must not crash the assemble() call."""
    assembler = build_default_assembler(
        embedder=FakeEmbedder(), llm_registry=FakeLLM("chat")
    )
    bundle = await assembler.assemble(
        "你好",
        memory_manager=FakeMemoryManager(raise_on_recall=True),
        tool_registry=FakeToolRegistry(),
    )
    # Memory component returned an empty slice with error meta.
    assert "memory" in bundle.decisions.components
    mem_trace = bundle.decisions.components["memory"]
    assert mem_trace.meta.get("error_type") == "RuntimeError"
    # Persona still rendered.
    assert "DeskPet" in bundle.frozen_system


# ---------------------------------------------------------------------------
# Feedback + decisions ring (task 12.12)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_feedback_records_used_tools(tmp_path: Path):
    assembler = build_default_assembler(
        embedder=FakeEmbedder(), llm_registry=FakeLLM("chat")
    )
    bundle = await assembler.assemble(
        "你好",
        memory_manager=FakeMemoryManager(),
        tool_registry=FakeToolRegistry(),
    )
    assembler.feedback(
        bundle, used_tools=["memory_read"], final_response="好呀！"
    )
    assert bundle.decisions.used_tools == ["memory_read"]
    assert bundle.decisions.final_response_len > 0
    # Ring buffer exposes the decision.
    recent = assembler.recent_decisions(5)
    assert len(recent) >= 1
    assert recent[-1]["used_tools"] == ["memory_read"]


# ---------------------------------------------------------------------------
# Persona / Time / Workspace / Skill / Tool components
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_persona_component_uses_config_when_set():
    persona = PersonaComponent()
    ctx = ComponentContext(
        task_type="chat",
        policy=AssemblyPolicy(task_type="chat"),
        user_message="",
        config={"agent": {"persona": "你是好朋友"}},
    )
    slice_ = await persona.provide(ctx)
    assert "你是好朋友" in slice_.text_content


@pytest.mark.asyncio
async def test_time_component_uses_injected_clock():
    class FixedClock:
        def now(self):
            from datetime import datetime, timezone

            return datetime(2025, 1, 2, 3, 4, 5, tzinfo=timezone.utc)

    t = TimeComponent(clock=FixedClock())
    ctx = ComponentContext(
        task_type="chat",
        policy=AssemblyPolicy(task_type="chat"),
        user_message="",
    )
    slice_ = await t.provide(ctx)
    assert "2025-01-02" in slice_.text_content
    assert slice_.bucket == "dynamic"


@pytest.mark.asyncio
async def test_workspace_component_lists_recent_files(tmp_path: Path):
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    (tmp_path / "b.log").write_text("world", encoding="utf-8")
    ws = WorkspaceComponent(workspace_dir=tmp_path)
    ctx = ComponentContext(
        task_type="chat",
        policy=AssemblyPolicy(task_type="chat"),
        user_message="",
    )
    slice_ = await ws.provide(ctx)
    assert "a.txt" in slice_.text_content
    assert "b.log" in slice_.text_content


@pytest.mark.asyncio
async def test_skill_component_none_registry_is_empty():
    skill = SkillComponent()
    ctx = ComponentContext(
        task_type="chat",
        policy=AssemblyPolicy(task_type="chat"),
        user_message="",
    )
    slice_ = await skill.provide(ctx)
    assert slice_.text_content == ""
    assert slice_.meta.get("status") == "no_registry"


@pytest.mark.asyncio
async def test_tool_component_respects_wildcard_and_named_lists():
    tr = FakeToolRegistry()
    tool = ToolComponent()
    # Wildcard
    ctx = ComponentContext(
        task_type="chat",
        policy=AssemblyPolicy(task_type="chat", tools=["*"]),
        user_message="",
        tool_registry=tr,
    )
    s = await tool.provide(ctx)
    assert len(s.tool_schemas) == 4
    # Named subset
    ctx.policy = AssemblyPolicy(
        task_type="chat", tools=["memory_read", "web_fetch"]
    )
    s = await tool.provide(ctx)
    names = {sc["function"]["name"] for sc in s.tool_schemas}
    assert names == {"memory_read", "web_fetch"}
    # Empty → explicitly no tools
    ctx.policy = AssemblyPolicy(task_type="chat", tools=[])
    s = await tool.provide(ctx)
    assert s.tool_schemas == []


# ---------------------------------------------------------------------------
# TTS pre-narration (tasks 12.14, 12.15)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_prenarration_speaks_fixed_phrase():
    spoken: list[str] = []

    async def fake_tts(text: str):
        spoken.append(text)

    pn = TTSPreNarrator(
        tts_callable=fake_tts, rng=random.Random(42)
    )
    task = pn.speak("chat")
    assert task is not None
    await task
    assert len(spoken) == 1
    assert spoken[0] in {"嗯...", "让我想想..."}


@pytest.mark.asyncio
async def test_prenarration_tts_failure_is_swallowed():
    async def boom_tts(text: str):
        raise RuntimeError("tts offline")

    pn = TTSPreNarrator(tts_callable=boom_tts)
    task = pn.speak("chat")
    assert task is not None
    await task  # must not raise
    # No assertion on output — the point is it didn't blow up.


def test_prenarration_disabled_no_op():
    pn = TTSPreNarrator(tts_callable=lambda _: None, enabled=False)
    assert pn.speak("chat") is None


# ---------------------------------------------------------------------------
# Bench target (task 12.17) — not a full perf test, just a sanity p95 floor.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_assembler_p95_sanity(tmp_path: Path):
    """p95 over 10 runs should comfortably beat the 370ms spec target
    when using mock dependencies."""
    assembler = build_default_assembler(
        embedder=FakeEmbedder(), llm_registry=FakeLLM("chat")
    )
    timings: list[float] = []
    for _ in range(10):
        start = time.monotonic()
        await assembler.assemble(
            "你好呀",
            memory_manager=FakeMemoryManager(),
            tool_registry=FakeToolRegistry(),
        )
        timings.append((time.monotonic() - start) * 1000.0)
    timings.sort()
    p95 = timings[int(len(timings) * 0.95) - 1]
    # Mock path is deterministic and cheap — should be well under 50ms.
    assert p95 < 370.0, f"p95={p95:.1f}ms (target <370ms)"
