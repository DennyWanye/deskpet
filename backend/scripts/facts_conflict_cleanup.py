# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Stage 2 / WI-S2.1a — facts 表 cross-key 矛盾批量清理脚本。

Stage 1 已存在的 facts 表里可能积累了大量"跨 key 矛盾"行（用户原话
说过"花生过敏"后又说"海鲜过敏"，但两条都还 active）。Stage 2 上线
后，新写入靠 cross_key_merge flag 自动治理；老库的存量靠这个脚本批
量清理。

设计要点（PRD §4.1 / R3 v2）：
  * **不进 boot**：单独跑，对老库 batch + LLM
  * `--max-subjects N` 限制扫的 subject 数
  * `--llm-budget N` 限制 LLM 调用次数（防费用失控）
  * `--batch-size N` 每个 subject 内分批喂 LLM
  * Resume token（``.facts_conflict_cleanup.resume.json``）支持断点续跑
  * `--dry-run` 仅打印 will mark superseded 列表，不写 DB
  * 失败幂等：再跑一次能从上次断点继续

用法::

    cd backend
    python -m scripts.facts_conflict_cleanup --dry-run
    python -m scripts.facts_conflict_cleanup --max-subjects 10 --llm-budget 50
    python -m scripts.facts_conflict_cleanup            # 实跑全量
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except AttributeError:
        pass


# Exit codes:
#   0 — completed scan + cleanup
#   2 — exhausted LLM budget (resume token saved)
#   3 — fatal error (DB inaccessible etc.)
EXIT_DONE = 0
EXIT_BUDGET_EXHAUSTED = 2
EXIT_FATAL = 3


_RESUME_TOKEN_PATH = Path(".facts_conflict_cleanup.resume.json")


async def _amain() -> int:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.facts_conflict_cleanup",
        description="Batch-clean cross-key contradictions from facts table",
    )
    parser.add_argument(
        "--db", default="",
        help="state.db path（留空 = paths.user_data_dir()/data/state.db）",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="只打印决策，不改 DB",
    )
    parser.add_argument(
        "--max-subjects", type=int, default=0,
        help="只扫前 N 个 subject（0 = 不限制）",
    )
    parser.add_argument(
        "--batch-size", type=int, default=20,
        help="每个 subject 内每批喂 LLM 的 fact 数（默认 20）",
    )
    parser.add_argument(
        "--llm-budget", type=int, default=0,
        help="最多消耗 N 次 LLM 调用（0 = 不限制）",
    )
    parser.add_argument(
        "--resume", action="store_true",
        help="读 resume token 从上次位置继续",
    )
    parser.add_argument(
        "--no-llm", action="store_true",
        help="不调 LLM，仅打印 same-subject candidate 数（探查用）",
    )
    args = parser.parse_args()

    # Locate db_path —— 留空走 paths.user_data_dir()
    db_path = args.db or _default_db_path()
    if not Path(db_path).exists():
        print(f"[cleanup] FATAL: db not found: {db_path}", file=sys.stderr)
        return EXIT_FATAL

    # Resume state
    resume_state: dict[str, Any] = {}
    if args.resume and _RESUME_TOKEN_PATH.exists():
        try:
            resume_state = json.loads(_RESUME_TOKEN_PATH.read_text("utf-8"))
            print(f"[cleanup] resume from: {resume_state}")
        except (OSError, json.JSONDecodeError):
            resume_state = {}

    subjects_done: set[str] = set(resume_state.get("subjects_done", []))
    llm_used = int(resume_state.get("llm_used", 0))

    # Lazy import — sys.path is set above.
    from deskpet.memory.facts import (
        FactsStore, FactExtractor, _CROSS_KEY_CONFLICT_PROMPT,
        _parse_cross_key_decision,
    )

    store = FactsStore(db_path)
    # Get all subjects (group facts table)
    subjects = await _list_subjects(db_path)
    print(f"[cleanup] {len(subjects)} subjects in facts table")
    if args.max_subjects > 0:
        subjects = subjects[: args.max_subjects]
        print(f"[cleanup] limiting to first {len(subjects)} subjects")

    if args.no_llm:
        for subj in subjects:
            if subj in subjects_done:
                continue
            cnt = await _count_active(db_path, subj)
            print(f"  - subject={subj!r}: {cnt} active facts")
        return EXIT_DONE

    # Build a thin LLM call layer — use OPENAI_API_KEY / OPENAI_BASE_URL env
    # or fail loudly so user knows the script can't run.
    llm_call = _build_llm_call()
    if llm_call is None:
        print(
            "[cleanup] FATAL: 无可用 LLM provider — 设 OPENAI_API_KEY 或"
            " 调整 _build_llm_call()", file=sys.stderr,
        )
        return EXIT_FATAL

    total_marked = 0
    for subj in subjects:
        if subj in subjects_done:
            continue
        if args.llm_budget and llm_used >= args.llm_budget:
            print(
                f"[cleanup] LLM budget {args.llm_budget} exhausted; "
                f"saving resume token", file=sys.stderr,
            )
            _save_resume(subjects_done=subjects_done, llm_used=llm_used)
            return EXIT_BUDGET_EXHAUSTED
        try:
            marked, llm_calls = await _clean_subject(
                store=store,
                subject=subj,
                batch_size=args.batch_size,
                llm_call=llm_call,
                dry_run=args.dry_run,
                budget_remaining=(
                    args.llm_budget - llm_used if args.llm_budget else None
                ),
            )
        except Exception as exc:  # noqa: BLE001
            print(
                f"[cleanup] subject={subj!r} failed: {exc}",
                file=sys.stderr,
            )
            continue
        llm_used += llm_calls
        total_marked += marked
        subjects_done.add(subj)
        print(
            f"[cleanup] subject={subj!r}: marked {marked} superseded, "
            f"llm_used={llm_used}{'/' + str(args.llm_budget) if args.llm_budget else ''}"
        )

    # Cleanup resume token on full completion.
    if _RESUME_TOKEN_PATH.exists():
        try:
            _RESUME_TOKEN_PATH.unlink()
        except OSError:
            pass
    print(
        f"[cleanup] DONE: {total_marked} facts marked superseded across "
        f"{len(subjects_done)} subjects (llm calls: {llm_used})"
    )
    return EXIT_DONE


async def _clean_subject(
    *,
    store: Any,
    subject: str,
    batch_size: int,
    llm_call: Any,
    dry_run: bool,
    budget_remaining: Optional[int],
) -> tuple[int, int]:
    """Scan one subject. Return (rows marked superseded, llm calls used)."""
    from deskpet.memory.facts import (
        _CROSS_KEY_CONFLICT_PROMPT, _parse_cross_key_decision,
    )

    active = await store.list_active(subject=subject, limit=10000)
    if len(active) < 2:
        return 0, 0

    # Batch the subject's facts; treat batch[0] as "new" and the rest as
    # "existing" candidates. Iterate windows so every pair is considered.
    marked = 0
    llm_calls = 0
    for i, new_fact in enumerate(active):
        if budget_remaining is not None and llm_calls >= budget_remaining:
            break
        candidates = [r for r in active if r["id"] != new_fact["id"]][:batch_size]
        if not candidates:
            continue
        # 复用 fact prompt 的格式（拼字符串候选行）
        now = time.time()
        lines = []
        for r in candidates:
            updated = float(r.get("updated_at") or 0.0)
            ago = max(0.0, (now - updated) / 86400.0)
            lines.append(
                f"  - id={r['id']} key={r['key']!r} "
                f"value={str(r.get('value',''))[:80]!r} "
                f"updated_ago_days={ago:.1f}"
            )
        prompt = _CROSS_KEY_CONFLICT_PROMPT.format(
            new_category=new_fact["category"],
            new_subject=new_fact["subject"],
            new_key=new_fact["key"],
            new_value=new_fact["value"],
            candidates="\n".join(lines),
        )
        try:
            raw = await llm_call(prompt)
        except Exception as exc:  # noqa: BLE001
            print(
                f"  ! llm error for fact_id={new_fact['id']}: {exc}",
                file=sys.stderr,
            )
            continue
        llm_calls += 1
        decision = _parse_cross_key_decision(raw)
        existing_ids = {int(r["id"]) for r in candidates}
        for entry in decision.conflicts:
            old_id_raw = entry.get("old_id") if isinstance(entry, dict) else None
            if not isinstance(old_id_raw, int):
                continue
            if old_id_raw not in existing_ids:
                continue
            print(
                f"  -> mark old={old_id_raw} superseded_by={new_fact['id']} "
                f"(reason: {entry.get('reason','')[:80]})"
            )
            if not dry_run:
                await store.mark_superseded(
                    old_id=old_id_raw, superseded_by=int(new_fact["id"]),
                )
                marked += 1
    return marked, llm_calls


async def _list_subjects(db_path: str | Path) -> list[str]:
    import aiosqlite
    async with aiosqlite.connect(db_path) as conn:
        cur = await conn.execute(
            "SELECT DISTINCT subject FROM facts "
            "WHERE is_active = 1 ORDER BY subject"
        )
        rows = await cur.fetchall()
        await cur.close()
    return [str(r[0]) for r in rows if r[0]]


async def _count_active(db_path: str | Path, subject: str) -> int:
    import aiosqlite
    async with aiosqlite.connect(db_path) as conn:
        cur = await conn.execute(
            "SELECT COUNT(*) FROM facts WHERE is_active = 1 AND subject = ?",
            (subject,),
        )
        row = await cur.fetchone()
        await cur.close()
    return int(row[0]) if row else 0


def _save_resume(*, subjects_done: set[str], llm_used: int) -> None:
    payload = {
        "subjects_done": sorted(subjects_done),
        "llm_used": int(llm_used),
        "saved_at": time.time(),
    }
    try:
        _RESUME_TOKEN_PATH.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"[cleanup] resume token saved → {_RESUME_TOKEN_PATH}")
    except OSError as exc:
        print(f"[cleanup] WARNING: failed to save resume token: {exc}",
              file=sys.stderr)


def _default_db_path() -> str:
    """Default to paths.user_data_dir()/data/state.db (matches main.py)."""
    try:
        import paths as _paths
        return str(_paths.user_data_dir() / "data" / "state.db")
    except Exception:  # noqa: BLE001
        return "./data/state.db"


def _build_llm_call():
    """Construct a minimal LLM call: prompt -> str.

    Looks for OPENAI_API_KEY / OPENAI_BASE_URL in env. Returns None if no
    usable provider is configured. Production users may patch this for
    deepseek-chat etc.
    """
    import os
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None
    base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
    model = os.environ.get("CLEANUP_MODEL", "deepseek-chat")
    try:
        import httpx
    except ImportError:
        return None

    async def _call(prompt: str) -> str:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.0,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]

    return _call


def main() -> int:
    return asyncio.run(_amain())


if __name__ == "__main__":
    sys.exit(main())
