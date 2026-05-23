"""记忆系统升级 WI-M0.2 — 中文回测集 fixture（可复现的稳定基准）。

为什么需要它
------------
worktree 的 ``.dev-userdata`` 是空库，造不出有意义的回测集；主 checkout
的真实 ``state.db`` 又因人而异、不可复现。本模块把一份**人工固化的中文
对话 + (query, expected_msg_id) 配对**钉死在代码里 —— 任何机器、任何时间
``seed_zh_fixture(db)`` 都得到逐字节一致的回测集，作为 eval 门控（TG-8）
与 baseline（STAGE0-baseline.md）的稳定参照。

数据形态
--------
* ``_FIXTURE_MESSAGES``：~40 条单人助理对话，跨多个 session、覆盖饮食/
  工作/宠物/旅行/健康/家庭/日程等离散话题，每条话题特征明显，便于召回
  区分。
* ``_FIXTURE_QA``：≥30 条 ``(query, 目标消息下标, tags)`` —— query 是用户
  日后可能问的自然问句，应召回对应那条消息。下标是 ``_FIXTURE_MESSAGES``
  里的 0-based 位置；``seed`` 时解析成真实 ``messages.id``。

用法
----
    from deskpet.memory.eval.zh_fixture import seed_zh_fixture
    info = await seed_zh_fixture("state.db")
    # info = {"messages": N, "qa": M, "session_ids": [...]}

``seed`` 是幂等的：重复 seed 同一个库前会先清掉 ``source='zh_fixture'``
的旧 QA 行（消息不清 —— 消息无来源标记，重复 seed 会重复插，调用方应
对全新临时库 seed）。
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import aiosqlite

from deskpet.memory.memory_v2_schema import ensure_memory_v2_tables

# 回测集来源标记 —— eval CLI / 门控脚本据此识别这批稳定 QA。
FIXTURE_SOURCE = "zh_fixture"

# (session_id, role, content) —— 顺序即 0-based 下标，QA 用下标引用。
_FIXTURE_MESSAGES: list[tuple[str, str, str]] = [
    # --- session A：日常生活偏好 ---
    ("zhfix-a", "user", "我对花生过敏，吃了会喉咙肿，所以零食要特别小心。"),
    ("zhfix-a", "assistant", "记下了，你对花生过敏。以后推荐零食我会避开含花生的。"),
    ("zhfix-a", "user", "我平时不喝咖啡，下午通常喝乌龙茶提神。"),
    ("zhfix-a", "assistant", "好的，你下午习惯喝乌龙茶，不碰咖啡。"),
    ("zhfix-a", "user", "我家有一只叫旺财的橘猫，今年三岁了，特别黏人。"),
    ("zhfix-a", "assistant", "旺财听起来很可爱，三岁的橘猫正是活泼的年纪。"),
    ("zhfix-a", "user", "周末我一般去爬山，最近常去的是西边的青龙山。"),
    ("zhfix-a", "assistant", "青龙山的步道风景不错，周末爬山是很好的放松方式。"),
    # --- session B：工作与项目 ---
    ("zhfix-b", "user", "我现在在做一个叫深海的后端重构项目，主要是把单体拆成微服务。"),
    ("zhfix-b", "assistant", "深海项目的微服务拆分要注意服务边界和数据一致性。"),
    ("zhfix-b", "user", "深海项目的截止日期是六月底，现在进度有点紧张。"),
    ("zhfix-b", "assistant", "六月底交付的话，建议先锁定核心服务的接口契约。"),
    ("zhfix-b", "user", "我们团队用 Python 和 Go 两种语言，网关层是 Go 写的。"),
    ("zhfix-b", "assistant", "网关用 Go、业务服务用 Python 是常见的混合栈选择。"),
    ("zhfix-b", "user", "我的上司叫李梅，她下周三要听一次项目汇报。"),
    ("zhfix-b", "assistant", "那下周三给李梅的汇报，建议提前准备进度和风险两块内容。"),
    # --- session C：健康与作息 ---
    ("zhfix-c", "user", "医生说我有点轻度脂肪肝，让我少吃油炸的，多运动。"),
    ("zhfix-c", "assistant", "轻度脂肪肝通过控制油脂摄入和规律运动通常能改善。"),
    ("zhfix-c", "user", "我最近在坚持每天跑步三公里，已经连续两周了。"),
    ("zhfix-c", "assistant", "连续两周每天跑三公里很不错，注意循序渐进别拉伤。"),
    ("zhfix-c", "user", "我晚上经常失眠，差不多要到凌晨一点才睡得着。"),
    ("zhfix-c", "assistant", "凌晨才入睡的话，可以试试睡前一小时不看手机屏幕。"),
    # --- session D：旅行计划 ---
    ("zhfix-d", "user", "我打算十月去日本旅行，主要想去京都看红叶。"),
    ("zhfix-d", "assistant", "十月底到十一月初的京都红叶季很美，记得提前订住宿。"),
    ("zhfix-d", "user", "这次旅行预算大概两万人民币，打算去十天。"),
    ("zhfix-d", "assistant", "两万预算十天的日本行，机票和住宿大约会占一半。"),
    ("zhfix-d", "user", "我护照下个月就到期了，得赶紧去办新的。"),
    ("zhfix-d", "assistant", "护照快到期一定要先办，否则十月的行程会受影响。"),
    # --- session E：家庭与人际 ---
    ("zhfix-e", "user", "我女儿叫朵朵，今年上小学二年级，特别喜欢画画。"),
    ("zhfix-e", "assistant", "二年级的朵朵喜欢画画，可以多带她去看看画展。"),
    ("zhfix-e", "user", "我妈妈生日是八月十五，今年想给她买个按摩椅。"),
    ("zhfix-e", "assistant", "八月十五给妈妈买按摩椅是很贴心的生日礼物。"),
    ("zhfix-e", "user", "我和老婆是大学同学，明年就结婚十周年了。"),
    ("zhfix-e", "assistant", "大学同学到结婚十周年，这份感情很难得。"),
    # --- session F：兴趣爱好 ---
    ("zhfix-f", "user", "我最近迷上了做手冲咖啡之外的事——其实是学吉他，每天练半小时。"),
    ("zhfix-f", "assistant", "每天练半小时吉他，坚持下来三个月就能弹完整的曲子。"),
    ("zhfix-f", "user", "我收藏了不少黑胶唱片，最喜欢的是七八十年代的摇滚。"),
    ("zhfix-f", "assistant", "七八十年代的摇滚黑胶很有收藏价值，音质也独特。"),
    ("zhfix-f", "user", "我养了一缸热带鱼，里面有孔雀鱼和神仙鱼。"),
    ("zhfix-f", "assistant", "孔雀鱼和神仙鱼混养要注意水温和食量的平衡。"),
]

# (query, 目标消息 0-based 下标, tags)
_FIXTURE_QA: list[tuple[str, int, tuple[str, ...]]] = [
    ("我对什么食物过敏", 0, ("preference", "health")),
    ("我能吃花生吗", 0, ("preference",)),
    ("我下午一般喝什么饮料", 2, ("preference",)),
    ("我喝不喝咖啡", 2, ("preference",)),
    ("我家的猫叫什么名字", 4, ("profile", "pet")),
    ("旺财是什么动物", 4, ("pet",)),
    ("我周末常去哪座山爬山", 6, ("hobby",)),
    ("青龙山在哪个方向", 6, ("hobby",)),
    ("我在做的后端项目叫什么", 8, ("project",)),
    ("深海项目是做什么的", 8, ("project",)),
    ("深海项目什么时候截止", 10, ("project",)),
    ("六月底要交付什么", 10, ("project",)),
    ("我们团队用哪些编程语言", 12, ("project",)),
    ("网关层是用什么语言写的", 12, ("project",)),
    ("我的上司叫什么名字", 14, ("profile", "project")),
    ("李梅什么时候听汇报", 14, ("project",)),
    ("医生说我有什么健康问题", 16, ("health",)),
    ("我的脂肪肝该怎么注意", 16, ("health",)),
    ("我最近在坚持什么运动", 18, ("health",)),
    ("我每天跑几公里", 18, ("health",)),
    ("我晚上几点才睡得着", 20, ("health",)),
    ("我有失眠的问题吗", 20, ("health",)),
    ("我打算几月去日本", 22, ("travel",)),
    ("我去京都想看什么", 22, ("travel",)),
    ("这次日本旅行预算多少", 24, ("travel",)),
    ("我打算去日本玩几天", 24, ("travel",)),
    ("我的护照什么时候到期", 26, ("travel",)),
    ("我女儿叫什么名字", 28, ("family",)),
    ("朵朵上几年级了", 28, ("family",)),
    ("我妈妈的生日是哪天", 30, ("family",)),
    ("今年想给妈妈买什么礼物", 30, ("family",)),
    ("我和老婆是怎么认识的", 32, ("family",)),
    ("我最近在学什么乐器", 34, ("hobby",)),
    ("我收藏了什么唱片", 36, ("hobby",)),
    ("我养的热带鱼有哪些品种", 38, ("hobby", "pet")),
]


async def seed_zh_fixture(db_path: str | Path) -> dict[str, Any]:
    """把中文回测集 fixture 灌进 ``db_path``。

    步骤：① 用 ``SessionDB.initialize()`` 建出**真实** state.db schema
    （migration 链，FTS5 trigger 就位）；② 用 ``append_message`` 顺序插入
    ~40 条 fixture 消息（拿真实 id、FTS5 自动同步）；③ 确保 v2 表，清掉
    旧的 ``source='zh_fixture'`` QA 行，按下标把 ≥30 条 QA 解析成
    ``(query, 真实 expected_msg_id)`` 插入 ``memory_qa_set``。

    对**全新临时库**调用 —— 重复 seed 同一个库会重复插消息。返回
    ``{"messages": N, "qa": M, "session_ids": [...]}``。
    """
    db_path = Path(db_path)
    # 先建真实 schema —— 不自己 CREATE messages，避免和 migration 链
    # （002 会 ALTER ADD is_summary）撞列。
    from deskpet.memory.session_db import SessionDB

    sdb = SessionDB(db_path=db_path)
    await sdb.initialize()
    msg_ids: list[int] = []
    for sid, role, content in _FIXTURE_MESSAGES:
        mid = await sdb.append_message(session_id=sid, role=role, content=content)
        msg_ids.append(int(mid))

    await ensure_memory_v2_tables(db_path)
    now = time.time()
    import json
    async with aiosqlite.connect(db_path) as db:
        await db.execute("PRAGMA busy_timeout=5000")
        await db.execute(
            "DELETE FROM memory_qa_set WHERE source = ?", (FIXTURE_SOURCE,)
        )
        qa_count = 0
        for query, msg_idx, tags in _FIXTURE_QA:
            expected_id = msg_ids[msg_idx]
            await db.execute(
                "INSERT INTO memory_qa_set("
                "source, query, expected_msg_id, tags, created_at, notes"
                ") VALUES (?, ?, ?, ?, ?, ?)",
                (
                    FIXTURE_SOURCE, query, expected_id,
                    json.dumps(list(tags)), now,
                    "中文回测集 fixture (WI-M0.2)",
                ),
            )
            qa_count += 1
        await db.commit()
    return {
        "messages": len(msg_ids),
        "qa": qa_count,
        "session_ids": sorted({s for s, _, _ in _FIXTURE_MESSAGES}),
    }
