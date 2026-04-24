"""P4-S12 §17.1 — Phase-4 component bench.

Benchmarks the P4 memory/skill/assembler layer in isolation so we can
validate SLO compliance (§17.3) without standing up the full session
pipeline. Prints human-readable summary + writes ``bench_phase4.json``.

SLO targets measured here:
- Memory recall (L1 + L2, L3 when embedder available) p95 < 30ms
- SkillLoader.list_skills() p95 < 5ms
- FileMemory read_snapshot p95 < 10ms

Not measured here (requires full stack — verified separately in S13):
- Assembler p95 < 370ms (needs classifier + components + policies wired)
- First-byte p50 < 1100ms (needs LLM)
- Prompt cache hit rate (needs anthropic/claude)

Run: ``python -m scripts.bench_phase4`` from ``backend/``.
"""
from __future__ import annotations

import asyncio
import json
import statistics
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Awaitable

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deskpet.memory.file_memory import FileMemory
from deskpet.memory.manager import MemoryManager
from deskpet.memory.session_db import SessionDB
from deskpet.skills.loader import SkillLoader


async def timeit(label: str, fn: Callable[[], Awaitable[Any]], n: int = 200) -> dict:
    """Run ``fn`` n times, return {p50, p95, p99, mean, n} in ms."""
    samples: list[float] = []
    # warm-up — 5 throwaway runs so we don't count first-call overhead
    for _ in range(5):
        await fn()
    for _ in range(n):
        t0 = time.perf_counter()
        await fn()
        samples.append((time.perf_counter() - t0) * 1000.0)
    samples.sort()
    return {
        "label": label,
        "n": n,
        "p50_ms": round(statistics.median(samples), 3),
        "p95_ms": round(samples[int(0.95 * n)], 3),
        "p99_ms": round(samples[int(0.99 * n)], 3),
        "mean_ms": round(statistics.mean(samples), 3),
        "max_ms": round(max(samples), 3),
    }


async def bench_file_memory(base_dir: Path) -> list[dict]:
    """L1 — FileMemory read/append."""
    fm = FileMemory(base_dir=base_dir)
    fm.ensure_base_dir()
    # Pre-seed with 30 entries so read isn't trivial.
    for i in range(30):
        await fm.append("memory", f"entry {i} about deskpet ops", salience=0.5)
    return [
        await timeit(
            "FileMemory.read_snapshot",
            lambda: fm.read_snapshot(),
            n=500,
        ),
        await timeit(
            "FileMemory.list_entries(memory)",
            lambda: fm.list_entries("memory"),
            n=500,
        ),
    ]


async def bench_memory_recall(base_dir: Path) -> list[dict]:
    """L1+L2 MemoryManager.recall — SLO: p95 < 30ms."""
    fm = FileMemory(base_dir=base_dir)
    fm.ensure_base_dir()
    for i in range(20):
        await fm.append("memory", f"recall-bench note #{i}", salience=0.5)

    db_path = base_dir / "bench.sqlite"
    sdb = SessionDB(str(db_path))
    await sdb.initialize()
    # seed L2 with 100 messages
    for i in range(100):
        await sdb.append_message(
            session_id="bench-sess",
            role="user" if i % 2 == 0 else "assistant",
            content=f"message {i}",
        )

    mm = MemoryManager(file_memory=fm, session_db=sdb, retriever=None)
    await mm.initialize()

    return [
        await timeit(
            "MemoryManager.recall(l1=snapshot, l2_top_k=10, l3=skip)",
            lambda: mm.recall(
                "deskpet",
                policy={
                    "l1": "snapshot",
                    "l2_top_k": 10,
                    "l3_top_k": 0,
                    "session_id": "bench-sess",
                },
            ),
            n=300,
        ),
        await timeit(
            "MemoryManager.recall(l1=skip, l2_top_k=10)",
            lambda: mm.recall(
                "deskpet",
                policy={"l2_top_k": 10, "l3_top_k": 0, "session_id": "bench-sess"},
            ),
            n=300,
        ),
    ]


async def bench_skill_loader(base_dir: Path) -> list[dict]:
    """SkillLoader.list_skills — SLO: p95 < 5ms."""
    user_dir = base_dir / "user-skills"
    user_dir.mkdir(parents=True, exist_ok=True)
    # SkillLoader takes an ordered list of skill dirs (index 0 = builtin-like,
    # 1+ = user). We use the default lookup + user_dir override.
    loader = SkillLoader(skill_dirs=[user_dir], enable_watch=False)
    await loader.start()
    # list_skills is sync; wrap so timeit's async contract still fits.
    async def call() -> Any:
        return loader.list_skills()

    return [await timeit("SkillLoader.list_skills()", call, n=500)]


def evaluate_slo(results: list[dict]) -> list[dict]:
    slo_map = {
        "MemoryManager.recall(l1=snapshot, l2_top_k=10, l3=skip)": 30.0,
        "MemoryManager.recall(l1=skip, l2_top_k=10)": 30.0,
        "FileMemory.read_snapshot": 10.0,
        "FileMemory.list_entries(memory)": 10.0,
        "SkillLoader.list_skills()": 5.0,
    }
    out = []
    for r in results:
        slo = slo_map.get(r["label"])
        r2 = dict(r)
        if slo is not None:
            r2["slo_p95_ms"] = slo
            r2["slo_pass"] = r["p95_ms"] <= slo
        out.append(r2)
    return out


def print_human_summary(results: list[dict]) -> None:
    print()
    print("=== P4-S12 Phase-4 Bench ===")
    for r in results:
        slo = f" / SLO {r.get('slo_p95_ms', '-')}ms" if "slo_p95_ms" in r else ""
        status = ""
        if "slo_pass" in r:
            status = " [PASS]" if r["slo_pass"] else " [FAIL]"
        print(
            f"  {r['label']:<52}  "
            f"p50 {r['p50_ms']:>6.2f}  p95 {r['p95_ms']:>6.2f}"
            f"{slo}{status}"
        )
    print("============================")
    fails = [r["label"] for r in results if r.get("slo_pass") is False]
    if fails:
        print(f"FAIL: {len(fails)} labels out of SLO -> {fails}")
    else:
        print("All benched components within SLO.")


async def main() -> int:
    print("P4-S12 bench — warming up…")
    with tempfile.TemporaryDirectory(prefix="deskpet-bench-") as tmp:
        base = Path(tmp)
        fm_results = await bench_file_memory(base / "fm")
        recall_results = await bench_memory_recall(base / "mm")
        skill_results = await bench_skill_loader(base / "skills")
        all_results = fm_results + recall_results + skill_results
        annotated = evaluate_slo(all_results)
        print_human_summary(annotated)

        out_path = Path(__file__).resolve().parent.parent / "bench_phase4.json"
        out_path.write_text(
            json.dumps(
                {
                    "generated_at": time.time(),
                    "note": "P4-S12 §17.1/§17.3 Phase-4 component bench",
                    "results": annotated,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"Wrote {out_path}")

        any_fail = any(r.get("slo_pass") is False for r in annotated)
        return 1 if any_fail else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
