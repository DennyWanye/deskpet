"""P4-S15 — full-stack cold-start + per-turn assembly bench.

Measures:
- Cold-start: time from "construct stack" → "ready to accept first chat".
  Excludes BGE-M3 weights (mock fallback) — that path is independently
  benchmarked when the user actually downloads them.
- Per-turn assemble: 50 calls to ContextAssembler.assemble() with the
  same registry/policies/embedder a live backend uses.

Pass criteria (rc1):
- Cold-start < 5s (mock embedder), < 90s (real BGE-M3 expected once shipped).
- Assemble p95 < 370ms (component fan-out timeout cap).

Run: ``python -m scripts.bench_phase4_full_stack`` from ``backend/``.
"""
from __future__ import annotations

import asyncio
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deskpet.agent.assembler import build_default_assembler
from deskpet.memory.embedder import Embedder
from deskpet.memory.file_memory import FileMemory
from deskpet.memory.manager import MemoryManager
from deskpet.memory.retriever import Retriever
from deskpet.memory.session_db import SessionDB
from deskpet.memory.vector_worker import VectorWorker
from deskpet.mcp.bootstrap import create_and_start_from_config
from deskpet.skills.loader import SkillLoader


async def cold_start_bench(tmp: Path) -> dict:
    """Construct + initialise the full P4 stack. Returns timing breakdown."""
    breakdown: dict[str, float] = {}

    t0 = time.perf_counter()

    # SessionDB + initialise
    t = time.perf_counter()
    session_db = SessionDB(db_path=tmp / "state.db")
    await session_db.initialize()
    breakdown["session_db_init_ms"] = (time.perf_counter() - t) * 1000.0

    # Embedder warmup (mock path — model dir absent)
    t = time.perf_counter()
    embedder = Embedder(model_path=tmp / "no-bge", use_mock_when_missing=True)
    await embedder.warmup()
    breakdown["embedder_warmup_ms"] = (time.perf_counter() - t) * 1000.0

    # VectorWorker
    t = time.perf_counter()
    worker = VectorWorker(session_db=session_db, embedder=embedder)
    await worker.start()
    session_db._on_message_written = worker.enqueue
    breakdown["vector_worker_start_ms"] = (time.perf_counter() - t) * 1000.0

    # FileMemory + MemoryManager
    t = time.perf_counter()
    fm = FileMemory(base_dir=tmp)
    fm.ensure_base_dir()
    retriever = Retriever(session_db=session_db, embedder=embedder)
    mm = MemoryManager(file_memory=fm, session_db=session_db, retriever=retriever)
    await mm.initialize()
    breakdown["memory_manager_init_ms"] = (time.perf_counter() - t) * 1000.0

    # SkillLoader
    t = time.perf_counter()
    import deskpet.skills.builtin as _builtin_pkg
    builtin_dir = Path(_builtin_pkg.__file__).parent
    user_dir = tmp / "skills-user"
    user_dir.mkdir(parents=True, exist_ok=True)
    loader = SkillLoader(skill_dirs=[builtin_dir, user_dir], enable_watch=False)
    await loader.start()
    breakdown["skill_loader_start_ms"] = (time.perf_counter() - t) * 1000.0

    # Assembler
    t = time.perf_counter()
    assembler = build_default_assembler(
        embedder=embedder,
        llm_registry=None,
        enabled=True,
        context_window=32_000,
        budget_ratio=0.6,
    )
    breakdown["assembler_construct_ms"] = (time.perf_counter() - t) * 1000.0

    # MCP bootstrap (empty config)
    t = time.perf_counter()
    mcp = await create_and_start_from_config({}, tool_registry=None)
    breakdown["mcp_bootstrap_ms"] = (time.perf_counter() - t) * 1000.0

    breakdown["total_cold_start_ms"] = (time.perf_counter() - t0) * 1000.0

    # Return components for the per-turn bench.
    return {
        "breakdown": breakdown,
        "stack": {
            "session_db": session_db,
            "embedder": embedder,
            "worker": worker,
            "memory_manager": mm,
            "skill_loader": loader,
            "assembler": assembler,
            "mcp": mcp,
        },
    }


async def per_turn_bench(stack: dict, n: int = 50) -> dict:
    a = stack["assembler"]
    mm = stack["memory_manager"]
    sl = stack["skill_loader"]

    samples: list[float] = []
    # warmup
    for _ in range(3):
        await a.assemble(
            user_message="warmup",
            memory_manager=mm,
            skill_registry=sl,
            session_id="bench",
        )
    for _ in range(n):
        t = time.perf_counter()
        await a.assemble(
            user_message="what do you remember about cats?",
            memory_manager=mm,
            skill_registry=sl,
            session_id="bench",
        )
        samples.append((time.perf_counter() - t) * 1000.0)
    samples.sort()
    return {
        "n": n,
        "p50_ms": round(statistics.median(samples), 2),
        "p95_ms": round(samples[int(0.95 * n)], 2),
        "p99_ms": round(samples[int(0.99 * n)], 2),
        "max_ms": round(max(samples), 2),
        "mean_ms": round(statistics.mean(samples), 2),
    }


async def main() -> int:
    print("P4-S15 full-stack bench — bringing up the stack...")
    with tempfile.TemporaryDirectory(prefix="deskpet-s15-bench-") as tmp_str:
        tmp = Path(tmp_str)
        cs = await cold_start_bench(tmp)
        breakdown = cs["breakdown"]
        stack = cs["stack"]

        print()
        print("=== Cold-start breakdown (mock embedder) ===")
        for key, ms in breakdown.items():
            print(f"  {key:<30s} {ms:>8.2f} ms")
        print("============================================")

        print()
        print("Running per-turn assemble bench (n=50)...")
        turn = await per_turn_bench(stack, n=50)
        print()
        print("=== Per-turn assemble ===")
        print(f"  n        {turn['n']}")
        print(f"  p50      {turn['p50_ms']:.2f} ms")
        print(f"  p95      {turn['p95_ms']:.2f} ms (SLO 370ms)")
        print(f"  p99      {turn['p99_ms']:.2f} ms")
        print(f"  max      {turn['max_ms']:.2f} ms")
        print(f"  mean     {turn['mean_ms']:.2f} ms")
        print("=========================")

        # Cleanup
        await stack["mcp"].stop()
        await stack["worker"].stop()
        await stack["skill_loader"].stop()

        cold_start_pass = breakdown["total_cold_start_ms"] < 5_000.0  # 5s mock budget
        per_turn_pass = turn["p95_ms"] < 370.0
        print()
        print(f"Cold-start <5s (mock):   {'PASS' if cold_start_pass else 'FAIL'} "
              f"({breakdown['total_cold_start_ms']:.2f}ms)")
        print(f"Assemble p95 <370ms:     {'PASS' if per_turn_pass else 'FAIL'} "
              f"({turn['p95_ms']:.2f}ms)")

        out = Path(__file__).resolve().parent.parent / "bench_phase4_full_stack.json"
        out.write_text(
            json.dumps(
                {
                    "generated_at": time.time(),
                    "cold_start_breakdown_ms": breakdown,
                    "per_turn_assemble": turn,
                    "slo": {
                        "cold_start_mock_ms": 5_000.0,
                        "assemble_p95_ms": 370.0,
                    },
                    "passing": cold_start_pass and per_turn_pass,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"Wrote {out}")
        return 0 if (cold_start_pass and per_turn_pass) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
