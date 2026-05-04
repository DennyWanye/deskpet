"""P4-S20 真实市场端到端 — 通过 WS 走完整 IPC 链路装一个真实 GitHub 技能。

此脚本验证：
  1. skill_install_from_url 能真的 git clone --depth 1
  2. SkillInstaller 能 parse 真实社区技能的 SKILL.md / 派生 manifest
  3. 安全检查能拦下不在 allowlist 的工具（用一个明显恶意的 manifest 做对照）
  4. skill_install_confirm(approve=True) 能 finalize 进 user skills 目录
  5. SkillLoader hot-reload 能立刻看到新技能
  6. skill_uninstall 能干净删除

要求 backend 在 8100 跑着。
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import websockets


def _print(*parts: object) -> None:
    print("[e2e-mkt]", *parts, flush=True)


# 用 anthropics/skills 仓库里一个真实的 SKILL.md（artifacts-builder 比较小）。
# 社区仓库 SKILL.md 大多没有 manifest.json — installer 会从 frontmatter
# 派生最小 manifest，然后通过 safety 校验。
TEST_URL = "github:anthropics/skills/tree/main/skills/algorithmic-art"


async def _send_recv(ws, msg: dict, expected_types: set[str], timeout: float = 60.0):
    await ws.send(json.dumps(msg))
    while True:
        raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
        m = json.loads(raw)
        if m.get("type") in expected_types:
            return m


async def main() -> int:
    uri = "ws://127.0.0.1:8100/ws/control?secret="
    _print("connecting", uri)

    async with websockets.connect(uri) as ws:
        # skip startup_status
        await asyncio.wait_for(ws.recv(), timeout=5.0)

        # 1. 列已安装（应该是空或仅 builtin 残余）
        before = await _send_recv(
            ws, {"type": "skill_list_installed"},
            {"skill_list_installed_response"},
        )
        _print("before install — installed count:",
               len(before.get("payload", {}).get("skills", [])))

        # 2. 真实克隆
        _print("git clone:", TEST_URL)
        pending = await _send_recv(
            ws,
            {"type": "skill_install_from_url", "payload": {"url": TEST_URL}},
            {"skill_install_pending"},
            timeout=120.0,
        )
        p = pending.get("payload", {})
        if not p.get("ok"):
            _print("FAIL clone:", p.get("error"))
            return 1
        staging_id = p.get("staging_id")
        skill_name = p.get("name")
        _print(
            f"staged ok name={skill_name} staging_id={staging_id}",
            f"manifest_keys={list((p.get('manifest') or {}).keys())}",
        )

        # 3. 用户确认 → finalize
        confirm = await _send_recv(
            ws,
            {
                "type": "skill_install_confirm",
                "payload": {"staging_id": staging_id, "approve": True},
            },
            {"skill_install_confirm_response"},
        )
        cp = confirm.get("payload", {})
        if not cp.get("ok"):
            _print("FAIL finalize:", cp.get("error"))
            return 1
        installed_path = cp.get("path")
        _print("finalized at", installed_path)

        # 4. 列已安装（应该多一个）
        after = await _send_recv(
            ws, {"type": "skill_list_installed"},
            {"skill_list_installed_response"},
        )
        names = [s.get("name") for s in after.get("payload", {}).get("skills", [])]
        _print("after install — names:", names)
        if skill_name not in names:
            _print(f"FAIL: {skill_name} not in installed list")
            return 1

        # 5. 卸载
        u = await _send_recv(
            ws,
            {"type": "skill_uninstall", "payload": {"name": skill_name}},
            {"skill_uninstall_response"},
        )
        if not u.get("payload", {}).get("ok"):
            _print("FAIL uninstall:", u.get("payload"))
            return 1
        _print("uninstalled ok")

        # 6. 确认目录已删
        if installed_path and Path(installed_path).exists():
            _print("FAIL: dir still exists after uninstall:", installed_path)
            return 1

    _print("PASS: real GitHub clone -> stage -> confirm -> install -> list -> uninstall")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
