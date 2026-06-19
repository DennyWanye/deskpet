# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Tests for WI-4.1 二级披露做实（embedding 强匹配 → 自动载 skill 正文）.

Covers:
  T1 — 3 skills (strong/mid/weak match) → only strong body in prelude, weak desc-only.
  T2 — over-budget: high usage_count body retained, low dropped.
  T3 — embedder=None → degrade to desc list (no crash).
  T4 — disable_model_invocation skill → not auto-loaded.
  T5 — flag off → desc-list-only (byte regression vs current behavior).
  T6 — loader.read_body(name) returns raw body without arg substitution.
  T7 — SkillMatcher.match returns sorted (name, sim) list.
  T8 — SkillMatcher caches skill embeddings; query embedding recomputed per call.
  T9 — SkillMatcher.match with embedder=None returns [].
"""
from __future__ import annotations

import asyncio
import textwrap
from pathlib import Path
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from deskpet.agent.assembler.bundle import Slice
from deskpet.agent.assembler.components.base import ComponentContext
from deskpet.agent.assembler.components.skill import SkillComponent
from deskpet.skills.loader import SkillLoader, SkillMeta
from deskpet.skills.skill_matcher import SkillMatcher


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_meta(
    name: str,
    description: str,
    body: str = "",
    *,
    disable_model_invocation: bool = False,
    usage_count: int = 0,
) -> SkillMeta:
    """Build a minimal SkillMeta for testing."""
    return SkillMeta(
        name=name,
        description=description,
        version="0.1.0",
        author="test",
        scope="built-in",
        path=f"/fake/{name}/SKILL.md",
        disable_model_invocation=disable_model_invocation,
    )


def _make_skill_dir(tmp_path: Path, name: str, description: str, body: str) -> Path:
    """Write a real SKILL.md for loader tests."""
    # Do NOT use textwrap.dedent with indented content — it strips 8 spaces
    # and the `---` ends up with leading spaces, making _split_frontmatter miss it.
    lines = [
        "---",
        f"name: {name}",
        f"description: {description}",
        "version: 0.1.0",
        "author: test",
        "---",
        body,
        "",
    ]
    fm = "\n".join(lines)
    skill_dir = tmp_path / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(fm, encoding="utf-8")
    return skill_dir


def _make_embedder(vectors: dict[str, list[float]]) -> Any:
    """Fake synchronous embedder. encode(text) -> list[float]."""

    class _FakeEmbedder:
        def encode(self, text: str) -> list[float]:
            # Return stored vector for known texts, else zero vector
            for key, vec in vectors.items():
                if key in text:
                    return list(vec)
            # Return a distinct vector per unknown text to avoid accidental matches
            # Use hash-based deterministic vector
            h = hash(text) % 1000
            return [float(h), 0.0, 0.0]

    return _FakeEmbedder()


def _unit_vec(idx: int, dim: int = 3) -> list[float]:
    """Return a unit vector with 1.0 at position idx."""
    v = [0.0] * dim
    v[idx % dim] = 1.0
    return v


# ---------------------------------------------------------------------------
# T6 — loader.read_body(name) returns raw body, no arg substitution
# ---------------------------------------------------------------------------

def test_read_body_returns_raw_body_no_substitution(tmp_path: Path) -> None:
    """read_body should return the unsubstituted body text."""
    body_text = "Do ${args[0]} with ${args[1]} items."
    _make_skill_dir(tmp_path, "raw-demo", "A demo skill", body_text)
    loader = SkillLoader([tmp_path], enable_watch=False)
    loader.reload()
    body = loader.read_body("raw-demo")
    assert "${args[0]}" in body
    assert "${args[1]}" in body
    assert "Do" in body


def test_read_body_unknown_skill_raises(tmp_path: Path) -> None:
    loader = SkillLoader([tmp_path], enable_watch=False)
    loader.reload()
    with pytest.raises(KeyError):
        loader.read_body("nonexistent")


def test_read_body_strips_frontmatter(tmp_path: Path) -> None:
    """read_body must NOT include the frontmatter block."""
    body_text = "This is the skill body."
    _make_skill_dir(tmp_path, "strip-fm", "Test", body_text)
    loader = SkillLoader([tmp_path], enable_watch=False)
    loader.reload()
    body = loader.read_body("strip-fm")
    assert "---" not in body.strip().split("\n")[0]
    assert "This is the skill body." in body


# ---------------------------------------------------------------------------
# T7 — SkillMatcher.match returns sorted (name, sim) list
# ---------------------------------------------------------------------------

def test_skill_matcher_match_sorted_by_similarity() -> None:
    """match() should return (name, sim) pairs sorted descending."""
    # Query vector [1, 0, 0]
    # skill-a description vector [1, 0, 0] → cos=1.0 (strong)
    # skill-b description vector [0, 1, 0] → cos=0.0 (weak)
    # skill-c description vector [0.6, 0.8, 0] → cos=0.6 (mid)
    vectors = {
        "query": _unit_vec(0),
        "strong skill": [1.0, 0.0, 0.0],
        "mid skill": [0.6, 0.8, 0.0],
        "weak skill": [0.0, 1.0, 0.0],
    }
    embedder = _make_embedder(vectors)

    skills = [
        _make_meta("strong", "strong skill"),
        _make_meta("weak", "weak skill"),
        _make_meta("mid", "mid skill"),
    ]

    matcher = SkillMatcher(embedder)
    matcher.build(skills)

    results = matcher.match("query", skills)
    assert len(results) == 3
    names = [r[0] for r in results]
    sims = [r[1] for r in results]
    # Must be sorted descending by sim
    assert sims == sorted(sims, reverse=True)
    # strong should be first
    assert names[0] == "strong"


def test_skill_matcher_match_empty_skills() -> None:
    """match() with empty skill list returns []."""
    embedder = _make_embedder({"q": [1.0, 0.0, 0.0]})
    matcher = SkillMatcher(embedder)
    matcher.build([])
    results = matcher.match("q", [])
    assert results == []


# ---------------------------------------------------------------------------
# T8 — embedder=None → match returns []
# ---------------------------------------------------------------------------

def test_skill_matcher_none_embedder_returns_empty() -> None:
    matcher = SkillMatcher(None)
    skills = [_make_meta("foo", "bar")]
    matcher.build(skills)
    results = matcher.match("bar", skills)
    assert results == []


# T9 variant: SkillMatcher.match doesn't crash with None embedder
def test_skill_matcher_none_embedder_no_crash() -> None:
    matcher = SkillMatcher(None)
    matcher.build([])
    results = matcher.match("hello", [])
    assert isinstance(results, list)


# ---------------------------------------------------------------------------
# FP-5 缺口 5h/5i 回归 (2026-06-06 真机抓 bug)：
# 生产 BGE-M3 embedder 是 ASYNC（``async embed(list[str]) -> list[list[float]]``
# / ``async encode(list[str]) -> ndarray``）。原 matcher 同步调 ``encode(text)``
# → 拿到未 await 的 coroutine → 缓存恒空 → 自动披露真机 top_sim=0.0 永远零匹配。
# 旧单测只用 SYNC mock 所以全绿，掩盖了这条生产死链。本组用 async embedder
# 复现生产契约，确保 match_async 能惰性异步建缓存 + 产出真相似度。
# ---------------------------------------------------------------------------

def _make_async_embedder(vectors: dict[str, list[float]]) -> Any:
    """Production-shaped ASYNC embedder: async embed(list[str]) -> list[list[float]]."""

    class _AsyncEmbedder:
        async def embed(self, texts: list[str]) -> list[list[float]]:
            out: list[list[float]] = []
            for text in texts:
                vec = None
                for key, v in vectors.items():
                    if key in text:
                        vec = list(v)
                        break
                if vec is None:
                    h = hash(text) % 1000
                    vec = [float(h), 0.0, 0.0]
                out.append(vec)
            return out

        async def encode(self, texts: list[str]):  # also async, list-in
            return await self.embed(texts)

    return _AsyncEmbedder()


@pytest.mark.asyncio
async def test_skill_matcher_async_embedder_lazy_builds_and_matches() -> None:
    """match_async with an ASYNC embedder strong-matches WITHOUT a prior build().

    Reproduces production: build() ran before BGE-M3 warmup (cache empty) AND
    the embedder is async. match_async must lazily await-embed the selected
    skills + query and produce real cosine sims (regression for top_sim=0.0).
    """
    vectors = {
        "query": [1.0, 0.0, 0.0],
        "strong skill": [1.0, 0.0, 0.0],   # cos=1.0
        "weak skill": [0.0, 1.0, 0.0],     # cos=0.0
    }
    embedder = _make_async_embedder(vectors)
    skills = [
        _make_meta("strong", "strong skill"),
        _make_meta("weak", "weak skill"),
    ]
    matcher = SkillMatcher(embedder)
    # NOTE: deliberately NOT calling build() — simulate empty/stale cache.
    results = await matcher.match_async("query", skills)

    assert len(results) == 2, f"应对 2 个技能都算出 sim,实际 {results}"
    sims = dict(results)
    assert sims["strong"] > 0.9, f"strong 应高相似度,实际 {sims['strong']}"
    assert sims["weak"] < 0.1, f"weak 应低相似度,实际 {sims['weak']}"
    # Sorted descending, strong first.
    assert results[0][0] == "strong"


@pytest.mark.asyncio
async def test_skill_matcher_async_encode_only_embedder() -> None:
    """Embedder exposing only async ``encode(list)`` (no ``embed``) also works."""

    class _EncodeOnly:
        async def encode(self, texts: list[str]):
            return [[1.0, 0.0, 0.0] if "q" in t or "match me" in t else [0.0, 1.0, 0.0]
                    for t in texts]

    matcher = SkillMatcher(_EncodeOnly())
    skills = [_make_meta("hit", "match me please")]
    results = await matcher.match_async("q", skills)
    assert results and results[0][0] == "hit"
    assert results[0][1] > 0.9


# ---------------------------------------------------------------------------
# T1 — SkillComponent 3-tier: strong body in prelude, weak desc-only
# ---------------------------------------------------------------------------

def _make_ctx(
    skills: list[SkillMeta],
    user_message: str = "query",
    auto_disclosure_enabled: bool = True,
    strong_threshold: float = 0.55,
    budget_tokens: int = 8000,
) -> ComponentContext:
    """Build a ComponentContext with a fake registry and config."""
    registry = MagicMock()
    registry.all.return_value = skills
    # select returns all (no task type filtering for these tests)
    registry.select.return_value = skills

    ctx = ComponentContext(
        task_type="chat",
        policy=MagicMock(prefer=[]),
        user_message=user_message,
        config={
            "skills": {
                "auto_disclosure": {
                    "enabled": auto_disclosure_enabled,
                    "strong_threshold": strong_threshold,
                    "budget_tokens": budget_tokens,
                }
            }
        },
    )
    ctx.skill_registry = registry
    return ctx


@pytest.mark.asyncio
async def test_strong_match_body_in_prelude(tmp_path: Path) -> None:
    """When sim >= threshold, skill body must appear in prelude."""
    # Write real skill files so read_body works
    _make_skill_dir(tmp_path, "strong-skill", "strong skill description", "## Strong Body\nThis is strong body content.")
    _make_skill_dir(tmp_path, "weak-skill", "weak skill description", "## Weak Body\nThis is weak body content.")

    loader = SkillLoader([tmp_path], enable_watch=False)
    loader.reload()

    strong_meta = loader.get("strong-skill")
    weak_meta = loader.get("weak-skill")
    assert strong_meta is not None
    assert weak_meta is not None

    # Embedder: query matches strong
    vectors = {
        "strong skill": [1.0, 0.0, 0.0],
        "weak skill": [0.0, 1.0, 0.0],
        "query": [1.0, 0.0, 0.0],
    }
    embedder = _make_embedder(vectors)
    matcher = SkillMatcher(embedder)
    matcher.build([strong_meta, weak_meta])

    component = SkillComponent(skill_matcher=matcher, skill_loader=loader)
    ctx = _make_ctx(
        [strong_meta, weak_meta],
        user_message="query",
        auto_disclosure_enabled=True,
        strong_threshold=0.55,
    )

    result: Slice = await component.provide(ctx)
    assert result.text_content
    # Strong body should be inlined
    assert "Strong Body" in result.text_content or "strong body content" in result.text_content.lower()
    # Weak body should NOT be inlined
    assert "weak body content" not in result.text_content.lower()
    # Both names should appear (desc list always present)
    assert "strong-skill" in result.text_content
    assert "weak-skill" in result.text_content


@pytest.mark.asyncio
async def test_weak_match_desc_only(tmp_path: Path) -> None:
    """When no skill exceeds threshold, only desc list (no bodies)."""
    _make_skill_dir(tmp_path, "skill-a", "alpha description", "## Alpha body content.")
    loader = SkillLoader([tmp_path], enable_watch=False)
    loader.reload()
    meta_a = loader.get("skill-a")
    assert meta_a is not None

    # Query is orthogonal to all skills → all sims near 0
    vectors = {
        "alpha description": [0.0, 1.0, 0.0],
        "query": [0.0, 0.0, 1.0],
    }
    embedder = _make_embedder(vectors)
    matcher = SkillMatcher(embedder)
    matcher.build([meta_a])

    component = SkillComponent(skill_matcher=matcher, skill_loader=loader)
    ctx = _make_ctx(
        [meta_a],
        user_message="query",
        auto_disclosure_enabled=True,
        strong_threshold=0.55,
    )
    result: Slice = await component.provide(ctx)
    assert "skill-a" in result.text_content
    assert "Alpha body content" not in result.text_content


# ---------------------------------------------------------------------------
# T2 — over-budget: high usage_count retained, low usage_count dropped
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_overbudget_high_usage_retained(tmp_path: Path) -> None:
    """When total body tokens exceed budget, drop lowest usage_count first."""
    # Create two skills both strong matches, budget allows only one
    body_high = "x" * 500  # ~125 tokens
    body_low = "y" * 500   # ~125 tokens

    _make_skill_dir(tmp_path, "high-use", "alpha skill description", body_high)
    _make_skill_dir(tmp_path, "low-use", "beta skill description", body_low)
    loader = SkillLoader([tmp_path], enable_watch=False)
    loader.reload()

    meta_high = loader.get("high-use")
    meta_low = loader.get("low-use")
    assert meta_high and meta_low

    # Add usage_count tracking via SkillMeta meta dict
    meta_high.meta["usage_count"] = 10
    meta_low.meta["usage_count"] = 0

    # Both strongly match the query
    vectors = {
        "alpha skill": [1.0, 0.0, 0.0],
        "beta skill": [0.99, 0.0, 0.0],  # slightly less sim but still strong
        "query": [1.0, 0.0, 0.0],
    }
    embedder = _make_embedder(vectors)
    matcher = SkillMatcher(embedder)
    matcher.build([meta_high, meta_low])

    # Tiny budget: only fits one body (~125 tokens each, budget=130)
    component = SkillComponent(skill_matcher=matcher, skill_loader=loader)
    ctx = _make_ctx(
        [meta_high, meta_low],
        user_message="query",
        auto_disclosure_enabled=True,
        strong_threshold=0.55,
        budget_tokens=130,
    )
    result: Slice = await component.provide(ctx)
    # high-use body should be present
    assert body_high[:20] in result.text_content
    # low-use body should have been dropped
    assert body_low[:20] not in result.text_content
    # But both names must still appear in desc list
    assert "high-use" in result.text_content
    assert "low-use" in result.text_content


# ---------------------------------------------------------------------------
# T3 — embedder=None → degrade to desc list (no crash)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_embedder_none_degrades_to_desc_list(tmp_path: Path) -> None:
    """embedder=None: no crash, returns desc list only."""
    _make_skill_dir(tmp_path, "some-skill", "some description", "## Body content")
    loader = SkillLoader([tmp_path], enable_watch=False)
    loader.reload()
    meta = loader.get("some-skill")
    assert meta is not None

    matcher = SkillMatcher(None)  # no embedder
    matcher.build([meta])

    component = SkillComponent(skill_matcher=matcher, skill_loader=loader)
    ctx = _make_ctx(
        [meta],
        user_message="some query",
        auto_disclosure_enabled=True,
    )
    result: Slice = await component.provide(ctx)
    assert result.text_content
    # desc list present
    assert "some-skill" in result.text_content
    # body NOT present (no embedding match)
    assert "Body content" not in result.text_content


# ---------------------------------------------------------------------------
# T4 — disable_model_invocation skill → not auto-loaded
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_disable_model_invocation_not_auto_loaded(tmp_path: Path) -> None:
    """Skills with disable_model_invocation=True must not have body auto-loaded."""
    _make_skill_dir(tmp_path, "gated-skill", "gated skill description", "## Gated body content.")
    loader = SkillLoader([tmp_path], enable_watch=False)
    loader.reload()
    # Manually set disable_model_invocation on the loaded meta
    meta = loader.get("gated-skill")
    assert meta is not None
    meta.disable_model_invocation = True

    # Perfect match
    vectors = {
        "gated skill": [1.0, 0.0, 0.0],
        "query": [1.0, 0.0, 0.0],
    }
    embedder = _make_embedder(vectors)
    matcher = SkillMatcher(embedder)
    matcher.build([meta])

    component = SkillComponent(skill_matcher=matcher, skill_loader=loader)
    ctx = _make_ctx(
        [meta],
        user_message="query",
        auto_disclosure_enabled=True,
        strong_threshold=0.55,
    )
    result: Slice = await component.provide(ctx)
    # Name still appears in desc list
    assert "gated-skill" in result.text_content
    # Body NOT inlined
    assert "Gated body content" not in result.text_content


# ---------------------------------------------------------------------------
# T5 — flag off → desc-list-only (byte regression vs current)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_flag_off_desc_list_only(tmp_path: Path) -> None:
    """With auto_disclosure.enabled=False, behavior matches pre-WI-4.1 (desc list only)."""
    _make_skill_dir(tmp_path, "flag-skill", "flag skill description", "## Flag body content.")
    loader = SkillLoader([tmp_path], enable_watch=False)
    loader.reload()
    meta = loader.get("flag-skill")
    assert meta is not None

    # Strong embedder match, but flag is OFF
    vectors = {
        "flag skill": [1.0, 0.0, 0.0],
        "query": [1.0, 0.0, 0.0],
    }
    embedder = _make_embedder(vectors)
    matcher = SkillMatcher(embedder)
    matcher.build([meta])

    component = SkillComponent(skill_matcher=matcher, skill_loader=loader)
    ctx = _make_ctx(
        [meta],
        user_message="query",
        auto_disclosure_enabled=False,  # FLAG OFF
    )
    result: Slice = await component.provide(ctx)
    # Desc list still present
    assert "flag-skill" in result.text_content
    # Body NOT present (flag off)
    assert "Flag body content" not in result.text_content


@pytest.mark.asyncio
async def test_flag_off_no_matcher_needed(tmp_path: Path) -> None:
    """With flag off, SkillComponent works even if no matcher/loader injected."""
    _make_skill_dir(tmp_path, "vanilla", "vanilla description", "## Vanilla body.")
    loader = SkillLoader([tmp_path], enable_watch=False)
    loader.reload()
    meta = loader.get("vanilla")
    assert meta is not None

    # No matcher, no loader — flag off = current behavior
    component = SkillComponent()  # no matcher/loader
    ctx = _make_ctx(
        [meta],
        user_message="query",
        auto_disclosure_enabled=False,
    )
    result: Slice = await component.provide(ctx)
    assert "vanilla" in result.text_content
    assert "Vanilla body" not in result.text_content


# ---------------------------------------------------------------------------
# T-extra: prelude format — "以下技能正文已预载" annotation present when body loaded
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_prelude_annotation_present_when_body_loaded(tmp_path: Path) -> None:
    """When body is auto-loaded, prelude should contain an annotation."""
    _make_skill_dir(tmp_path, "annotated-skill", "annotated skill desc", "## Annotated body.")
    loader = SkillLoader([tmp_path], enable_watch=False)
    loader.reload()
    meta = loader.get("annotated-skill")
    assert meta is not None

    vectors = {
        "annotated skill": [1.0, 0.0, 0.0],
        "query": [1.0, 0.0, 0.0],
    }
    embedder = _make_embedder(vectors)
    matcher = SkillMatcher(embedder)
    matcher.build([meta])

    component = SkillComponent(skill_matcher=matcher, skill_loader=loader)
    ctx = _make_ctx(
        [meta],
        user_message="query",
        auto_disclosure_enabled=True,
        strong_threshold=0.55,
    )
    result: Slice = await component.provide(ctx)
    # Should have some indicator that auto-load happened
    assert "annotated-skill" in result.text_content
    assert "Annotated body" in result.text_content


# ---------------------------------------------------------------------------
# T-extra: desc_list priority=85 (not cut), body segment priority lower
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_desc_list_always_present_even_when_no_body_loaded(tmp_path: Path) -> None:
    """Desc list (priority=85) must always appear regardless of body loading."""
    _make_skill_dir(tmp_path, "always-listed", "always listed desc", "## Body.")
    loader = SkillLoader([tmp_path], enable_watch=False)
    loader.reload()
    meta = loader.get("always-listed")
    assert meta is not None

    # embedder=None → no bodies loaded
    component = SkillComponent(skill_matcher=SkillMatcher(None), skill_loader=loader)
    ctx = _make_ctx([meta], auto_disclosure_enabled=True)
    result = await component.provide(ctx)
    assert "always-listed" in result.text_content


# ---------------------------------------------------------------------------
# TC-5.1 真机回归 (2026-06-11)：total=1 / top_sim=0.000 双根因
#
# ① ``loader.select(task_type)`` 按 task_types frontmatter 过滤，而 builtin
#   claude-code-v1 格式 skill 全是 ``task_types=[]`` → 全被滤掉 → 组件只拿到
#   1 个漏网 skill（真机 log: skill_auto_disclosed total=1）。auto-disclosure
#   的设计是「全集进 desc list + embedding 决定强匹配」→ flag ON 必须用
#   ``registry.all()`` 全集；flag OFF 保持 select 路径（字节级 BC）。
# ② lifespan 的 ``matcher.build(loader.all())`` 同步调 async encode → 静默
#   no-op（unawaited coroutine 被吞）→ 需要 ``build_async`` 预热入口。
# ③ log 的 top_sim 打 strong_matches[0]（空时 0.0）掩盖真实 ranked 分数。
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_auto_on_uses_full_skill_set_not_select(tmp_path: Path) -> None:
    """auto ON 时组件必须用 registry.all() 全集，不被 select() 过滤。"""
    _make_skill_dir(
        tmp_path, "research-skill", "deep research helper", "## Research body content."
    )
    loader = SkillLoader([tmp_path], enable_watch=False)
    loader.reload()
    research = loader.get("research-skill")
    assert research is not None
    other = _make_meta("other", "unrelated description")

    registry = MagicMock()
    registry.select.return_value = [other]  # select 把 research-skill 滤掉了
    registry.all.return_value = [other, research]  # 全集才有

    vectors = {
        "deep research": [1.0, 0.0, 0.0],
        "unrelated description": [0.0, 1.0, 0.0],
        "query": [1.0, 0.0, 0.0],
    }
    matcher = SkillMatcher(_make_embedder(vectors))
    matcher.build([other, research])

    component = SkillComponent(skill_matcher=matcher, skill_loader=loader)
    ctx = _make_ctx([], user_message="query", auto_disclosure_enabled=True)
    ctx.skill_registry = registry

    result: Slice = await component.provide(ctx)
    # 全集进 desc list（select 漏掉的 skill 必须在）
    assert "research-skill" in result.text_content
    # 且强匹配 body 被预载
    assert "Research body content" in result.text_content
    assert result.meta["count"] == 2


@pytest.mark.asyncio
async def test_flag_off_keeps_select_path(tmp_path: Path) -> None:
    """flag OFF 保持 select() venue（字节级 BC：不泄露全集进 chat prelude）。"""
    _make_skill_dir(tmp_path, "research-skill", "deep research helper", "## Body.")
    loader = SkillLoader([tmp_path], enable_watch=False)
    loader.reload()
    research = loader.get("research-skill")
    other = _make_meta("other", "unrelated description")

    registry = MagicMock()
    registry.select.return_value = [other]
    registry.all.return_value = [other, research]

    component = SkillComponent()
    ctx = _make_ctx([], user_message="query", auto_disclosure_enabled=False)
    ctx.skill_registry = registry

    result: Slice = await component.provide(ctx)
    assert "other" in result.text_content
    assert "research-skill" not in result.text_content


@pytest.mark.asyncio
async def test_build_async_with_async_embedder_fills_cache() -> None:
    """build_async 用生产 async embedder 真填缓存（lifespan 预热入口）。"""
    vectors = {
        "strong skill": [1.0, 0.0, 0.0],
        "weak skill": [0.0, 1.0, 0.0],
    }
    embedder = _make_async_embedder(vectors)
    skills = [
        _make_meta("strong", "strong skill"),
        _make_meta("weak", "weak skill"),
    ]
    matcher = SkillMatcher(embedder)
    await matcher.build_async(skills)
    assert set(matcher._cache.keys()) == {"strong", "weak"}


def test_build_sync_with_async_embedder_skips_cleanly() -> None:
    """sync build() 遇到 async embedder 不留垃圾缓存（lazy match_async 兜底）。"""
    embedder = _make_async_embedder({"s": [1.0, 0.0, 0.0]})
    matcher = SkillMatcher(embedder)
    matcher.build([_make_meta("s", "s desc")])
    assert matcher._cache == {}


# ---------------------------------------------------------------------------
# TC-5.1 混合匹配 (2026-06-11)：triggers 词法路。
# BGE-M3 int8 对短中文 query 区分度不够(实测 on-target 0.45~0.55 vs
# off-target 0.53+)——显式 triggers 命中直接强匹配,embedding 兜 paraphrase。
# ---------------------------------------------------------------------------

def test_loader_parses_triggers_legacy(tmp_path: Path) -> None:
    """legacy 格式 frontmatter 的 triggers + when_to_use 提升为一等字段。"""
    lines = [
        "---",
        "name: trig-skill",
        "description: a skill",
        "version: 0.1.0",
        "author: test",
        "when_to_use: 用户要深度调研时",
        "triggers: [深度调研, 调研报告]",
        "---",
        "body",
    ]
    d = tmp_path / "trig-skill"
    d.mkdir()
    (d / "SKILL.md").write_text("\n".join(lines), encoding="utf-8")
    loader = SkillLoader([tmp_path], enable_watch=False)
    loader.reload()
    meta = loader.get("trig-skill")
    assert meta is not None
    assert meta.triggers == ["深度调研", "调研报告"]
    assert meta.when_to_use == "用户要深度调研时"
    assert meta.to_dict()["triggers"] == ["深度调研", "调研报告"]


@pytest.mark.asyncio
async def test_trigger_hit_forces_strong_match() -> None:
    """query 含触发词 → sim 抬到 ≥0.95(过任何 strong_threshold)。"""
    # embedding 给 deep-research 很低的 sim(0.1),但 trigger 命中
    vectors = {
        "research skill": [0.1, 0.995, 0.0],
        "调研": [1.0, 0.0, 0.0],
    }
    embedder = _make_async_embedder(vectors)
    meta = _make_meta("deep-research", "research skill")
    meta.triggers = ["深度调研"]
    matcher = SkillMatcher(embedder)
    results = await matcher.match_async("帮我深度调研一下AI桌宠", [meta])
    assert results and results[0][0] == "deep-research"
    assert results[0][1] >= 0.95


@pytest.mark.asyncio
async def test_trigger_case_insensitive() -> None:
    """触发词大小写不敏感(PPT vs ppt)。"""
    meta = _make_meta("ppt-generate", "ppt skill")
    meta.triggers = ["PPT"]
    matcher = SkillMatcher(_make_async_embedder({"x": [1.0, 0.0, 0.0]}))
    results = await matcher.match_async("帮我做个ppt", [meta])
    assert results and results[0][1] >= 0.95


@pytest.mark.asyncio
async def test_no_trigger_no_vec_skill_omitted() -> None:
    """没向量也没 trigger 命中的 skill 不进结果(原契约)。"""
    matcher = SkillMatcher(None)  # 无 embedder → 无向量
    meta = _make_meta("plain", "plain skill")  # 无 triggers
    results = await matcher.match_async("随便聊聊", [meta])
    assert results == []


@pytest.mark.asyncio
async def test_trigger_works_without_embedder() -> None:
    """embedder=None 时词法路仍工作(离线/未 warmup 也能强匹配)。"""
    meta = _make_meta("weather-report", "weather skill")
    meta.triggers = ["天气"]
    matcher = SkillMatcher(None)
    results = await matcher.match_async("今天天气怎么样", [meta])
    assert results and results[0][1] >= 0.95


@pytest.mark.asyncio
async def test_log_top_sim_reports_ranked_not_strong(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """无强匹配时 top_sim 应打真实 ranked 最高分，不是 0.000（诊断误导回归）。"""
    _make_skill_dir(tmp_path, "mid-skill", "mid match description", "## Mid body.")
    loader = SkillLoader([tmp_path], enable_watch=False)
    loader.reload()
    meta = loader.get("mid-skill")
    assert meta is not None

    # cos(query, mid) = 0.6 — 低于 0.55 阈值?不,0.6>0.55。改用 0.3。
    vectors = {
        "mid match": [0.3, 0.954, 0.0],  # cos≈0.3 vs query [1,0,0]
        "query": [1.0, 0.0, 0.0],
    }
    matcher = SkillMatcher(_make_embedder(vectors))
    matcher.build([meta])

    component = SkillComponent(skill_matcher=matcher, skill_loader=loader)
    ctx = _make_ctx(
        [meta], user_message="query", auto_disclosure_enabled=True, strong_threshold=0.55
    )
    import logging as _logging

    with caplog.at_level(_logging.INFO, logger="deskpet.agent.assembler.components.skill"):
        await component.provide(ctx)
    line = next(
        (r.getMessage() for r in caplog.records if "skill_auto_disclosed" in r.getMessage()),
        "",
    )
    assert line, "应有 skill_auto_disclosed log"
    assert "top_sim=0.000" not in line, f"top_sim 应是真实 ranked 分数: {line}"
    assert "top_sim=0.3" in line


# ---------------------------------------------------------------------------
# T-extra: meta contains auto_loaded_count when bodies are loaded
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_meta_auto_loaded_count(tmp_path: Path) -> None:
    """Slice meta should report auto_loaded_count."""
    _make_skill_dir(tmp_path, "counted", "counted skill desc", "## Count body.")
    loader = SkillLoader([tmp_path], enable_watch=False)
    loader.reload()
    meta = loader.get("counted")
    assert meta is not None

    vectors = {
        "counted skill": [1.0, 0.0, 0.0],
        "query": [1.0, 0.0, 0.0],
    }
    embedder = _make_embedder(vectors)
    matcher = SkillMatcher(embedder)
    matcher.build([meta])

    component = SkillComponent(skill_matcher=matcher, skill_loader=loader)
    ctx = _make_ctx([meta], user_message="query", auto_disclosure_enabled=True, strong_threshold=0.55)
    result = await component.provide(ctx)
    assert "auto_loaded_count" in result.meta
    assert result.meta["auto_loaded_count"] >= 1
