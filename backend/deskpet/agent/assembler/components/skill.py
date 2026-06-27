# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Skill component (P4-S7 task 12.5, WI-4.1 upgrade).

Emits a "skill prelude" containing:
  1. **Desc list** (priority 85, never cut) — always present when skills exist.
  2. **Body段** (can be cut) — auto-inlined bodies for strong-matching skills
     when ``config.skills.auto_disclosure.enabled`` is True.

When the flag is off (default), only the desc list is emitted — byte-identical
to the pre-WI-4.1 behavior.

3-tier logic (flag on):
  - **Strong match** (cos-sim ≥ strong_threshold, default 0.55):
    inline skill body into prelude (truncated to per_skill_max_tokens).
  - **Weak / all others**: name + desc only.
  - ``disable_model_invocation`` skills are never auto-loaded.
  - Budget: fill by sim desc; overflow drops least-recently-used
    (``meta["usage_count"]`` tie-break; no usage → pure sim order).

Output goes into the ``"skill"`` bucket (``skill_prelude`` on the bundle)
which sits between ``frozen_system`` and ``memory_block``.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

from deskpet.agent.assembler.bundle import Slice
from deskpet.agent.assembler.components.base import Component, ComponentContext
from deskpet.agent.tokens import count_text_tokens

logger = logging.getLogger(__name__)


# 粗略 char 截断预算用的 chars/token 比（仅用于反向算 max_chars 截断长度，
# 不是 token 计数口径；真正的 token 计数走 tokens.count_text_tokens）。
_CHARS_PER_TOKEN = 4

# Default config values (mirror SkillsAutoDisclosureConfig defaults).
_DEFAULT_STRONG_THRESHOLD = 0.55
_DEFAULT_BUDGET_TOKENS = 8000
_DEFAULT_PER_SKILL_MAX_TOKENS = 2000


class SkillComponent:
    """Emits a skill prelude block when skills are registered.

    Parameters
    ----------
    skill_matcher:
        Optional :class:`~deskpet.skills.skill_matcher.SkillMatcher`. When
        provided and the auto-disclosure flag is on, performs embedding
        similarity matching to decide which skills get their body inlined.
    skill_loader:
        Optional :class:`~deskpet.skills.loader.SkillLoader`. Used to call
        ``read_body(name)`` for body inlining. When absent body inlining is
        silently skipped (desc-only).
    """

    name: str = "skill"

    def __init__(
        self,
        *,
        skill_matcher: Optional[Any] = None,
        skill_loader: Optional[Any] = None,
    ) -> None:
        self._matcher = skill_matcher
        self._loader = skill_loader

    async def provide(self, ctx: ComponentContext) -> Slice:
        start = time.monotonic()
        registry = ctx.skill_registry
        if registry is None:
            return Slice(
                component_name=self.name,
                priority=70,
                bucket="skill",
                meta={"status": "no_registry"},
            )

        # Read config for auto-disclosure first — it decides the skill venue.
        ad_cfg = _read_auto_disclosure_config(ctx.config)
        auto_enabled = ad_cfg.get("enabled", False)
        knowledge_enabled = _read_knowledge_enabled_config(ctx.config)

        # Fetch skill list from registry.
        #
        # TC-5.1 真机回归 (2026-06-11)：``select(task_type)`` 按 task_types
        # frontmatter 过滤，而 builtin claude-code-v1 skill 全是 task_types=[]
        # → 全被滤掉（真机 total=1）。auto-disclosure 的契约是「全集进 desc
        # list + embedding 决定强匹配」→ flag ON 用 ``all()`` 全集；flag OFF
        # 保持 select 路径（字节级 BC：不泄露全集进 chat prelude）。
        skills: list[Any]
        try:
            if auto_enabled and hasattr(registry, "all"):
                maybe = registry.all()
                skills = await maybe if hasattr(maybe, "__await__") else maybe
            elif hasattr(registry, "select"):
                maybe = registry.select(
                    ctx.task_type, prefer=list(ctx.policy.prefer)
                )
                skills = await maybe if hasattr(maybe, "__await__") else maybe
            elif hasattr(registry, "all"):
                maybe = registry.all()
                skills = await maybe if hasattr(maybe, "__await__") else maybe
            else:
                skills = []
        except Exception as exc:
            return Slice(
                component_name=self.name,
                priority=70,
                bucket="skill",
                meta={"error": str(exc), "error_type": type(exc).__name__},
            )

        if not skills:
            return Slice(
                component_name=self.name,
                priority=70,
                bucket="skill",
                meta={
                    "count": 0,
                    "auto_loaded_count": 0,
                    "knowledge_loaded_count": 0,
                },
            )

        if not knowledge_enabled:
            skills = [s for s in skills if _is_user_invocable(s)]
            if not skills:
                return Slice(
                    component_name=self.name,
                    priority=70,
                    bucket="skill",
                    meta={
                        "count": 0,
                        "auto_loaded_count": 0,
                        "knowledge_loaded_count": 0,
                    },
                )

        # -----------------------------------------------------------------
        # Build desc list (always — priority 85, not cuttable).
        # -----------------------------------------------------------------
        desc_lines = ["## 可用技能"]
        visible_skills = [s for s in skills if _is_user_invocable(s)]
        for s in visible_skills:
            sname = _skill_attr(s, "name", "?")
            summary = _skill_attr(s, "summary", "") or _skill_attr(s, "description", "")
            if summary:
                desc_lines.append(f"- **{sname}**: {summary}")
            else:
                desc_lines.append(f"- **{sname}**")
        desc_text = "\n".join(desc_lines) if visible_skills else ""

        # When flag is off → emit desc list only (byte-identical to pre-WI-4.1).
        if not auto_enabled or self._matcher is None or self._loader is None:
            elapsed_ms = (time.monotonic() - start) * 1000.0
            return Slice(
                component_name=self.name,
                text_content=desc_text,
                tokens=count_text_tokens(desc_text) if desc_text else 0,
                priority=85,
                bucket="skill",
                meta={
                    "count": len(visible_skills),
                    "latency_ms": round(elapsed_ms, 2),
                    "auto_loaded_count": 0,
                    "knowledge_loaded_count": 0,
                },
            )

        # -----------------------------------------------------------------
        # Auto-disclosure: embedding match + body inlining.
        # -----------------------------------------------------------------
        strong_threshold = float(ad_cfg.get("strong_threshold", _DEFAULT_STRONG_THRESHOLD))
        budget_tokens = int(ad_cfg.get("budget_tokens", _DEFAULT_BUDGET_TOKENS))
        per_skill_max_tokens = int(
            ad_cfg.get("per_skill_max_tokens", _DEFAULT_PER_SKILL_MAX_TOKENS)
        )

        # Build query: user message (add recent history excerpt if useful).
        query = ctx.user_message or ""

        # Get similarity scores (async — avoids blocking event loop).
        try:
            ranked: list[tuple[str, float]] = await self._matcher.match_async(query, skills)
        except Exception:  # noqa: BLE001
            ranked = []

        # Filter to strong matches; skip disable_model_invocation skills.
        strong_matches = [
            (nm, sim)
            for nm, sim in ranked
            if sim >= strong_threshold
            and not _is_disabled_for_model(nm, skills)
        ]

        # Budget-fill loop: sort by sim desc; tie-break by usage_count desc
        # (higher usage = retained first; lower = dropped first).
        strong_matches = _sort_by_sim_then_usage(strong_matches, skills)

        auto_loaded_count = 0
        knowledge_loaded_count = 0
        body_sections: list[str] = []
        used_tokens = 0

        for nm, sim in strong_matches:
            if used_tokens >= budget_tokens:
                break
            try:
                raw_body = self._loader.read_body(nm)
            except Exception:  # noqa: BLE001
                continue
            # Per-skill truncation
            max_chars = per_skill_max_tokens * _CHARS_PER_TOKEN
            if len(raw_body) > max_chars:
                raw_body = raw_body[:max_chars] + "\n…（已截断）"
            body_tokens = count_text_tokens(raw_body)
            if used_tokens + body_tokens > budget_tokens:
                break
            is_knowledge = _is_knowledge(nm, skills)
            heading = "知识片段（自动注入）" if is_knowledge else "技能正文（自动预载）"
            body_sections.append(
                f"### {nm} {heading}\n{raw_body}"
            )
            used_tokens += body_tokens
            auto_loaded_count += 1
            if is_knowledge:
                knowledge_loaded_count += 1

        # -----------------------------------------------------------------
        # Compose final prelude text.
        # -----------------------------------------------------------------
        parts = [desc_text] if desc_text else []
        if body_sections:
            parts.append("\n---\n以下内容已按触发词预载，无需再 skill_invoke：")
            parts.extend(body_sections)

        text = "\n\n".join(parts)
        elapsed_ms = (time.monotonic() - start) * 1000.0
        # Observability (FP-5 WI-4.1/4.2): auto-disclosure is otherwise invisible in
        # logs — emit which skills were strong-matched + body-inlined so real-machine
        # acceptance (TC-5.1/5.8) has a hard evidence line.
        # top_sim 必须打真实 ranked 最高分（不是 strong_matches[0]）——否则
        # 「有 0.4 的相似度但没过阈值」和「embedding 全坏零向量」在 log 里
        # 都显示 top_sim=0.000，真机诊断会被误导（TC-5.1 教训）。
        logger.info(
            "skill_auto_disclosed total=%d strong=%d auto_loaded=%d names=%s top_sim=%.3f",
            len(skills),
            len(strong_matches),
            auto_loaded_count,
            [nm for nm, _ in strong_matches[:5]],
            (ranked[0][1] if ranked else 0.0),
        )
        return Slice(
            component_name=self.name,
            text_content=text,
            tokens=count_text_tokens(text),
            priority=85,  # desc list is never cut; body section can be trimmed externally
            bucket="skill",
            meta={
                "count": len(visible_skills),
                "auto_loaded_count": auto_loaded_count,
                "knowledge_loaded_count": knowledge_loaded_count,
                "triggered": auto_loaded_count > 0,
                "protected": knowledge_loaded_count > 0,
                "latency_ms": round(elapsed_ms, 2),
            },
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _skill_attr(s: Any, attr: str, default: Any = None) -> Any:
    if isinstance(s, dict):
        return s.get(attr, default)
    return getattr(s, attr, default)


def _is_disabled_for_model(name: str, skills: list[Any]) -> bool:
    """Return True if the skill has disable_model_invocation set."""
    for s in skills:
        if _skill_attr(s, "name", "") == name:
            return bool(_skill_attr(s, "disable_model_invocation", False))
    return False


def _is_user_invocable(skill: Any) -> bool:
    return bool(_skill_attr(skill, "user_invocable", True))


def _is_knowledge(name: str, skills: list[Any]) -> bool:
    for s in skills:
        if _skill_attr(s, "name", "") == name:
            return not _is_user_invocable(s)
    return False


def _get_usage_count(name: str, skills: list[Any]) -> int:
    """Return usage_count from skill meta dict, or 0."""
    for s in skills:
        if _skill_attr(s, "name", "") == name:
            meta = _skill_attr(s, "meta", {}) or {}
            if isinstance(meta, dict):
                return int(meta.get("usage_count", 0))
    return 0


def _sort_by_sim_then_usage(
    ranked: list[tuple[str, float]], skills: list[Any]
) -> list[tuple[str, float]]:
    """Sort strong matches: primary = sim desc; tie-break = usage_count desc."""
    return sorted(
        ranked,
        key=lambda x: (x[1], _get_usage_count(x[0], skills)),
        reverse=True,
    )


def _read_auto_disclosure_config(config: Any) -> dict[str, Any]:
    """Extract auto_disclosure sub-dict from the component context config.

    Accepts:
      * ``dict`` with key ``"skills"`` → ``{"auto_disclosure": {...}}``
      * ``AppConfig`` instance with ``.skills.auto_disclosure`` dataclass
      * Anything else → returns empty dict (degrade to desc-only).
    """
    if isinstance(config, dict):
        skills_cfg = config.get("skills")
        if isinstance(skills_cfg, dict):
            ad = skills_cfg.get("auto_disclosure")
            if isinstance(ad, dict):
                return ad
        return {}
    # AppConfig dataclass path
    try:
        skills_obj = config.skills
        ad_obj = skills_obj.auto_disclosure
        return {
            "enabled": ad_obj.enabled,
            "strong_threshold": ad_obj.strong_threshold,
            "budget_tokens": ad_obj.budget_tokens,
            "per_skill_max_tokens": ad_obj.per_skill_max_tokens,
        }
    except AttributeError:
        return {}


def _read_knowledge_enabled_config(config: Any) -> bool:
    """Return [skills].knowledge_enabled, defaulting to False for BC."""
    if isinstance(config, dict):
        skills_cfg = config.get("skills")
        if isinstance(skills_cfg, dict):
            return bool(skills_cfg.get("knowledge_enabled", False))
        return False
    try:
        return bool(config.skills.knowledge_enabled)
    except AttributeError:
        return False


_ASSERT_PROTOCOL: Component = SkillComponent()
