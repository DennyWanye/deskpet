# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""MR-S-1 实机 boot smoke — /help slash command 真路径硬证据.

跑法：
    G:\\projects\\deskpet\\backend\\.venv\\Scripts\\python.exe scripts/manual_slash_smoke.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))


async def main():
    print("=" * 70)
    print("MR-S-1 boot smoke — /help slash command 真路径")
    print("=" * 70)

    # 1. 用真 SkillLoader 跑
    from deskpet.skills.loader import SkillLoader
    import deskpet.skills.builtin as _builtin_pkg
    builtin_dir = Path(_builtin_pkg.__file__).parent
    loader = SkillLoader(skill_dirs=[builtin_dir], enable_watch=False)
    loader.reload()  # 触发首次扫描（start() 是 async + 启 watchdog；smoke 用 sync）

    skills = loader.list_skills()
    print(f"\n[1] SkillLoader 加载 {len(skills)} 个 skill")
    for s in skills[:5]:
        name = s.get("name") or "?"
        desc = (s.get("description") or "")[:50]
        print(f"    - {name}: {desc}")

    # 2. 调真 dispatch_slash_command
    from deskpet.commands import dispatch_slash_command

    print("\n[2] dispatch_slash_command('help', '', 'test-sid')")
    res = await dispatch_slash_command(
        "help", "", "test-sid", skill_loader=loader,
    )
    print(f"    type: {res['type']}")
    print(f"    builtins: {len(res['builtins'])} 条")
    print(f"    skills: {len(res['skills'])} 条")
    assert res["type"] == "help", f"expected type=help, got {res['type']}"
    assert len(res["skills"]) > 0, "skills 应不为空"
    print("    [OK] /help 真路径返回非空 skill 列表")

    # 3. 调真 dispatch '/' unknown
    print("\n[3] dispatch_slash_command('notexistcmd', '', 'test-sid')")
    res2 = await dispatch_slash_command(
        "notexistcmd", "", "test-sid", skill_loader=loader,
    )
    print(f"    type: {res2['type']}")
    print(f"    message: {res2.get('message', '')}")
    assert res2["type"] == "error", "unknown 应返 error"
    # SkillLoader.invoke_script 会 raise KeyError → 转 unknown skill error
    assert "unknown" in res2["message"].lower(), \
        f"应含 'unknown'，实际: {res2['message']}"
    print("    [OK] 未知命令真路径返 error")

    print("\n" + "=" * 70)
    print("MR-S-1 BOOT SMOKE PASSED — /help slash command 真接电")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
