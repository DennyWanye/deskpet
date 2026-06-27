#!/usr/bin/env python
"""PPT Pro 接线冒烟（WI-9）。

全 mock、零网络、零真出图：验证 ppt_pro 的「注册 + async 秒回 handler + 独立编排
task 控制流（调研→拟纲→大纲卡确认→渲染→上报）」整条 wiring 不死。

跑法（从 backend 目录）：
    .venv/Scripts/python.exe ../scripts/acceptance/ppt_pro_smoke.py
或从 repo 根：
    backend/.venv/Scripts/python.exe scripts/acceptance/ppt_pro_smoke.py

期望末行：DECISION: SHIP
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# 让 backend 包可导入
_HERE = Path(__file__).resolve()
_BACKEND = _HERE.parents[2] / "backend"
sys.path.insert(0, str(_BACKEND))

import deskpet.tools.ppt_tools as ppt  # noqa: E402
from deskpet.tools import research_tools as rt  # noqa: E402
from deskpet.tools.registry import registry  # noqa: E402

_FAILS: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        _FAILS.append(name)


async def _amain() -> None:
    # 1) 注册检查
    specs = getattr(registry, "_specs", None) or getattr(registry, "_tools", {}) or {}
    keys = list(specs.keys()) if hasattr(specs, "keys") else []
    check("ppt_pro 已注册进 registry", "ppt_pro" in keys, f"ppt tools={[k for k in keys if 'ppt' in k]}")

    cfg = ppt._ppt_pro_cfg()
    check("配置默认值（enabled/deep/confirm 1800）",
          bool(cfg.enabled) and cfg.default_depth == "deep" and cfg.confirm_timeout_s >= 300,
          f"enabled={cfg.enabled} depth={cfg.default_depth} confirm={cfg.confirm_timeout_s}")

    # 2) mock 掉网络/真出图/真 LLM
    async def _fake_research(topic, *, depth, timeout_s):  # noqa: ANN001
        return None  # 走「无来源」分支，避免网络

    async def _fake_draft(topic, report, *, pages, theme, image_mode, llm_call, feedback="", prev_slides=None):  # noqa: ANN001
        return [
            ppt.SlideOutline(layout="image_full", title="封面", bullets=["要点一", "要点二", "要点三"],
                             image_prompt="cinematic cover"),
        ]

    captured: dict = {"render_calls": 0, "skip_image_gen": None, "artifacts": None,
                      "receipt_outcome": None, "notifies": []}

    def _fake_render_pro(slides, *, theme, title, author, output_path, image_mode, probe_timeout_s, notify):  # noqa: ANN001
        captured["render_calls"] += 1
        try:
            notify("渲染中…")
        except Exception:
            pass
        return {"ok": True, "path": "/tmp/deskpet-ppt-smoke.pptx", "slide_count": 1,
                "theme": theme, "artifacts": [{"kind": "file", "path": "/tmp/deskpet-ppt-smoke.pptx"}]}

    async def _fake_llm():
        async def _call(prompt: str) -> str:
            return "[]"
        return _call

    ppt._research_topic_for_ppt = _fake_research          # type: ignore
    ppt._draft_outline_from_research = _fake_draft        # type: ignore
    ppt._render_pro = _fake_render_pro                     # type: ignore
    rt._resolve_default_llm_call = _fake_llm              # type: ignore

    # 3) 注入 mock services（大纲卡直接 accept）
    async def _outline_propose(sid, *, topic, slides, sources_count, outline_md, no_research):  # noqa: ANN001
        return {"action": "accept"}

    async def _notifier(sid, text):  # noqa: ANN001
        captured["notifies"].append(text)

    def _run_blocking(fn):
        return asyncio.get_running_loop().run_in_executor(None, fn)

    async def _artifact_push(sid, artifacts, text=""):  # noqa: ANN001
        captured["artifacts"] = artifacts

    def _receipt_report(sid, *, tool="ppt_pro", outcome, path=None, shas=None):  # noqa: ANN001
        captured["receipt_outcome"] = outcome

    ppt.set_ppt_pro_services(outline_propose=_outline_propose, notifier=_notifier,
                             run_blocking=_run_blocking, artifact_pusher=_artifact_push,
                             receipt_reporter=_receipt_report)

    # 4) 调 handler（秒回）+ 等后台编排 task 跑完
    sid = "smoke-default"
    res = await ppt._handle_ppt_pro({"topic": "钠离子电池2025", "pages": 4, "image_mode": True,
                                     "_session_id": sid})
    check("handler 秒回 status=researching", isinstance(res, dict) and res.get("status") == "researching", str(res))

    task = ppt._PPT_PRO_RUNNING.get(sid)
    check("独立编排 task 已起", task is not None)
    if task is not None:
        await asyncio.wait_for(task, timeout=30)

    check("编排走到渲染（_render_pro 被调）", captured["render_calls"] == 1)
    check("惊艳路径成功后推 artifact 卡", captured["artifacts"] is not None
          and any(a.get("kind") == "file" for a in (captured["artifacts"] or [])))
    check("receipt outcome=ok", captured["receipt_outcome"] == "ok")
    check("有进度/成品通知", len(captured["notifies"]) >= 1)
    check("task 跑完已清 _PPT_PRO_RUNNING（identity-guard）", sid not in ppt._PPT_PRO_RUNNING)

    # 5) 同主题重复调 → already_running（需先制造一个在跑的 task）
    async def _slow_propose(sid, **kw):  # noqa: ANN001
        await asyncio.sleep(5)
        return {"action": "cancel"}
    ppt.set_ppt_pro_services(outline_propose=_slow_propose, notifier=_notifier,
                             run_blocking=_run_blocking, artifact_pusher=_artifact_push,
                             receipt_reporter=_receipt_report)
    sid2 = "smoke-dedup"
    await ppt._handle_ppt_pro({"topic": "主题X", "_session_id": sid2})
    await asyncio.sleep(0.2)
    res_dup = await ppt._handle_ppt_pro({"topic": "主题X", "_session_id": sid2})
    check("同主题重复调 → already_running", res_dup.get("status") == "already_running", str(res_dup))
    # 清理：取消
    ppt._ppt_pro_cancel(sid2)
    t2 = ppt._PPT_PRO_RUNNING.get(sid2)
    if t2 is not None:
        try:
            await asyncio.wait_for(t2, timeout=5)
        except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
            pass


def main() -> int:
    try:
        asyncio.run(_amain())
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] 冒烟异常: {type(exc).__name__}: {exc}")
        _FAILS.append("uncaught")
    print("-" * 50)
    if _FAILS:
        print(f"DECISION: NO-SHIP（{len(_FAILS)} 项失败: {', '.join(_FAILS)}）")
        return 1
    print("DECISION: SHIP")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
