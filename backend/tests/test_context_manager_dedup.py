# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Phase 1.2 — File-read dedup（design.md D3）单测。

覆盖 spec `long-run-context` 的 "File-read deduplication supersedes stale
reads" Requirement 3 个场景：
  1. 同 path 读多次 → 旧 tool_result body 原地替换成 superseded marker，
     只保留最近一条；message 数组长度/下标不变（tool_call/tool_result 配对
     不错位）
  2. path 规范化处理 Windows 大小写盘符 / 正反斜杠
  3. write/exec 类工具不去重

dedup 逻辑挂在 ContextManager（`_read_path_seen: dict[str,int]`），
agent loop append tool message 后调用 `dedup_file_reads`。
"""
from __future__ import annotations

from agent.context_manager import ContextManager


def _tool_msg(tool_call_id: str, name: str, content: str) -> dict:
    return {
        "role": "tool",
        "tool_call_id": tool_call_id,
        "name": name,
        "content": content,
    }


def test_reread_supersedes_earlier_copy():
    """Scenario: Re-reading a file supersedes the earlier copy.

    read_file("App.jsx") 在 iter5（6KB body）→ iter12 再读。
    iter5 那条 content 被替换成 superseded marker；iter12 保留完整 body；
    总 message 数不变。
    """
    ctx = ContextManager()
    msgs: list[dict] = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "去看 App.jsx"},
    ]
    body_v1 = "A" * 6000

    # iter5：第一次读
    msgs.append({"role": "assistant", "content": "", "tool_calls": [{"id": "c1"}]})
    msgs.append(_tool_msg("c1", "read_file", body_v1))
    idx_v1 = len(msgs) - 1
    ctx.dedup_file_reads(
        msgs,
        tool_name="read_file",
        tool_args={"path": "App.jsx"},
        new_index=idx_v1,
        iteration=5,
    )
    # 第一次读：什么都不替换
    assert msgs[idx_v1]["content"] == body_v1

    # 中间夹一些别的消息（确保 message 下标稳定不被删）
    msgs.append({"role": "assistant", "content": "想想"})
    body_v2 = "B" * 6200

    # iter12：再次读同一 path
    msgs.append({"role": "assistant", "content": "", "tool_calls": [{"id": "c2"}]})
    msgs.append(_tool_msg("c2", "read_file", body_v2))
    idx_v2 = len(msgs) - 1
    total_before = len(msgs)
    ctx.dedup_file_reads(
        msgs,
        tool_name="read_file",
        tool_args={"path": "App.jsx"},
        new_index=idx_v2,
        iteration=12,
    )

    # 旧那条被替换成 superseded marker
    assert msgs[idx_v1]["content"] == (
        "<file App.jsx was re-read at iteration 12; "
        "superseded — see the later read>"
    )
    # 新那条保留完整 body
    assert msgs[idx_v2]["content"] == body_v2
    # 总 message 数不变（不删消息，只换 content）
    assert len(msgs) == total_before
    # 配对字段（role/tool_call_id/name）不被动
    assert msgs[idx_v1]["role"] == "tool"
    assert msgs[idx_v1]["tool_call_id"] == "c1"
    assert msgs[idx_v1]["name"] == "read_file"


def test_three_reads_only_last_kept():
    """1.2.1：同 path 读 3 次，前 2 条都被替换成 superseded，最后 1 条保留。"""
    ctx = ContextManager()
    msgs: list[dict] = [{"role": "system", "content": "sys"}]
    indices: list[int] = []
    for i, it in enumerate((3, 7, 15)):
        cid = f"c{i}"
        msgs.append({"role": "assistant", "content": "", "tool_calls": [{"id": cid}]})
        msgs.append(_tool_msg(cid, "read_file", f"VERSION{i}-" + "x" * 3000))
        idx = len(msgs) - 1
        indices.append(idx)
        ctx.dedup_file_reads(
            msgs,
            tool_name="read_file",
            tool_args={"path": "/proj/Home.jsx"},
            new_index=idx,
            iteration=it,
        )

    # spec 要求："keeping only the most recent read" —— 前两条 body 都被
    # supersede（不留原文），最后一条保留完整 body。marker 指向"是哪一次
    # 读把它顶掉的"（read#1 在 iter7 被顶 → 指 7；read#2 在 iter15 被顶
    # → 指 15），这比统一指最后一次更可追溯，且同样满足 spec。
    assert "superseded" in msgs[indices[0]]["content"]
    assert "iteration 7" in msgs[indices[0]]["content"]
    assert "VERSION0-" not in msgs[indices[0]]["content"]
    assert "superseded" in msgs[indices[1]]["content"]
    assert "iteration 15" in msgs[indices[1]]["content"]
    assert "VERSION1-" not in msgs[indices[1]]["content"]
    # 最后一条保留完整 body
    assert msgs[indices[2]]["content"].startswith("VERSION2-")
    # message 数组长度 = 1 system + 3*(assistant+tool) = 7
    assert len(msgs) == 7


def test_path_normalization_windows_casing():
    """Scenario: Path normalization handles Windows casing.

    read_file("G:\\proj\\App.jsx") then read_file("g:/proj/App.jsx")
    → 同一 path（resolve + 大小写规范化），第一条 superseded。
    """
    ctx = ContextManager()
    msgs: list[dict] = [{"role": "system", "content": "sys"}]

    msgs.append({"role": "assistant", "content": "", "tool_calls": [{"id": "a"}]})
    msgs.append(_tool_msg("a", "read_file", "first-copy"))
    idx1 = len(msgs) - 1
    ctx.dedup_file_reads(
        msgs,
        tool_name="read_file",
        tool_args={"path": "G:\\proj\\App.jsx"},
        new_index=idx1,
        iteration=2,
    )

    msgs.append({"role": "assistant", "content": "", "tool_calls": [{"id": "b"}]})
    msgs.append(_tool_msg("b", "read_file", "second-copy"))
    idx2 = len(msgs) - 1
    ctx.dedup_file_reads(
        msgs,
        tool_name="read_file",
        tool_args={"path": "g:/proj/App.jsx"},  # 不同大小写 + 正斜杠
        new_index=idx2,
        iteration=9,
    )

    assert "superseded" in msgs[idx1]["content"]
    assert msgs[idx2]["content"] == "second-copy"


def test_path_normalization_forward_back_slash_same():
    """正反斜杠混用视为同一 path。"""
    ctx = ContextManager()
    msgs: list[dict] = [{"role": "system", "content": "sys"}]

    msgs.append({"role": "assistant", "content": "", "tool_calls": [{"id": "a"}]})
    msgs.append(_tool_msg("a", "read_file", "v1"))
    i1 = len(msgs) - 1
    ctx.dedup_file_reads(
        msgs, tool_name="read_file",
        tool_args={"path": "C:/a/b/c.py"}, new_index=i1, iteration=1,
    )
    msgs.append({"role": "assistant", "content": "", "tool_calls": [{"id": "b"}]})
    msgs.append(_tool_msg("b", "read_file", "v2"))
    i2 = len(msgs) - 1
    ctx.dedup_file_reads(
        msgs, tool_name="read_file",
        tool_args={"path": "C:\\a\\b\\c.py"}, new_index=i2, iteration=4,
    )
    assert "superseded" in msgs[i1]["content"]
    assert msgs[i2]["content"] == "v2"


def test_write_exec_tools_not_deduplicated():
    """Scenario: Write/exec tools are not deduplicated.

    run_shell / edit_file 即使"同 path"也不去重——只动 read-class。
    """
    ctx = ContextManager()
    msgs: list[dict] = [{"role": "system", "content": "sys"}]

    for i, (tool, args) in enumerate(
        [
            ("run_shell", {"command": "ls /proj"}),
            ("run_shell", {"command": "ls /proj"}),
            ("edit_file", {"path": "/proj/x.py"}),
            ("edit_file", {"path": "/proj/x.py"}),
            ("write_file", {"path": "/proj/x.py"}),
        ]
    ):
        cid = f"c{i}"
        msgs.append({"role": "assistant", "content": "", "tool_calls": [{"id": cid}]})
        msgs.append(_tool_msg(cid, tool, f"output-{i}"))
        idx = len(msgs) - 1
        ctx.dedup_file_reads(
            msgs, tool_name=tool, tool_args=args, new_index=idx, iteration=i,
        )

    # 没有任何 tool message 被替换成 superseded marker
    for m in msgs:
        if m.get("role") == "tool":
            assert "superseded" not in m["content"]


def test_mcp_filesystem_read_is_read_class():
    """mcp_filesystem_read_text_file 也算 read-class（spec 明列）。"""
    ctx = ContextManager()
    msgs: list[dict] = [{"role": "system", "content": "sys"}]

    msgs.append({"role": "assistant", "content": "", "tool_calls": [{"id": "a"}]})
    msgs.append(_tool_msg("a", "mcp_filesystem_read_text_file", "mcp-v1"))
    i1 = len(msgs) - 1
    ctx.dedup_file_reads(
        msgs, tool_name="mcp_filesystem_read_text_file",
        tool_args={"path": "/p/f.txt"}, new_index=i1, iteration=1,
    )
    msgs.append({"role": "assistant", "content": "", "tool_calls": [{"id": "b"}]})
    msgs.append(_tool_msg("b", "mcp_filesystem_read_text_file", "mcp-v2"))
    i2 = len(msgs) - 1
    ctx.dedup_file_reads(
        msgs, tool_name="mcp_filesystem_read_text_file",
        tool_args={"path": "/p/f.txt"}, new_index=i2, iteration=6,
    )
    assert "superseded" in msgs[i1]["content"]
    assert msgs[i2]["content"] == "mcp-v2"


def test_no_path_arg_is_noop():
    """读类工具但 args 里没有 path（防御）→ 不崩、不去重。"""
    ctx = ContextManager()
    msgs: list[dict] = [{"role": "system", "content": "sys"}]
    msgs.append({"role": "assistant", "content": "", "tool_calls": [{"id": "a"}]})
    msgs.append(_tool_msg("a", "read_file", "body"))
    idx = len(msgs) - 1
    # 不抛
    ctx.dedup_file_reads(
        msgs, tool_name="read_file", tool_args={}, new_index=idx, iteration=1,
    )
    assert msgs[idx]["content"] == "body"


def test_dedup_returns_superseded_count():
    """方法返回被 supersede 的条数（便于 agent loop 落日志/统计）。"""
    ctx = ContextManager()
    msgs: list[dict] = [{"role": "system", "content": "sys"}]

    msgs.append({"role": "assistant", "content": "", "tool_calls": [{"id": "a"}]})
    msgs.append(_tool_msg("a", "read_file", "v1"))
    i1 = len(msgs) - 1
    n0 = ctx.dedup_file_reads(
        msgs, tool_name="read_file",
        tool_args={"path": "/p/f.py"}, new_index=i1, iteration=1,
    )
    assert n0 == 0  # 第一次读，没东西可 supersede

    msgs.append({"role": "assistant", "content": "", "tool_calls": [{"id": "b"}]})
    msgs.append(_tool_msg("b", "read_file", "v2"))
    i2 = len(msgs) - 1
    n1 = ctx.dedup_file_reads(
        msgs, tool_name="read_file",
        tool_args={"path": "/p/f.py"}, new_index=i2, iteration=5,
    )
    assert n1 == 1  # 替换了 1 条旧 body
