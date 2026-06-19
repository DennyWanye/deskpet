# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""WI-4.3 — 技能自创闭环（后端核心）。

公开 API
--------
detect_trigger(path, *, degraded_mode=False) -> bool
    判断一条 ToolPath 是否应触发候选技能生成。

render_skill_md(candidate) -> str
    把候选 dict 渲染为标准 SKILL.md 文本（frontmatter + body）。
    requires_script 硬编码 false。

class SkillCandidateStore
    sqlite-backed pending 候选 CRUD。pending 记录**不进** SkillMemoryStore
    的 skill_memory 表，因此不出现在 recall/list_all 结果里，不影响
    skill_prelude 组装。

class SkillCodifier
    propose(path) -> int|None  — 检测 + 生成 + 写 pending
    confirm(cid, accept)       — accept=True: 落盘 SKILL.md + reload
                                 accept=False: 删 pending

class SkillCandidateWaiters
    main.py 用的 Future-await 字典（复制 _PLAN_CONFIRM_WAITERS 模式）。

设计约束（WI-4.3 §5）
----------------------
- 不生成可执行 script.py；requires_script 硬编码 false。
- 不照搬 hermes 自动执行；所有技能必须通过用户确认门才落盘。
- flag OFF = 字节级 BC（codifier 不构造 / propose 短路返回 None）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

import aiosqlite

log = logging.getLogger(__name__)

_LLMCall = Callable[[str], Awaitable[str]]

# ---------------------------------------------------------------------------
# DDL for pending_skill_candidates (独立表，不污染 skill_memory)
# ---------------------------------------------------------------------------
_DDL_PENDING = """
CREATE TABLE IF NOT EXISTS pending_skill_candidates (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL,
    description     TEXT    NOT NULL,
    trigger_pattern TEXT,
    steps_json      TEXT    NOT NULL DEFAULT '[]',
    status          TEXT    NOT NULL DEFAULT 'pending',
    created_at      REAL    NOT NULL
);
"""

# ---------------------------------------------------------------------------
# Prompt for candidate generation
# ---------------------------------------------------------------------------
_CODIFY_PROMPT = """\
You are a skill-extraction assistant for a desktop pet AI.
Given a completed workflow (tool call sequence + goal), extract a reusable skill.

Output ONLY valid JSON with these keys:
  name         - kebab-case identifier, no spaces (e.g. "weather-ppt-report")
  description  - one sentence describing what the skill does (English or Chinese)
  trigger_pattern - when should this skill be auto-selected (one phrase)
  steps        - list of 2-8 short step strings describing the workflow

Rules:
- name and description MUST be non-empty
- steps MUST have at least 2 items
- Do NOT include "requires_script" in your output (it is always false)
- Output ONLY the JSON object, no prose, no markdown fences

Goal: {goal_text}
Tools used (in order): {tools_list}
Has recovery (tool failed then retried successfully): {has_recovery}
Has user correction: {has_correction}

JSON:"""


# ---------------------------------------------------------------------------
# detect_trigger
# ---------------------------------------------------------------------------

def detect_trigger(path: "ToolPath", *, degraded_mode: bool = False) -> bool:  # noqa: F821
    """Return True if ToolPath meets any hermes trigger condition.

    Trigger conditions (satisfy ANY):
    1. >= 5 tool calls total
    2. recovered_from_error (any step with recovered=True) — full mode only
    3. corrected by user (any step with corrected=True) — full mode only
    4. non-obvious workflow (>= 3 distinct tool names in a multi-step sequence)

    degraded_mode=True (no corrected/recovered signals available):
    Only conditions 1 and 4 apply.
    """
    if not path.steps:
        return False

    total_steps = len(path.steps)
    distinct_tools = len({s.name for s in path.steps})

    # Condition 1: >= 5 tool calls
    if total_steps >= 5:
        return True

    # Conditions 2 & 3 only in full mode
    if not degraded_mode:
        # Condition 2: any step recovered from error
        if any(s.recovered for s in path.steps):
            return True
        # Condition 3: any step corrected by user
        if any(s.corrected for s in path.steps):
            return True

    # Condition 4: non-obvious workflow — >= 3 distinct tools in specific order
    # (i.e. the workflow is genuinely multi-tool, not just one tool called repeatedly)
    if distinct_tools >= 3:
        return True

    return False


# ---------------------------------------------------------------------------
# render_skill_md
# ---------------------------------------------------------------------------

def _slugify(name: str) -> str:
    """Convert name to a safe filesystem slug."""
    s = name.lower().strip()
    s = re.sub(r"[^\w\-]", "-", s)
    s = re.sub(r"-+", "-", s)
    return s.strip("-") or "skill"


def render_skill_md(candidate: dict[str, Any]) -> str:
    """Render a candidate dict to SKILL.md text.

    Frontmatter fields: name / description / version / author=self-codified /
    when_to_use / requires_script: false (HARDCODED).
    Body: numbered step list.
    """
    name = candidate.get("name", "")
    description = candidate.get("description", "")
    trigger_pattern = candidate.get("trigger_pattern", "")
    steps = candidate.get("steps", [])

    # Escape YAML special chars in description / trigger_pattern
    def _esc(s: str) -> str:
        s = str(s)
        if any(c in s for c in (":", "#", "[", "]", "{", "}", ",")):
            return f'"{s}"'
        return s

    body_steps = "\n".join(f"{i+1}. {step}" for i, step in enumerate(steps))

    frontmatter = (
        "---\n"
        f"name: {_slugify(name)}\n"
        f"description: {_esc(description)}\n"
        "version: 1.0.0\n"
        "author: self-codified\n"
        f"when_to_use: {_esc(trigger_pattern)}\n"
        # FP-5 缺口 5g (2026-06-06 真机抓 bug)：codify 出的技能从 code 会话工具
        # 路径生成，但原 frontmatter 漏 task_types → SkillLoader.select(task_type)
        # 按 task_types 过滤时永远排除它 → 自动披露（WI-4.1/4.2）永远召回不到
        # 自己造的技能（codify 造、disclosure 召不回，FP-5 闭环断裂）。标注 code/task。
        "task_types: [code, task]\n"
        "requires_script: false\n"
        "---\n"
    )
    body = f"# {name}\n\n{body_steps}\n"
    return frontmatter + body


# ---------------------------------------------------------------------------
# SkillCandidateStore
# ---------------------------------------------------------------------------

class SkillCandidateStore:
    """Pending skill candidate CRUD — stored in a separate table from skill_memory.

    pending 候选不进 skill_memory，不影响 recall / list_all / skill_prelude。
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)

    async def _ensure_table(self) -> None:
        async with aiosqlite.connect(self._db_path) as conn:
            await conn.executescript(_DDL_PENDING)
            await conn.commit()

    async def write_pending(self, candidate: dict[str, Any]) -> int:
        """Insert a pending candidate. Returns the new row id."""
        await self._ensure_table()
        now = time.time()
        async with aiosqlite.connect(self._db_path) as conn:
            cur = await conn.execute(
                "INSERT INTO pending_skill_candidates"
                "(name, description, trigger_pattern, steps_json, status, created_at)"
                " VALUES (?, ?, ?, ?, 'pending', ?)",
                (
                    str(candidate.get("name", "")),
                    str(candidate.get("description", "")),
                    str(candidate.get("trigger_pattern", "") or ""),
                    json.dumps(list(candidate.get("steps", []))),
                    now,
                ),
            )
            new_id = int(cur.lastrowid or 0)
            await cur.close()
            await conn.commit()
        return new_id

    async def fetch_pending(self, candidate_id: int) -> Optional[dict[str, Any]]:
        """Return the pending candidate row as a dict, or None if not found."""
        await self._ensure_table()
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                "SELECT * FROM pending_skill_candidates WHERE id = ?",
                (int(candidate_id),),
            )
            row = await cur.fetchone()
            await cur.close()
        if row is None:
            return None
        d = dict(row)
        try:
            d["steps"] = json.loads(d.get("steps_json") or "[]")
        except json.JSONDecodeError:
            d["steps"] = []
        return d

    async def delete_pending(self, candidate_id: int) -> bool:
        """Delete a pending candidate. Returns True if a row was deleted."""
        await self._ensure_table()
        async with aiosqlite.connect(self._db_path) as conn:
            cur = await conn.execute(
                "DELETE FROM pending_skill_candidates WHERE id = ?",
                (int(candidate_id),),
            )
            n = cur.rowcount or 0
            await cur.close()
            await conn.commit()
        return bool(n)

    async def list_all_pending(self) -> list[dict[str, Any]]:
        """List all pending candidates (for rehydration / debug)."""
        await self._ensure_table()
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                "SELECT * FROM pending_skill_candidates "
                "WHERE status = 'pending' ORDER BY created_at DESC"
            )
            rows = await cur.fetchall()
            await cur.close()
        result = []
        for row in rows:
            d = dict(row)
            try:
                d["steps"] = json.loads(d.get("steps_json") or "[]")
            except json.JSONDecodeError:
                d["steps"] = []
            result.append(d)
        return result


# ---------------------------------------------------------------------------
# SkillCandidateWaiters  (Future-await channel, mirrors _PLAN_CONFIRM_WAITERS)
# ---------------------------------------------------------------------------

class SkillCandidateWaiters:
    """Per-candidate-id Future store for the skill_candidate_confirm WS flow.

    Usage in main.py::

        _SKILL_CANDIDATE_WAITERS = SkillCandidateWaiters()

        # Producer side (after propose() returns a cid):
        fut = asyncio.get_event_loop().create_future()
        _SKILL_CANDIDATE_WAITERS.add(cid, fut)
        decision = await asyncio.wait_for(fut, timeout=300)

        # Consumer side (WS handler skill_candidate_confirm):
        _SKILL_CANDIDATE_WAITERS.resolve(cid, "accept" | "reject")
    """

    def __init__(self) -> None:
        self._waiters: dict[int, asyncio.Future] = {}

    def add(self, candidate_id: int, fut: asyncio.Future) -> None:
        self._waiters[int(candidate_id)] = fut

    def resolve(self, candidate_id: int, decision: str) -> None:
        """Set result on the future keyed by candidate_id. No-op if not found."""
        fut = self._waiters.pop(int(candidate_id), None)
        if fut is not None and not fut.done():
            fut.set_result(decision)

    def get(self, candidate_id: int) -> Optional[asyncio.Future]:
        return self._waiters.get(int(candidate_id))

    def pop(self, candidate_id: int) -> Optional[asyncio.Future]:
        return self._waiters.pop(int(candidate_id), None)


# ---------------------------------------------------------------------------
# SkillCodifier
# ---------------------------------------------------------------------------

class SkillCodifier:
    """Orchestrates trigger detection → candidate generation → confirm/reject.

    Parameters
    ----------
    candidate_store:
        SkillCandidateStore — pending CRUD.
    user_skill_dir:
        Path to the user skill directory (e.g. ``<user_data>/skills/user``).
    llm_call:
        Async callable (prompt: str) -> str. Used to generate the candidate JSON.
    skill_loader:
        Optional SkillLoader; if supplied, ``reload()`` is called after SKILL.md
        is written so the new skill is immediately available.
    degraded_mode:
        When True, corrected/recovered signals are ignored and only the ≥5 tools
        and non-obvious triggers apply. Set when P0-1 ToolPath signals aren't
        available.
    """

    def __init__(
        self,
        candidate_store: SkillCandidateStore,
        user_skill_dir: Path,
        llm_call: _LLMCall,
        *,
        skill_loader: Optional[Any] = None,
        degraded_mode: bool = False,
    ) -> None:
        self._store = candidate_store
        self._user_dir = Path(user_skill_dir)
        self._llm = llm_call
        self._loader = skill_loader
        self._degraded = degraded_mode

    async def propose(self, path: "ToolPath") -> Optional[int]:  # noqa: F821
        """Detect trigger → call LLM → validate → write pending → return candidate_id.

        Returns None if the trigger doesn't fire, the LLM response is empty/invalid,
        or the candidate fails validation (steps < 2, empty name/description).
        Safe-fail: never raises (logs at debug level on failure).
        """
        try:
            if not detect_trigger(path, degraded_mode=self._degraded):
                return None

            candidate = await self._generate_candidate(path)
            if candidate is None:
                return None

            cid = await self._store.write_pending(candidate)
            log.info(
                "skill_codifier.proposed cid=%d name=%s steps=%d",
                cid, candidate.get("name"), len(candidate.get("steps", [])),
            )
            return cid
        except Exception as exc:  # noqa: BLE001
            log.debug("skill_codifier.propose_failed error=%s", exc)
            return None

    async def confirm(self, candidate_id: int, *, accept: bool) -> bool:
        """Process user confirm/reject for a pending candidate.

        accept=True:
          - Fetches the pending candidate
          - Renders SKILL.md  (requires_script=false hardcoded)
          - Resolves slug conflicts with -v2 suffix
          - Writes <user_dir>/<slug>/SKILL.md
          - Calls skill_loader.reload() if wired
          - Deletes the pending entry

        accept=False:
          - Deletes the pending entry
          - No file written

        Returns True on success (including the accept=False path), False if
        the candidate_id is not found.
        """
        try:
            pending = await self._store.fetch_pending(candidate_id)
            if pending is None:
                log.debug("skill_codifier.confirm_no_pending cid=%d", candidate_id)
                return False

            if not accept:
                await self._store.delete_pending(candidate_id)
                log.info("skill_codifier.rejected cid=%d", candidate_id)
                return True

            # accept=True path
            skill_md_text = render_skill_md(pending)

            # Resolve target directory (handle slug collision)
            slug = _slugify(pending.get("name", "skill"))
            target_dir = self._resolve_skill_dir(slug)
            target_dir.mkdir(parents=True, exist_ok=True)
            skill_md_path = target_dir / "SKILL.md"
            skill_md_path.write_text(skill_md_text, encoding="utf-8")

            log.info("skill_codifier.written path=%s", skill_md_path)

            # Reload so the skill is immediately available
            if self._loader is not None:
                try:
                    self._loader.reload()
                except Exception as exc:  # noqa: BLE001
                    log.warning("skill_codifier.reload_failed error=%s", exc)

            await self._store.delete_pending(candidate_id)
            return True

        except Exception as exc:  # noqa: BLE001
            log.debug("skill_codifier.confirm_failed cid=%d error=%s", candidate_id, exc)
            return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _generate_candidate(self, path: "ToolPath") -> Optional[dict[str, Any]]:  # noqa: F821
        """Call LLM to generate a candidate dict. Validates and returns or None."""
        tools_list = ", ".join(s.name for s in path.steps)
        has_recovery = any(s.recovered for s in path.steps)
        has_correction = any(s.corrected for s in path.steps)

        prompt = _CODIFY_PROMPT.format(
            goal_text=path.goal_text,
            tools_list=tools_list,
            has_recovery=str(has_recovery),
            has_correction=str(has_correction),
        )
        try:
            raw = await self._llm(prompt)
        except Exception as exc:  # noqa: BLE001
            log.debug("skill_codifier.llm_failed error=%s", exc)
            return None

        if not raw or not raw.strip():
            return None

        # Try to parse JSON — strip optional markdown fences
        text = raw.strip()
        if text.startswith("```"):
            # strip ```json ... ``` fences
            text = re.sub(r"^```[a-z]*\n?", "", text)
            text = re.sub(r"\n?```$", "", text)
        try:
            candidate = json.loads(text)
        except json.JSONDecodeError:
            log.debug("skill_codifier.json_parse_failed raw=%r", text[:200])
            return None

        if not isinstance(candidate, dict):
            return None

        # Validation
        name = str(candidate.get("name", "") or "").strip()
        description = str(candidate.get("description", "") or "").strip()
        steps = candidate.get("steps", [])
        if not isinstance(steps, list):
            steps = []

        if not name:
            log.debug("skill_codifier.invalid_candidate: empty name")
            return None
        if not description:
            log.debug("skill_codifier.invalid_candidate: empty description")
            return None
        if len(steps) < 2:
            log.debug("skill_codifier.invalid_candidate: steps < 2 (got %d)", len(steps))
            return None

        return {
            "name": name,
            "description": description,
            "trigger_pattern": str(candidate.get("trigger_pattern", "") or ""),
            "steps": [str(s) for s in steps],
        }

    def _resolve_skill_dir(self, slug: str) -> Path:
        """Return target dir, appending -v2 / -v3 ... to avoid collisions."""
        base = self._user_dir / slug
        if not base.exists():
            return base
        suffix = 2
        while True:
            candidate_dir = self._user_dir / f"{slug}-v{suffix}"
            if not candidate_dir.exists():
                return candidate_dir
            suffix += 1


__all__ = [
    "detect_trigger",
    "render_skill_md",
    "SkillCandidateStore",
    "SkillCandidateWaiters",
    "SkillCodifier",
]
