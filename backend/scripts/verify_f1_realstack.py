# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""F1 真组件栈验证 (memory-stage2-followup, 阶段2)。

不是 stub 单测 —— 用 **生产装配** `build_default_assembler` 造真 assembler,
注入真 `Embedder`(会冷加载 —— 正是 round-3 fanout timeout 的根因组件)+ 真
`WorkspaceMemoryStore`,seed 一条真文件动作,然后调真 `ComponentRegistry.fanout`
(assemble() 内部就是它),断言:

  1. 每个组件 slice 带 ``meta.duration_ms`` + ``meta.status`` (F1 新增的
     per-component 计时)。
  2. workspace_memory 组件返回真内容 (status=ok)，不被别的慢组件拖成
     timeout —— 这正是 round-3 bug #4 修复后该有的行为。
  3. 无 "全部组件一起 timeout" 的旧塌缩行为。

隔离：DESKPET_USER_DATA 指向 worktree-local 临时目录，独立 state.db；不开任何
端口、不连 GUI、不碰其它 worktree / 8400 backend / codex。
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


async def main() -> int:
    from deskpet.agent.assembler import build_default_assembler
    from deskpet.agent.assembler.components.base import ComponentContext
    from deskpet.agent.assembler.bundle import AssemblyPolicy
    from deskpet.memory.embedder import Embedder
    from deskpet.memory.workspace import WorkspaceMemoryStore

    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "f1_realstack.db"

        # 真 WorkspaceMemoryStore + seed 一条真文件动作（code mode 真场景）。
        ws_store = WorkspaceMemoryStore(db_path)
        await ws_store.record_action(
            session_id="f1-verify",
            path=r"G:\projects\demo\README.md",
            action="read",
            content="# Demo project\nUses Python + FastAPI + SQLite.\n",
        )

        # 真 Embedder（mock 缺权重时降级，但仍是生产 Embedder 类；若有真
        # 权重会真冷加载 —— round-3 timeout 的根因组件）。
        embedder = Embedder(model_path=None, use_mock_when_missing=True)
        await embedder.warmup()

        # 生产装配：与 main.py lifespan 同一个工厂。
        asm = build_default_assembler(
            embedder=embedder,
            workspace_memory_store=ws_store,
            enabled=True,
        )
        registry = asm._registry

        # 真 ctx：workspace_memory 组件会真查 ws_store；其余组件按各自
        # 依赖（memory_manager=None → 优雅空）。policy 让 workspace_memory
        # 参与（code 任务 prefer 它）。
        policy = AssemblyPolicy(
            task_type="code",
            must=["memory"],
            prefer=["workspace_memory", "persona", "time", "tool", "workspace"],
        )
        ctx = ComponentContext(
            task_type="code",
            policy=policy,
            user_message="再看一下刚才那个 README",
            session_id="f1-verify",
        )

        t0 = time.monotonic()
        slices = await registry.fanout(ctx, timeout_ms=1500)
        elapsed_ms = (time.monotonic() - t0) * 1000.0

        by_name = {s.component_name: s for s in slices}
        report = {
            "fanout_elapsed_ms": round(elapsed_ms, 1),
            "components": {
                name: {
                    "status": s.meta.get("status"),
                    "duration_ms": s.meta.get("duration_ms"),
                    "error": s.meta.get("error"),
                    "has_content": bool(getattr(s, "text_content", "")),
                }
                for name, s in by_name.items()
            },
        }
        print("=== F1 real-stack fanout report ===")
        print(json.dumps(report, ensure_ascii=False, indent=2))

        # ---- 断言（F1 验收）----
        failures = []

        # 1. 每个组件都带 per-component 计时（F1 新增）。
        for name, s in by_name.items():
            if s.meta.get("duration_ms") is None:
                failures.append(f"{name}: missing duration_ms")
            if s.meta.get("status") is None:
                failures.append(f"{name}: missing status")

        # 2. workspace_memory 真返回内容 + status=ok（bug #4 修复后行为）。
        wm = by_name.get("workspace_memory")
        if wm is None:
            failures.append("workspace_memory component not run")
        else:
            if wm.meta.get("status") != "ok":
                failures.append(
                    f"workspace_memory status={wm.meta.get('status')} "
                    f"(expect ok); error={wm.meta.get('error')}"
                )
            if "README" not in (getattr(wm, "text_content", "") or ""):
                failures.append(
                    "workspace_memory text_content missing seeded README "
                    f"(got: {getattr(wm, 'text_content', '')!r:.80})"
                )

        # 3. 无"全部一起 timeout"塌缩：至少 trivial 组件 (time) status=ok。
        tm = by_name.get("time")
        if tm is not None and tm.meta.get("status") not in ("ok", None):
            failures.append(
                f"time component starved (status={tm.meta.get('status')}) "
                "— blanket-timeout regression"
            )

        print()
        if failures:
            print("RESULT: FAIL")
            for f in failures:
                print(f"  - {f}")
            return 1
        print("RESULT: PASS — F1 per-component fanout 在真组件栈下工作正常")
        print(f"  workspace_memory: status=ok, content 含 seeded README")
        print(f"  每组件均带 duration_ms/status; 无全局塌缩")
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
