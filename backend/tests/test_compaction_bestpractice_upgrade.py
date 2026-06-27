# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""Phase 1 单测 — compaction-bestpractice-upgrade WI-1/2/3/4a。

对照 plans/2026-06-16-compaction-bestpractice-upgrade/00-PLAN.md §4 的可证伪断言。
"""
from __future__ import annotations

import pytest

from deskpet.agent.context_compressor import (
    ContextCompressor,
    _extract_prior_summary,
    _microcompact_tool_results,
    _format_summary,
    _looks_reflective,
    _SUMMARY_MARKER,
)


# ───────────────────────────── WI-1 buffer 触发 ─────────────────────────────
class TestWI1BufferTrigger:
    @pytest.mark.parametrize(
        "window,eff_pct,compact_pct,exp_reserve,exp_trigger",
        [
            # gpt-5.5 400K: reserve=12500, trigger=min(320000, 380000-12500)=320000
            (400_000, 0.95, 0.80, 12_500, 320_000),
            # deepseek 1M: reserve=31250, trigger=min(750000, 950000-31250)=750000
            (1_000_000, 0.95, 0.75, 31_250, 750_000),
            # _default 32K: reserve=8000, trigger=min(25600, 28800-8000)=20800
            (32_000, 0.90, 0.80, 8_000, 20_800),
        ],
    )
    def test_buffer_formula_and_trigger_line(
        self, window, eff_pct, compact_pct, exp_reserve, exp_trigger
    ):
        c = ContextCompressor(
            context_window=window,
            threshold_percent=compact_pct,
            effective_pct=eff_pct,
        )
        assert c.output_reserve() == exp_reserve
        assert c.trigger_tokens() == exp_trigger

    def test_trigger_boundary(self):
        # _default 32K → trigger 20800
        c = ContextCompressor(
            context_window=32_000, threshold_percent=0.80, effective_pct=0.90
        )
        assert c.should_compress(20_799) is False
        assert c.should_compress(20_800) is True

    def test_bc_when_no_effective_pct(self):
        """effective_pct=None → 纯比例阈值(旧单测/BC),buffer 不参与。"""
        c = ContextCompressor(context_window=1000, threshold_percent=0.75)
        assert c.trigger_tokens() == 750
        assert c.should_compress(749) is False
        assert c.should_compress(750) is True

    def test_tiny_window_falls_back_to_ratio(self):
        """极小窗口 eff_win-buffer<0 → 忽略 buffer 项,退回比例阈值(不会全触发)。"""
        c = ContextCompressor(
            context_window=1000, threshold_percent=0.75, effective_pct=0.95
        )
        # eff_win=950, reserve=8000 → buffer_line 负 → 退回 750
        assert c.trigger_tokens() == 750
        assert c.should_compress(749) is False


# ───────────────────────────── WI-2 microcompact ─────────────────────────────
class TestWI2Microcompact:
    def _msgs_with_tools(self, n_tools=5):
        out = [{"role": "system", "content": "sys"}]
        for i in range(n_tools):
            out.append(
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"id": f"tc{i}", "function": {"name": "web_fetch"}}
                    ],
                }
            )
            out.append(
                {"role": "tool", "tool_call_id": f"tc{i}", "content": "RESULT" * 2000}
            )
        out.append({"role": "user", "content": "继续"})
        return out

    def test_prunes_stale_keeps_recent_and_shell(self):
        msgs = self._msgs_with_tools(5)
        out, n = _microcompact_tool_results(msgs, keep_recent_tools=2)
        assert n == 3  # 5 个 tool,最近 2 个保护 → 清 3 个
        tools = [m for m in out if m.get("role") == "tool"]
        assert len(tools) == 5  # 整条壳保留,绝不删整条
        # 最近 2 个原文保留
        assert tools[-1]["content"] == "RESULT" * 2000
        assert tools[-2]["content"] == "RESULT" * 2000
        # 更早 3 个换占位,但 tool_call_id + role 保留
        for t in tools[:3]:
            assert "已清理" in t["content"]
            assert t["tool_call_id"].startswith("tc")
            assert t["role"] == "tool"

    def test_no_tools_noop(self):
        msgs = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "ho"}]
        out, n = _microcompact_tool_results(msgs, keep_recent_tools=3)
        assert n == 0
        assert out == msgs

    def test_idempotent_placeholder_not_recounted(self):
        msgs = self._msgs_with_tools(5)
        out1, n1 = _microcompact_tool_results(msgs, keep_recent_tools=2)
        out2, n2 = _microcompact_tool_results(out1, keep_recent_tools=2)
        assert n1 == 3
        assert n2 == 0  # 占位串不再被重复清理

    @pytest.mark.asyncio
    async def test_compress_microcompact_only_skips_haiku(self):
        """microcompact 后已降到触发线下 → 不调 haiku(mock 计数=0)。"""

        class _CountingLLM:
            def __init__(self):
                self.calls = 0

            async def chat_with_fallback(self, *a, **k):
                self.calls += 1

                class R:
                    content = "S"

                return R()

        llm = _CountingLLM()
        # window 大到 microcompact 后总量 < trigger
        c = ContextCompressor(
            llm_registry=llm, context_window=200_000, threshold_percent=0.75,
            microcompact_keep_tools=1,
        )
        msgs = self._msgs_with_tools(5)
        r = await c.compress(msgs)
        assert r.compressed is True
        assert r.meta.get("reason") == "microcompact_only"
        assert llm.calls == 0  # 没调模型

    @pytest.mark.asyncio
    async def test_microcompact_output_no_orphan_tool(self):
        from deskpet.agent.context_compressor import _sanitize_tool_pairs

        msgs = self._msgs_with_tools(5)
        out, _ = _microcompact_tool_results(msgs, keep_recent_tools=2)
        sanitized = _sanitize_tool_pairs(out)
        # 占位后每个 tool 仍有配对 assistant → 不产孤儿(条数不减)
        assert len([m for m in sanitized if m.get("role") == "tool"]) == 5

    @pytest.mark.asyncio
    async def test_safe_fail_preserves_microcompact(self):
        """haiku 失败时退回 microcompact 后的 work(占位仍在),不丢收益。"""

        class _RaisingLLM:
            async def chat_with_fallback(self, *a, **k):
                raise RuntimeError("haiku down")

        # 小窗口逼到必须走 haiku(microcompact 不够) → 失败 → 应返回 work
        c = ContextCompressor(
            llm_registry=_RaisingLLM(), context_window=8_000,
            threshold_percent=0.75, microcompact_keep_tools=1,
        )
        msgs = self._msgs_with_tools(5)
        r = await c.compress(msgs)
        assert r.compressed is True  # microcompact 生效
        assert r.meta.get("tool_results_pruned", 0) >= 1
        tool_contents = [m["content"] for m in r.messages if m.get("role") == "tool"]
        assert any("已清理" in t for t in tool_contents)

    # ─── WI-1B-5 microcompact size-aware（flag OFF=keep N 不变；ON=N + 字节预算）───
    def test_microcompact_keep_default_unchanged(self):
        """默认构造 keep=3 不变 + keep_bytes=None → 行为字节级 BC。"""
        c = ContextCompressor()
        assert c.microcompact_keep_tools == 3
        assert c.microcompact_size_aware is False
        # _microcompact_tool_results 默认 keep_bytes=None → 纯"最近 N 条"。
        msgs = self._msgs_with_tools(5)
        out_default, n_default = _microcompact_tool_results(
            msgs, keep_recent_tools=3
        )
        out_explicit, n_explicit = _microcompact_tool_results(
            msgs, keep_recent_tools=3, keep_bytes=None
        )
        assert n_default == n_explicit == 2  # 5 个 tool,保护最近 3 → 清 2
        assert out_default == out_explicit

    def test_microcompact_size_aware(self):
        """ON: 最近 N 条里有巨型 tool_result → 字节预算耗尽,更旧的纳入压缩判断。

        最近 3 条若全保护会留 3×(RESULT*2000)≈36KB 字节。给 keep_bytes 只够
        装最近 1 条(每条 ~12KB),size-aware 应只保护最近 1 条 → 清掉 4 条,
        而非纯"最近 N 条"路径只清 2 条。
        """
        msgs = self._msgs_with_tools(5)
        # 单条 content 字节数（RESULT*2000 = 12000 ASCII bytes）。
        one_body = len(("RESULT" * 2000).encode("utf-8"))
        # 预算只够 1 条多一点、不够 2 条。
        budget = one_body + 100
        out, n = _microcompact_tool_results(
            msgs, keep_recent_tools=3, keep_bytes=budget
        )
        # 纯"最近 3 条"会清 2；size-aware 预算只够最近 1 条 → 清 4。
        assert n == 4
        tools = [m for m in out if m.get("role") == "tool"]
        assert len(tools) == 5  # 整条壳仍保留
        # 仅最近 1 条原文保留,其余 4 条换占位。
        assert tools[-1]["content"] == "RESULT" * 2000
        for t in tools[:4]:
            assert "已清理" in t["content"]
            assert t["role"] == "tool"

    def test_microcompact_size_aware_budget_fits_all_recent(self):
        """预算充足时 size-aware 退化为纯'最近 N 条'（与 BC 同结果）。"""
        msgs = self._msgs_with_tools(5)
        out_sa, n_sa = _microcompact_tool_results(
            msgs, keep_recent_tools=3, keep_bytes=10_000_000
        )
        out_bc, n_bc = _microcompact_tool_results(
            msgs, keep_recent_tools=3, keep_bytes=None
        )
        assert n_sa == n_bc == 2
        assert out_sa == out_bc


# ───────────────────────── WI-3 结构化摘要 + 锚定增量 ─────────────────────────
class TestWI3StructuredSummaryAndAnchoring:
    def test_summary_system_has_full_schema(self):
        s = ContextCompressor._SUMMARY_SYSTEM
        for seg in ["意图", "进行中", "已完成", "关键事实", "文件", "待办", "下一步"]:
            assert seg in s, f"missing segment: {seg}"

    def test_extract_prior_summary_pure(self):
        prior_msg = {"role": "assistant", "content": _format_summary("旧摘要内容")}
        msgs = [
            {"role": "user", "content": "a"},
            prior_msg,
            {"role": "assistant", "content": "b"},
        ]
        prior, kept = _extract_prior_summary(msgs)
        assert prior == "旧摘要内容"
        # 旧摘要从待摘列表剔除
        assert all(_SUMMARY_MARKER not in str(m.get("content")) for m in kept)
        assert len(kept) == 2

    def test_extract_prior_none_when_absent(self):
        msgs = [{"role": "user", "content": "x"}]
        prior, kept = _extract_prior_summary(msgs)
        assert prior is None
        assert kept == msgs

    @pytest.mark.asyncio
    async def test_summary_transcript_excludes_old_marker(self):
        """喂 haiku 的待摘 transcript 不含旧 [压缩摘要] 前缀(防套娃)。"""

        captured = {}

        class _LLM:
            async def chat_with_fallback(self, messages, **k):
                captured["user"] = messages[1]["content"]
                captured["system"] = messages[0]["content"]

                class R:
                    content = "NEW SUMMARY"

                return R()

        c = ContextCompressor(llm_registry=_LLM(), first_n=1, last_n=1)
        old = {"role": "assistant", "content": _format_summary("PRIOR-STATE-XYZ")}
        msgs = [{"role": "user", "content": "u0"}]
        msgs += [{"role": "assistant", "content": f"mid{i}" * 30} for i in range(3)]
        msgs.insert(2, old)
        msgs += [{"role": "user", "content": "u-last"}]
        await c.compress(msgs)
        assert _SUMMARY_MARKER not in captured["user"]
        # prior 作为独立段拼进 system
        assert "PRIOR-STATE-XYZ" in captured["system"]
        assert "已有摘要" in captured["system"]

    @pytest.mark.asyncio
    async def test_no_nesting_single_summary_after_two_compressions(self):
        class _LLM:
            async def chat_with_fallback(self, *a, **k):
                class R:
                    content = "SUMMARY-CONTENT"

                return R()

        c = ContextCompressor(llm_registry=_LLM(), first_n=1, last_n=1)
        msgs = [{"role": "user", "content": "u0"}]
        msgs += [{"role": "assistant", "content": f"m{i}" * 40} for i in range(8)]
        msgs += [{"role": "user", "content": "u-last"}]
        r1 = await c.compress(msgs)
        # 第二次压缩(把第一次产物再压)
        more = list(r1.messages) + [
            {"role": "assistant", "content": f"x{i}" * 40} for i in range(6)
        ] + [{"role": "user", "content": "u-last2"}]
        r2 = await c.compress(more)
        n_summary = sum(
            1 for m in r2.messages
            if str(m.get("content") or "").lstrip().startswith(_SUMMARY_MARKER)
        )
        assert n_summary <= 1, f"摘要套娃: {n_summary} 条 [压缩摘要]"


# ───────────────────────────── WI-4a 目标 always-on ─────────────────────────────
class TestWI4aGoalAnchor:
    @pytest.mark.asyncio
    async def test_compress_dedups_when_anchor_already_present(self):
        """system 段已有 [目标锚定] → compress 不再注第二条(≤1 DoD)。"""

        class _LLM:
            async def chat_with_fallback(self, *a, **k):
                class R:
                    content = "S"

                return R()

        c = ContextCompressor(llm_registry=_LLM())
        msgs = [
            {"role": "system", "content": "[目标锚定] 当前目标：已存在"},
        ]
        msgs += [{"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}" * 50}
                 for i in range(30)]
        r = await c.compress(msgs, goal_text="新目标会想注入但应被去重")
        anchors = [
            m for m in r.messages
            if m.get("role") == "system" and str(m.get("content")).startswith("[目标锚定]")
        ]
        assert len(anchors) == 1
        assert "已存在" in anchors[0]["content"]

    @pytest.mark.asyncio
    async def test_compress_still_injects_when_no_existing_anchor(self):
        """无 always-on 锚(独立调用,如 CLI/单测) → 仍按 goal_text 注一条(BC)。"""

        class _LLM:
            async def chat_with_fallback(self, *a, **k):
                class R:
                    content = "S"

                return R()

        c = ContextCompressor(llm_registry=_LLM())
        msgs = [{"role": "system", "content": "sys"}]
        msgs += [{"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}" * 50}
                 for i in range(30)]
        r = await c.compress(msgs, goal_text="独立目标")
        anchors = [
            m for m in r.messages
            if m.get("role") == "system" and str(m.get("content")).startswith("[目标锚定]")
        ]
        assert len(anchors) == 1
        assert "独立目标" in anchors[0]["content"]

    def test_goal_store_get_pending_tasks(self):
        """always-on 子目标数据源: SessionGoalStore.get_pending_tasks 返回 subgoals。"""
        from deskpet.agent.goal_store import SessionGoalStore

        store = SessionGoalStore()
        # 无目标 → 空
        assert store.get_pending_tasks("s0") == []
        g = store.set("s1", "整理周报")
        # 有目标但无子目标 → 空
        assert store.get_pending_tasks("s1") == []
        g.subgoals = ["收集数据", "汇总成稿"]
        assert store.get_pending_tasks("s1") == ["收集数据", "汇总成稿"]


# ───────────────────────── 反射 guard (caveat #2 加固) ─────────────────────────
class TestReflectionGuard:
    def test_looks_reflective_detects_meta(self):
        # 真机观测到的反射样本(复述压缩提示词)
        reflective = (
            "【进行中/当前任务】用户要把一段对话历史压缩成更省 token 的摘要，"
            "按意图/目标分段输出，用第三人称、不要杜撰。"
        )
        assert _looks_reflective(reflective) is True

    def test_looks_reflective_passes_real_task(self):
        real = (
            "【进行中/当前任务】用户在让助手调研宁德时代2024年报的营收和净利润，"
            "已查到营业收入约3620亿元，还差研发投入数据。"
        )
        assert _looks_reflective(real) is False

    def test_single_signal_not_flagged(self):
        # 真实对话碰巧提一次"压缩对话"不应误判(需 ≥2 信号)
        assert _looks_reflective("用户问怎么压缩对话框的字体大小") is False

    def test_extract_prior_skips_reflective(self):
        refl = _format_summary("用户要把对话历史压缩成摘要，分段输出，第三人称，不要杜撰")
        clean = _format_summary("用户在调研宁德时代年报")
        # 中段含一条反射旧摘要 → 不作为 prior 带入
        _, _ = _extract_prior_summary([{"role": "assistant", "content": refl}])
        prior_refl, _ = _extract_prior_summary([{"role": "assistant", "content": refl}])
        assert prior_refl is None  # 反射 prior 被丢弃
        prior_clean, _ = _extract_prior_summary([{"role": "assistant", "content": clean}])
        assert prior_clean == "用户在调研宁德时代年报"

    @pytest.mark.asyncio
    async def test_reflective_output_falls_back_to_prior(self):
        """新摘要反射 + 中段有干净旧摘要 → 输出回退到干净 prior,不落反射。"""

        class _ReflectiveLLM:
            async def chat_with_fallback(self, *a, **k):
                class R:
                    content = ("用户要把对话历史压缩成摘要，按分段输出，"
                               "第三人称，省 token，不要杜撰。")
                return R()

        c = ContextCompressor(llm_registry=_ReflectiveLLM(), first_n=1, last_n=1)
        clean_prior = _format_summary("用户在调研宁德时代2024年报核心财务数据")
        msgs = [
            {"role": "user", "content": "u0"},
            {"role": "assistant", "content": clean_prior},
            {"role": "user", "content": "m1" * 30},
            {"role": "assistant", "content": "m2" * 30},
            {"role": "user", "content": "u-last"},
        ]
        r = await c.compress(msgs)
        # 注入的摘要应是干净 prior,不是反射新摘要
        summary_msgs = [
            m for m in r.messages
            if str(m.get("content") or "").lstrip().startswith(_SUMMARY_MARKER)
        ]
        assert len(summary_msgs) == 1
        body = summary_msgs[0]["content"]
        assert "宁德时代" in body
        assert "不要杜撰" not in body  # 反射内容没落地


# ───────────────────────── WI-4b pre-flush 落 L1 (Phase 2) ─────────────────────────
class TestWI4bPreflush:
    @pytest.mark.asyncio
    async def test_preflush_writes_task_state_once(self):
        """触发压缩 → pre-flush 把任务态 append 到 L1 'memory',每 run 限一次。"""
        from agent.agent_loop import AgentLoop
        from deskpet.agent.context_compressor import CompressionResult

        class _FakeTools:
            def schemas(self, enabled_toolsets=None):
                return []

            async def execute_tool(self, name, args, task_id):
                return '{"ok": true}'

        class _FakeLLM:
            async def chat_with_fallback(self, messages, *, tools=None, model=None, **kw):
                from llm.types import ChatResponse
                return ChatResponse(
                    content="done", stop_reason="end_turn", tool_calls=[],
                    usage={"input_tokens": 5, "output_tokens": 3},
                )

        class _FakeCompressor:
            def should_compress(self, n):
                return True

            async def compress(self, messages, *, goal_text=None, pending_tasks=None):
                return CompressionResult(messages=list(messages), compressed=False)

        class _FakeGoalStore:
            def get_goal_text(self, sid):
                return "整理三份文档"

        class _RecordingFileMemory:
            def __init__(self):
                self.calls = []

            async def append(self, target, content, salience=0.5):
                self.calls.append({"target": target, "content": content, "sal": salience})

        fm = _RecordingFileMemory()
        loop = AgentLoop(
            llm_registry=_FakeLLM(),
            tool_registry=_FakeTools(),
            compressor=_FakeCompressor(),
            session_goal_store=_FakeGoalStore(),
            file_memory=fm,
        )
        msgs = [{"role": "user", "content": "请帮我整理文档"}]
        msgs += [{"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}"}
                 for i in range(4)]
        async for _ in loop.run(msgs, session_id="s-pf"):
            pass

        assert len(fm.calls) == 1, f"pre-flush 应限频一次, 实际 {len(fm.calls)}"
        c = fm.calls[0]
        assert c["target"] == "memory"
        assert "任务态" in c["content"]
        assert "整理三份文档" in c["content"]

    @pytest.mark.asyncio
    async def test_no_preflush_when_file_memory_none(self):
        """file_memory=None → 不 flush(BC)。"""
        from agent.agent_loop import AgentLoop

        sig_params = AgentLoop.__init__.__doc__  # smoke
        import inspect
        assert "file_memory" in inspect.signature(AgentLoop.__init__).parameters
        assert inspect.signature(AgentLoop.__init__).parameters["file_memory"].default is None


# ═══════════════════════════════════════════════════════════════════════════
# 一轮对抗式挑战 — 补强单测 (2026-06-16)
# 找到的覆盖缺口按 WI 分组,断言以实跑探查为准、可证伪。
# ═══════════════════════════════════════════════════════════════════════════


# ───────────────────── WI-1 边界缺口 (原 25 测未覆盖) ─────────────────────
class TestWI1EdgeGaps:
    def test_should_compress_shortcircuits_zero_window(self):
        """context_window<=0 → should_compress 永 False(短路,即便 prompt 巨大)。"""
        c = ContextCompressor(
            context_window=0, threshold_percent=0.75, effective_pct=0.95
        )
        assert c.should_compress(10**9) is False

    def test_should_compress_shortcircuits_zero_threshold(self):
        """threshold_percent<=0 → should_compress 永 False(短路)。"""
        c = ContextCompressor(
            context_window=1000, threshold_percent=0.0, effective_pct=0.95
        )
        assert c.should_compress(10**9) is False

    def test_effective_window_none_is_full_window(self):
        """effective_pct=None → effective_window() 按 ×1.0 = 整窗(不缩水)。"""
        c = ContextCompressor(context_window=123_456)
        assert c.effective_window() == 123_456

    def test_effective_window_with_pct(self):
        c = ContextCompressor(context_window=400_000, effective_pct=0.95)
        assert c.effective_window() == 380_000

    def test_trigger_buffer_wins_over_threshold(self):
        """buffer 先到的分支: threshold 高、有效窗口紧 → trigger = buffer_line。

        window=400K, compact_pct=0.99 → threshold=396000;
        eff_pct=0.90 → eff_win=360000, reserve=12500 → buffer_line=347500 < threshold
        ⇒ min 取 buffer_line。(原测只覆盖 threshold 先到的三个 case)
        """
        c = ContextCompressor(
            context_window=400_000, threshold_percent=0.99, effective_pct=0.90
        )
        assert c.threshold_tokens() == 396_000
        assert c.effective_window() - c.output_reserve() == 347_500
        assert c.trigger_tokens() == 347_500

    def test_output_reserve_floor_and_ceil_clamp(self):
        """output_reserve 公式上下限钳制: 极小窗口→8K floor, 极大窗口→32K ceil。"""
        tiny = ContextCompressor(context_window=10_000, effective_pct=0.95)
        assert tiny.output_reserve() == 8_000  # 10000//32=312 → floor 8000
        huge = ContextCompressor(context_window=10_000_000, effective_pct=0.95)
        assert huge.output_reserve() == 32_000  # //32 巨大 → ceil 32000


# ───────────────────── WI-2 microcompact 边界缺口 ─────────────────────
class TestWI2MicrocompactEdgeGaps:
    def test_keep_recent_tools_zero_prunes_all(self):
        """keep_recent_tools=0 → 全部 tool 被清(无保护,不留最后一个)。"""
        msgs = []
        for i in range(3):
            msgs.append({"role": "assistant", "content": "",
                         "tool_calls": [{"id": f"tc{i}", "function": {"name": "f"}}]})
            msgs.append({"role": "tool", "tool_call_id": f"tc{i}", "content": "DATA" * 100})
        out, n = _microcompact_tool_results(msgs, keep_recent_tools=0)
        assert n == 3
        assert all("已清理" in m["content"] for m in out if m["role"] == "tool")

    def test_multi_tool_per_assistant_protection_counted_per_tool_msg(self):
        """一个 assistant 配多个 tool 消息时,保护计数按【tool 消息】粒度,不按 assistant。

        keep_recent=1 → 只护最后一条 tool(id=b),同 assistant 的前一条(id=a)仍清。
        """
        msgs = [
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "a", "function": {"name": "f"}},
                {"id": "b", "function": {"name": "g"}},
            ]},
            {"role": "tool", "tool_call_id": "a", "content": "RA" * 100},
            {"role": "tool", "tool_call_id": "b", "content": "RB" * 100},
        ]
        out, n = _microcompact_tool_results(msgs, keep_recent_tools=1)
        assert n == 1
        by_id = {m["tool_call_id"]: m for m in out if m["role"] == "tool"}
        assert "已清理" in by_id["a"]["content"]      # 早的清
        assert "已清理" not in by_id["b"]["content"]  # 最近的护

    def test_recent_k_counted_by_tool_index_not_message_index(self):
        """tool 消息夹杂非 tool 消息时,"最近 K 个" 按 tool 出现次序取,不受中间普通消息干扰。"""
        msgs = [
            {"role": "assistant", "content": "", "tool_calls": [{"id": "t0", "function": {"name": "f"}}]},
            {"role": "tool", "tool_call_id": "t0", "content": "R0" * 50},
            {"role": "user", "content": "夹在中间的普通消息"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "t1", "function": {"name": "f"}}]},
            {"role": "tool", "tool_call_id": "t1", "content": "R1" * 50},
        ]
        out, n = _microcompact_tool_results(msgs, keep_recent_tools=1)
        assert n == 1
        by_id = {m["tool_call_id"]: m for m in out if m["role"] == "tool"}
        assert "已清理" in by_id["t0"]["content"]
        assert "已清理" not in by_id["t1"]["content"]

    def test_empty_content_tool_not_counted(self):
        """空 content 的 tool 不被计入清理(无正文可清),不产生假阳性 n。"""
        msgs = [
            {"role": "assistant", "content": "", "tool_calls": [{"id": "t0", "function": {"name": "f"}}]},
            {"role": "tool", "tool_call_id": "t0", "content": ""},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "t1", "function": {"name": "f"}}]},
            {"role": "tool", "tool_call_id": "t1", "content": "REAL" * 100},
        ]
        out, n = _microcompact_tool_results(msgs, keep_recent_tools=0)
        # 只有 t1 有正文 → 只清 1 条(t0 空内容不算)
        assert n == 1

    @pytest.mark.asyncio
    async def test_microcompact_only_keep0_no_orphan_after_sanitize(self):
        """microcompact-only 早退(keep_tools=0 全清) → _sanitize 不产孤儿(壳+配对仍齐)。"""
        from deskpet.agent.context_compressor import _sanitize_tool_pairs, _partition

        msgs = [{"role": "system", "content": "s"}]
        for i in range(5):
            msgs.append({"role": "assistant", "content": "",
                         "tool_calls": [{"id": f"tc{i}", "function": {"name": "f"}}]})
            msgs.append({"role": "tool", "tool_call_id": f"tc{i}", "content": "R" * 2000})
        msgs.append({"role": "user", "content": "go"})

        class _CountingLLM:
            def __init__(self): self.calls = 0
            async def chat_with_fallback(self, *a, **k):
                self.calls += 1
                class R: content = "S"
                return R()

        llm = _CountingLLM()
        c = ContextCompressor(
            llm_registry=llm, context_window=200_000, threshold_percent=0.75,
            microcompact_keep_tools=0,
        )
        r = await c.compress(msgs)
        assert r.meta.get("reason") == "microcompact_only"
        assert llm.calls == 0
        # 早退输出已经过 _sanitize → 每条 tool 仍有紧邻配对 assistant,无孤儿
        tools = [m for m in r.messages if m.get("role") == "tool"]
        assert len(tools) == 5
        # 协议合法性: 再 sanitize 一次条数不变(幂等,无孤儿可删)
        again = _sanitize_tool_pairs(r.messages)
        assert len([m for m in again if m.get("role") == "tool"]) == 5


# ───────────────────── WI-3 prior 抽取 + 透传 边界缺口 ─────────────────────
class TestWI3PriorExtractionEdgeGaps:
    def test_extract_prior_takes_last_of_multiple(self):
        """多条旧摘要 → prior 取最后一条;所有旧摘要均从待摘列表剔除。"""
        msgs = [
            {"role": "assistant", "content": _format_summary("OLD-FIRST")},
            {"role": "user", "content": "中间"},
            {"role": "assistant", "content": _format_summary("OLD-SECOND")},
        ]
        prior, kept = _extract_prior_summary(msgs)
        assert prior == "OLD-SECOND"
        assert all(not str(m.get("content") or "").lstrip().startswith(_SUMMARY_MARKER) for m in kept)
        assert len(kept) == 1  # 仅中间普通消息留下

    def test_extract_prior_tolerates_leading_whitespace(self):
        """旧摘要前有空白/换行/缩进 → 仍能识别 marker(用 lstrip)。"""
        content = "   \n  " + _format_summary("INDENTED-PRIOR")
        prior, kept = _extract_prior_summary([{"role": "assistant", "content": content}])
        assert prior == "INDENTED-PRIOR"
        assert kept == []  # 被剔除

    @pytest.mark.asyncio
    async def test_passthrough_emits_single_summary_no_haiku(self):
        """中段去掉旧摘要后为空 → 透传 prior: 恰 1 条摘要 + 不调 haiku(省 token)。"""

        class _CountingLLM:
            def __init__(self): self.calls = 0
            async def chat_with_fallback(self, *a, **k):
                self.calls += 1
                class R: content = "SHOULD-NOT-BE-CALLED"
                return R()

        llm = _CountingLLM()
        c = ContextCompressor(llm_registry=llm, first_n=1, last_n=1)
        msgs = [
            {"role": "user", "content": "u0"},
            {"role": "assistant", "content": _format_summary("PRIOR-PASS-XYZ")},
            {"role": "user", "content": "u-last"},
        ]
        r = await c.compress(msgs)
        assert r.meta.get("reason") == "prior_summary_passthrough"
        assert llm.calls == 0  # 不再走 haiku
        summaries = [
            m for m in r.messages
            if str(m.get("content") or "").lstrip().startswith(_SUMMARY_MARKER)
        ]
        assert len(summaries) == 1
        assert "PRIOR-PASS-XYZ" in summaries[0]["content"]

    @pytest.mark.asyncio
    async def test_prior_passed_as_separate_system_segment_not_in_transcript(self):
        """prior 作独立 system 段拼进 summary_system,不混进喂 haiku 的待摘 transcript。"""
        captured = {}

        class _LLM:
            async def chat_with_fallback(self, messages, **k):
                captured["system"] = messages[0]["content"]
                captured["user"] = messages[1]["content"]
                class R: content = "NEW-SUMMARY"
                return R()

        c = ContextCompressor(llm_registry=_LLM(), first_n=1, last_n=1)
        msgs = [
            {"role": "user", "content": "u0"},
            {"role": "assistant", "content": _format_summary("PRIOR-STATE-ABC")},
            {"role": "user", "content": "real-mid-1" * 20},
            {"role": "assistant", "content": "real-mid-2" * 20},
            {"role": "user", "content": "u-last"},
        ]
        await c.compress(msgs)
        assert "PRIOR-STATE-ABC" in captured["system"]
        assert "已有摘要" in captured["system"]
        assert _SUMMARY_MARKER not in captured["user"]
        assert "PRIOR-STATE-ABC" not in captured["user"]


# ───────────────────── 反射 guard 边界 + 大小写缺口 ─────────────────────
class TestReflectionGuardEdgeGaps:
    def test_exactly_one_signal_not_reflective(self):
        """恰好 1 个反射信号 → 不判反射(阈值 ≥2,防误伤真实对话偶提)。"""
        assert _looks_reflective("用户问怎么省 token 调用成本") is False

    def test_exactly_two_signals_is_reflective(self):
        """恰好 2 个不同信号 → 判反射(边界达成)。"""
        # 信号1: 压缩+对话/历史; 信号2: 省token
        assert _looks_reflective("用户要压缩对话历史并省 token") is True

    def test_省token_is_case_insensitive(self):
        """省token 信号 IGNORECASE — 大写 TOKEN 也命中(省 token 提示词省写法)。"""
        # 两个信号: 压缩对话(压缩.{0,8}对话/历史) + 省 TOKEN(大写, IGNORECASE)
        assert _looks_reflective("压缩对话历史，省 TOKEN") is True

    def test_empty_text_not_reflective(self):
        assert _looks_reflective("") is False
        assert _looks_reflective(None) is False  # None-safe

    @pytest.mark.asyncio
    async def test_reflective_output_no_clean_prior_kept_not_crash(self):
        """新摘要反射但【无干净 prior】→ 保留该反射摘要 + 不崩(only 告警, 任务连续性靠 always-on 兜)。"""

        class _ReflectiveLLM:
            async def chat_with_fallback(self, *a, **k):
                class R:
                    content = ("用户要把对话历史压缩成摘要，分段输出，"
                               "第三人称，省 token，不要杜撰。")
                return R()

        c = ContextCompressor(llm_registry=_ReflectiveLLM(), first_n=1, last_n=1)
        # 中段没有任何旧摘要 → prior is None
        msgs = [
            {"role": "user", "content": "u0"},
            {"role": "user", "content": "m1" * 30},
            {"role": "assistant", "content": "m2" * 30},
            {"role": "user", "content": "u-last"},
        ]
        r = await c.compress(msgs)  # 不抛
        assert r.compressed is True
        summaries = [
            m for m in r.messages
            if str(m.get("content") or "").lstrip().startswith(_SUMMARY_MARKER)
        ]
        assert len(summaries) == 1
        # 无干净 prior 可退 → 反射摘要被保留(降级,但不丢消息)
        assert "不要杜撰" in summaries[0]["content"]


# ───────────────────── WI-4a always-on 真注入 (跑 loop 断言) ─────────────────────
class TestWI4aAlwaysOnInjection:
    class _NullTools:
        def schemas(self, enabled_toolsets=None):
            return []

        async def execute_tool(self, name, args, task_id):
            return '{"ok": true}'

    class _EndTurnLLM:
        def __init__(self):
            self.seen: list[list[dict]] = []

        async def chat_with_fallback(self, messages, *, tools=None, model=None, **kw):
            self.seen.append(list(messages))
            from llm.types import ChatResponse
            return ChatResponse(
                content="done", stop_reason="end_turn", tool_calls=[],
                usage={"input_tokens": 5, "output_tokens": 3},
            )

    @pytest.mark.asyncio
    async def test_always_on_anchor_injected_with_subgoal(self):
        """有 goal + 有 pending → 循环前注入恰 1 条 [目标锚定],含目标 + [当前子目标]。"""
        from agent.agent_loop import AgentLoop
        from deskpet.agent.goal_store import SessionGoalStore

        store = SessionGoalStore()
        g = store.set("s-anchor", "整理季度复盘")
        g.subgoals = ["收集三个项目数据", "汇总成稿"]

        llm = self._EndTurnLLM()
        loop = AgentLoop(
            llm_registry=llm, tool_registry=self._NullTools(),
            session_goal_store=store,
        )
        async for _ in loop.run([{"role": "user", "content": "开始"}], session_id="s-anchor"):
            pass

        assert llm.seen, "LLM 未被调用"
        anchors = [
            m for m in llm.seen[0]
            if m.get("role") == "system" and str(m.get("content")).startswith("[目标锚定]")
        ]
        assert len(anchors) == 1
        content = anchors[0]["content"]
        assert "整理季度复盘" in content
        assert "[当前子目标] 收集三个项目数据" in content  # 取 pending[0]

    @pytest.mark.asyncio
    async def test_always_on_anchor_no_subgoal_line_when_no_pending(self):
        """有 goal 但无 pending → 注 [目标锚定] 但不带 [当前子目标] 行。"""
        from agent.agent_loop import AgentLoop
        from deskpet.agent.goal_store import SessionGoalStore

        store = SessionGoalStore()
        store.set("s-nosub", "随便写写")  # 无 subgoals

        llm = self._EndTurnLLM()
        loop = AgentLoop(
            llm_registry=llm, tool_registry=self._NullTools(),
            session_goal_store=store,
        )
        async for _ in loop.run([{"role": "user", "content": "x"}], session_id="s-nosub"):
            pass

        anchors = [
            m for m in llm.seen[0]
            if m.get("role") == "system" and str(m.get("content")).startswith("[目标锚定]")
        ]
        assert len(anchors) == 1
        assert "[当前子目标]" not in anchors[0]["content"]

    @pytest.mark.asyncio
    async def test_no_anchor_when_no_active_goal(self):
        """store 存在但本 session 无 goal → 不注入(BC)。"""
        from agent.agent_loop import AgentLoop
        from deskpet.agent.goal_store import SessionGoalStore

        store = SessionGoalStore()  # 空
        llm = self._EndTurnLLM()
        loop = AgentLoop(
            llm_registry=llm, tool_registry=self._NullTools(),
            session_goal_store=store,
        )
        async for _ in loop.run([{"role": "user", "content": "x"}], session_id="s-empty"):
            pass

        for call in llm.seen:
            assert not any(
                str(m.get("content") or "").startswith("[目标锚定]") for m in call
            )


# ───────────────────── WI-4b pre-flush 缺口 (safe-fail / 无 goal / 都无) ─────────────────────
class TestWI4bPreflushEdgeGaps:
    class _Tools:
        def schemas(self, enabled_toolsets=None):
            return []

        async def execute_tool(self, name, args, task_id):
            return '{"ok": true}'

    class _LLM:
        async def chat_with_fallback(self, messages, *, tools=None, model=None, **kw):
            from llm.types import ChatResponse
            return ChatResponse(
                content="done", stop_reason="end_turn", tool_calls=[],
                usage={"input_tokens": 5, "output_tokens": 3},
            )

    class _AlwaysCompress:
        def should_compress(self, n):
            return True

        async def compress(self, messages, *, goal_text=None, pending_tasks=None):
            from deskpet.agent.context_compressor import CompressionResult
            return CompressionResult(messages=list(messages), compressed=False)

    @pytest.mark.asyncio
    async def test_preflush_does_not_block_compression_when_append_raises(self):
        """file_memory.append 抛异常 → pre-flush safe-fail,压缩照常进行,run 不崩。"""
        from agent.agent_loop import AgentLoop

        class _RaisingFM:
            def __init__(self): self.calls = 0
            async def append(self, target, content, salience=0.5):
                self.calls += 1
                raise RuntimeError("disk full")

        class _GoalStore:
            def get_goal_text(self, sid):
                return "有目标"

        compressor = self._AlwaysCompress()
        fm = _RaisingFM()
        loop = AgentLoop(
            llm_registry=self._LLM(), tool_registry=self._Tools(),
            compressor=compressor, session_goal_store=_GoalStore(), file_memory=fm,
        )
        events = []
        async for ev in loop.run([{"role": "user", "content": "请整理"}], session_id="s-raise"):
            events.append(ev)
        # append 被尝试(safe-fail 吞异常),loop 正常产出 final,无 error event
        assert fm.calls >= 1
        assert any(getattr(e, "type", "") == "final" for e in events)
        assert not any(getattr(e, "type", "") == "error" for e in events)

    @pytest.mark.asyncio
    async def test_preflush_when_no_goal_but_recent_user(self):
        """无 goal 但有最近 user → 仍 flush(_flush_parts 含'最近请求')。"""
        from agent.agent_loop import AgentLoop

        class _RecFM:
            def __init__(self): self.calls = []
            async def append(self, target, content, salience=0.5):
                self.calls.append(content)

        class _NoGoalStore:
            def get_goal_text(self, sid):
                return None  # 无 goal

        fm = _RecFM()
        loop = AgentLoop(
            llm_registry=self._LLM(), tool_registry=self._Tools(),
            compressor=self._AlwaysCompress(), session_goal_store=_NoGoalStore(),
            file_memory=fm,
        )
        async for _ in loop.run([{"role": "user", "content": "帮我查天气"}], session_id="s-nogoal"):
            pass
        assert len(fm.calls) == 1
        assert "最近请求" in fm.calls[0]
        assert "帮我查天气" in fm.calls[0]
        assert "目标:" not in fm.calls[0]  # 无 goal → 不含目标段

    @pytest.mark.asyncio
    async def test_no_preflush_when_no_goal_no_user(self):
        """既无 goal 又无 user 消息(只 system) → _flush_parts 空 → 不 flush。"""
        from agent.agent_loop import AgentLoop

        class _RecFM:
            def __init__(self): self.calls = []
            async def append(self, target, content, salience=0.5):
                self.calls.append(content)

        class _NoGoalStore:
            def get_goal_text(self, sid):
                return None

        fm = _RecFM()
        loop = AgentLoop(
            llm_registry=self._LLM(), tool_registry=self._Tools(),
            compressor=self._AlwaysCompress(), session_goal_store=_NoGoalStore(),
            file_memory=fm,
        )
        # 只有 system 消息,无 user
        async for _ in loop.run([{"role": "system", "content": "persona"}], session_id="s-bare"):
            pass
        assert fm.calls == []  # goal+user 都无 → 不 flush


# ───────────────────── WI-1B-2 压缩可观测 (ctx_observability flag) ─────────────────────
class TestWI1B2CtxObservability:
    """flag OFF = 字节级 BC (不 emit metrics / 不 yield ContextCompactedEvent);
    flag ON = 压缩命中后 yield 一条 ContextCompactedEvent(字段=reduction/in/out)
    + record 一条 metrics。对照 plan WI-1B-2 可证伪断言。"""

    class _Tools:
        def schemas(self, enabled_toolsets=None):
            return []

        async def execute_tool(self, name, args, task_id):
            return '{"ok": true}'

    class _LLM:
        async def chat_with_fallback(self, messages, *, tools=None, model=None, **kw):
            from llm.types import ChatResponse
            return ChatResponse(
                content="done", stop_reason="end_turn", tool_calls=[],
                usage={"input_tokens": 5, "output_tokens": 3},
            )

    class _CompressHit:
        """压缩命中 — 返回 compressed=True + 真 token 字段，触发可观测分支。"""
        model = "claude-haiku-4-5"

        def should_compress(self, n):
            return True

        async def compress(self, messages, *, goal_text=None, pending_tasks=None):
            from deskpet.agent.context_compressor import CompressionResult
            return CompressionResult(
                messages=list(messages),
                compressed=True,
                input_tokens=1000,
                output_tokens=400,
                reduction_ratio=0.6,
            )

    @pytest.mark.asyncio
    async def test_observability_off_no_extra_event(self, monkeypatch):
        """flag OFF (默认): 压缩命中也不 yield ContextCompactedEvent、不调 metrics。"""
        from agent.agent_loop import AgentLoop
        import observability.metrics_sink as _ms

        recorded: list[tuple] = []
        monkeypatch.setattr(
            _ms, "record",
            lambda event, detail=None: recorded.append((event, detail)) or True,
        )

        loop = AgentLoop(
            llm_registry=self._LLM(),
            tool_registry=self._Tools(),
            compressor=self._CompressHit(),
            # ctx_observability 不传 → 默认 False (BC)
        )
        events = []
        async for ev in loop.run(
            [{"role": "user", "content": "请整理"}], session_id="s-off"
        ):
            events.append(ev)

        # OFF=BC: 无 ContextCompactedEvent
        assert not any(
            getattr(e, "type", "") == "context_compacted" for e in events
        ), "flag OFF 不应 yield ContextCompactedEvent"
        # OFF=BC: 无 context_compacted metrics 调用
        assert not any(
            ev == "context_compacted" for ev, _ in recorded
        ), "flag OFF 不应 record context_compacted metrics"
        # 压缩本身仍发生 → loop 正常结束
        assert any(getattr(e, "type", "") == "final" for e in events)

    @pytest.mark.asyncio
    async def test_observability_on_emits_event(self, monkeypatch):
        """flag ON: 压缩命中 yield ContextCompactedEvent(字段=reduction/in/out) +
        record 一条 context_compacted metrics(ratio/model/count)。"""
        from agent.agent_loop import AgentLoop, ContextCompactedEvent
        import observability.metrics_sink as _ms

        recorded: list[tuple] = []
        monkeypatch.setattr(
            _ms, "record",
            lambda event, detail=None: recorded.append((event, detail)) or True,
        )

        loop = AgentLoop(
            llm_registry=self._LLM(),
            tool_registry=self._Tools(),
            compressor=self._CompressHit(),
            ctx_observability=True,  # flag ON
        )
        events = []
        async for ev in loop.run(
            [{"role": "user", "content": "请整理"}], session_id="s-on"
        ):
            events.append(ev)

        cc_events = [e for e in events if isinstance(e, ContextCompactedEvent)]
        assert len(cc_events) == 1, "flag ON 压缩命中应 yield 恰一条 ContextCompactedEvent"
        cc = cc_events[0]
        assert cc.tokens_in == 1000
        assert cc.tokens_out == 400
        assert cc.reduction == pytest.approx(0.6)
        assert cc.model == "claude-haiku-4-5"
        assert cc.type == "context_compacted"

        # metrics: 恰一条 context_compacted, detail = ratio/model/count
        cc_metrics = [d for ev, d in recorded if ev == "context_compacted"]
        assert len(cc_metrics) == 1, "flag ON 应 record 恰一条 context_compacted metrics"
        detail = cc_metrics[0]
        assert detail["ratio"] == pytest.approx(0.6)
        assert detail["model"] == "claude-haiku-4-5"
        assert detail["count"] == 600  # 1000 - 400

    @pytest.mark.asyncio
    async def test_observability_on_metrics_event_whitelisted(self):
        """context_compacted 在 metrics_sink VALID_EVENTS 白名单内 → record 真落盘
        (否则被丢)。detail keys ratio/model/count 也在白名单内。"""
        from observability.metrics_sink import (
            VALID_EVENTS,
            sanitize_detail,
        )

        assert "context_compacted" in VALID_EVENTS
        out = sanitize_detail({"ratio": 0.6, "model": "claude-haiku-4-5", "count": 600})
        assert out == {"ratio": 0.6, "model": "claude-haiku-4-5", "count": 600}
