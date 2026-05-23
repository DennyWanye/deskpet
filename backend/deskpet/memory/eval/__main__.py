"""``python -m deskpet.memory.eval`` CLI.

Two subcommands:

* ``build`` — generate a QA set from existing messages
* ``run``   — replay the QA set against the live Retriever, print
  metrics, persist to ``memory_eval_run``

We deliberately keep the CLI small (no argparse subparsers proliferation).
For automation / CI, prefer the Python API in ``qaset.py`` / ``metrics.py``.

Usage::

    python -m deskpet.memory.eval build --n 50
    python -m deskpet.memory.eval run

Env / config inputs are read via ``deskpet.config.load_config`` so the
same ``state.db`` path / LLM provider as the live backend are used.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


async def _resolve_state_db() -> Path:
    """Resolve state.db path the same way main.py does.

    记忆系统升级修复：① ``config`` / ``providers`` 是 backend 根目录的
    顶层模块，不在 ``deskpet`` 包下 —— 旧代码 ``from deskpet.config import``
    永远 ImportError 被静默吞掉，CLI 退回 platformdirs、定位不到隔离环境
    的 state.db。② 改用 ``paths.user_data_dir()`` —— 它认
    ``DESKPET_USER_DATA_DIR`` env，eval CLI 因此能对准 worktree dev 库。
    """
    try:
        from config import load_config  # type: ignore
        cfg = load_config()
        if cfg.memory.db_path:
            return Path(cfg.memory.db_path).resolve().parent / "state.db"
    except Exception:
        pass
    try:
        import paths  # type: ignore
        return paths.user_data_dir() / "data" / "state.db"
    except Exception:
        pass
    try:
        import platformdirs
        return Path(
            platformdirs.user_data_dir("deskpet", appauthor=False, roaming=False)
        ) / "data" / "state.db"
    except ImportError:
        return Path("./state.db")


async def _make_llm_call():
    """Return an async ``(prompt: str) -> str`` bound to the live provider."""
    try:
        from providers.openai_compatible import (  # type: ignore
            OpenAICompatibleProvider,
        )
        from config import load_config  # type: ignore
    except ImportError:
        return _stub_llm

    cfg = load_config()
    # Pick the first provider in the chain — eval doesn't need fallback.
    providers = getattr(cfg.llm, "providers", None) or []
    if not providers:
        return _stub_llm
    p = providers[0]
    provider = OpenAICompatibleProvider(
        base_url=p.base_url,
        api_key=getattr(p, "api_key", "") or "",
        model=p.model,
    )

    async def _call(prompt: str) -> str:
        result = await provider.chat_with_tools(
            messages=[{"role": "user", "content": prompt}],
            tools=[],
            max_tokens=256,
        )
        return (result or {}).get("content") or ""

    return _call


async def _stub_llm(prompt: str) -> str:
    """Last-resort LLM that returns deterministic but useless output.

    The CLI prints a warning so the user knows their build will produce
    placeholder QAs, not real ones.
    """
    return '["What was discussed?", "Tell me more about that."]'


async def _cmd_build(args: argparse.Namespace) -> int:
    from deskpet.memory.eval.qaset import QASetBuilder
    db = await _resolve_state_db()
    if not db.exists():
        print(f"state.db not found at {db}", file=sys.stderr)
        return 2
    llm = await _make_llm_call()
    builder = QASetBuilder(db, llm)
    items = await builder.build(
        max_items=int(args.n),
        include_archive=not args.no_archive,
    )
    print(json.dumps({
        "inserted": len(items),
        "db": str(db),
        "first_5": [
            {"query": it.query, "expected_msg_id": it.expected_msg_id}
            for it in items[:5]
        ],
    }, ensure_ascii=False, indent=2))
    return 0


async def _cmd_run(args: argparse.Namespace) -> int:
    from deskpet.memory.eval.metrics import MetricsRunner
    db = await _resolve_state_db()
    if not db.exists():
        print(f"state.db not found at {db}", file=sys.stderr)
        return 2
    # Build a Retriever instance from live config.
    try:
        from deskpet.memory.session_db import SessionDB
        from deskpet.memory.embedder import Embedder
        from deskpet.memory.retriever import Retriever
        sdb = SessionDB(db_path=db)
        await sdb.initialize()
        embedder = Embedder(model_path=None, use_mock_when_missing=True)
        retriever = Retriever(session_db=sdb, embedder=embedder)
    except Exception as exc:
        print(f"failed to construct Retriever: {exc}", file=sys.stderr)
        return 3

    runner = MetricsRunner(
        db,
        retriever,
        config_snapshot={"mode": "cli", "top_k": int(args.top_k)},
    )
    report = await runner.run(top_k=int(args.top_k))
    print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
    return 0


async def _cmd_feedback(args: argparse.Namespace) -> int:
    """记忆系统升级 WI-M1.1：dump 用户反馈（memory_user_feedback）汇总。

    验证 DoD「eval CLI 能读到反馈数据」—— 前端点 👍/👎 写进表后，本命令
    能把 up/down/net 汇总 + 召回 trouble spot 读出来。
    """
    from deskpet.memory.eval.feedback import FeedbackStore
    db = await _resolve_state_db()
    if not db.exists():
        print(f"state.db not found at {db}", file=sys.stderr)
        return 2
    store = FeedbackStore(db)
    summary = await store.summary()
    negatives = await store.top_negative_messages(limit=10)
    print(json.dumps({
        "db": str(db),
        "summary": summary,
        "top_negative": negatives,
    }, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    _setup_logging()
    parser = argparse.ArgumentParser(
        prog="python -m deskpet.memory.eval",
        description="DeskPet memory recall evaluation harness",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_build = sub.add_parser("build", help="generate QA set")
    p_build.add_argument("--n", default="50", help="max items to insert")
    p_build.add_argument(
        "--no-archive", action="store_true",
        help="exclude messages_archive sources",
    )
    p_build.set_defaults(func=_cmd_build)
    p_run = sub.add_parser("run", help="replay QA against retriever")
    p_run.add_argument("--top-k", default="20")
    p_run.set_defaults(func=_cmd_run)
    p_fb = sub.add_parser("feedback", help="dump user thumbs feedback summary")
    p_fb.set_defaults(func=_cmd_feedback)

    args = parser.parse_args()
    return asyncio.run(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
