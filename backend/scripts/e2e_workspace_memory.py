# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Stage 2 / WI-V.1 (MR-S2-6) — workspace memory end-to-end GUI smoke.

针对 PRD §4.6 DoD：
  * 第二轮 agent 至少有一次 prompt 中含工作记忆段
  * flag on 时 file_read 调用数 ≤ flag off
  * 证据完整归档到 evidence/2026-05-23-mr4-e2e/

设计：
  * 不引入 Playwright / Tauri E2E 框架（PRD D15）
  * 直接驱动 backend WebSocket（agent_engine endpoint），录 file_*  tool
    invocation 计数 + workspace_state 表 dump
  * 主 checkout 跑（backend/.venv 真 BGE-M3 + 真 LLM）

用法::

    cd backend
    python -m scripts.e2e_workspace_memory --flag on
    python -m scripts.e2e_workspace_memory --flag off
    python -m scripts.e2e_workspace_memory --compare    # 跑两次并对比
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except AttributeError:
        pass


_TASK_DIR = Path(r"D:\tmp\stage2-mr4")
_EVIDENCE_DIR = Path(__file__).resolve().parent.parent.parent / "evidence" / "2026-05-23-mr4-e2e"


def _ensure_task_dir() -> None:
    _TASK_DIR.mkdir(parents=True, exist_ok=True)
    readme = _TASK_DIR / "README.md"
    if not readme.exists():
        readme.write_text(
            "# Test workspace for MR-S2-6\n\n"
            "This README is read by agent and summarized.\n"
            "Stage 2 MR-4 GUI end-to-end smoke fixture.\n"
            "Some content: pets like cats and dogs.\n",
            encoding="utf-8",
        )


def _dump_workspace_state(db_path: Path, session_id: str) -> list[dict]:
    if not db_path.is_file():
        return []
    out: list[dict] = []
    conn = sqlite3.connect(str(db_path))
    try:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            "SELECT * FROM workspace_state WHERE session_id = ? "
            "ORDER BY last_action_ts ASC",
            (session_id,),
        )
        for r in cur.fetchall():
            out.append(dict(r))
    except sqlite3.OperationalError as exc:
        print(f"[e2e] workspace_state read error: {exc}", file=sys.stderr)
    finally:
        conn.close()
    return out


async def _run_session(*, flag_on: bool, session_id: str) -> dict:
    """Run one agent session via WebSocket. Returns metric dict."""
    import websockets  # type: ignore[import-not-found]

    # 端口默认 8100；可被 env override
    port = int(os.environ.get("DESKPET_PORT", "8100"))
    secret = os.environ.get("DESKPET_SHARED_SECRET", "")
    ws_url = f"ws://127.0.0.1:{port}/ws/chat?session_id={session_id}"
    if secret:
        ws_url += f"&secret={secret}"

    file_read_count = 0
    file_write_count = 0
    workspace_recall_count = 0
    last_assistant: list[str] = []

    prompt_round1 = (
        "在 D:\\tmp\\stage2-mr4 下读 README.md，"
        "基于内容生成 summary.md（写到同目录）"
    )
    prompt_round2 = "再看一下刚才那个 summary.md，告诉我它说了什么"

    try:
        async with websockets.connect(ws_url, max_size=10**7) as ws:
            for prompt in (prompt_round1, prompt_round2):
                await ws.send(json.dumps({
                    "type": "chat",
                    "payload": {"content": prompt, "use_code_mode": True,
                                "project_root": str(_TASK_DIR)},
                }))
                deadline = time.time() + 120
                while time.time() < deadline:
                    msg_raw = await asyncio.wait_for(ws.recv(), timeout=120)
                    try:
                        msg = json.loads(msg_raw)
                    except json.JSONDecodeError:
                        continue
                    mt = msg.get("type", "")
                    if mt == "tool_invoke":
                        tn = msg.get("payload", {}).get("name", "")
                        if tn == "file_read":
                            file_read_count += 1
                        elif tn == "file_write":
                            file_write_count += 1
                        elif tn == "workspace_recall":
                            workspace_recall_count += 1
                    elif mt == "assistant_message":
                        last_assistant.append(
                            msg.get("payload", {}).get("content", "")
                        )
                    elif mt in ("assistant_done", "chat_done"):
                        break
    except Exception as exc:  # noqa: BLE001
        print(f"[e2e] session error: {exc}", file=sys.stderr)
        return {
            "flag_on": flag_on, "session_id": session_id,
            "file_read_count": -1, "error": str(exc),
        }

    return {
        "flag_on": flag_on,
        "session_id": session_id,
        "file_read_count": file_read_count,
        "file_write_count": file_write_count,
        "workspace_recall_count": workspace_recall_count,
        "last_assistant_excerpt": [a[:200] for a in last_assistant[-2:]],
    }


async def _amain() -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.e2e_workspace_memory")
    parser.add_argument(
        "--flag", choices=["on", "off"], default="on",
        help="workspace_memory flag state (informational only — "
             "you must also set [memory.v2] workspace_memory in config.toml)",
    )
    parser.add_argument(
        "--db-path", default="",
        help="state.db path (留空 = paths.user_data_dir()/data/state.db)",
    )
    parser.add_argument(
        "--compare", action="store_true",
        help="跑两次（先 off 后 on）并打印对比表",
    )
    args = parser.parse_args()

    _ensure_task_dir()
    _EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)

    if args.compare:
        print("=" * 60)
        print("[e2e] Running compare mode: flag=off then flag=on")
        print("[e2e] NOTE: user must restart backend with the right flag between runs")
        print("=" * 60)
        sid_off = f"mr4-off-{uuid.uuid4().hex[:8]}"
        sid_on = f"mr4-on-{uuid.uuid4().hex[:8]}"
        print("\n[e2e] >>> Now START backend with [memory.v2] workspace_memory=false")
        input("[e2e] Press Enter when backend is ready ...")
        off_metric = await _run_session(flag_on=False, session_id=sid_off)
        print("\n[e2e] >>> Now RESTART backend with [memory.v2] workspace_memory=true")
        input("[e2e] Press Enter when backend is ready ...")
        on_metric = await _run_session(flag_on=True, session_id=sid_on)

        # workspace_state dump
        db_path = Path(args.db_path) if args.db_path else _default_db_path()
        on_state = _dump_workspace_state(db_path, sid_on)
        off_state = _dump_workspace_state(db_path, sid_off)

        report = {
            "off": off_metric,
            "on": on_metric,
            "workspace_state_off": off_state,
            "workspace_state_on": on_state,
            "delta": {
                "file_read_count":
                    off_metric.get("file_read_count", 0)
                    - on_metric.get("file_read_count", 0),
                "workspace_recall_count":
                    on_metric.get("workspace_recall_count", 0)
                    - off_metric.get("workspace_recall_count", 0),
            },
        }
        report_path = _EVIDENCE_DIR / "compare-report.json"
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\n[e2e] report → {report_path}")
        print(json.dumps(report["delta"], ensure_ascii=False, indent=2))
        return 0

    sid = f"mr4-{args.flag}-{uuid.uuid4().hex[:8]}"
    metric = await _run_session(flag_on=(args.flag == "on"), session_id=sid)
    db_path = Path(args.db_path) if args.db_path else _default_db_path()
    state_rows = _dump_workspace_state(db_path, sid)

    out = {"metric": metric, "workspace_state": state_rows}
    report_path = _EVIDENCE_DIR / f"single-{args.flag}-{sid}.json"
    report_path.write_text(
        json.dumps(out, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n[e2e] report → {report_path}")
    print(json.dumps(metric, ensure_ascii=False, indent=2))
    return 0


def _default_db_path() -> Path:
    try:
        import paths as _paths
        return _paths.user_data_dir() / "data" / "state.db"
    except Exception:  # noqa: BLE001
        return Path("./data/state.db")


def main() -> int:
    return asyncio.run(_amain())


if __name__ == "__main__":
    sys.exit(main())
