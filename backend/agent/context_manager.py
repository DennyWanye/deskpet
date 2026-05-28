# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""P6 Phase 2 — ContextManager facade.

Why
---
Before P6 the chat handler + AgentLoop reached directly into three
independent helpers — B1 ``tool_result_truncator``, B2 ``history_compactor``,
B3 ``token_budget`` — and each call site duplicated wiring (thresholds,
provider plumbing, ref-store sharing).  This module unifies them into a
single facade so the agent loop has **one** dependency for "context
optimization":

  * Truncate noisy tool results into head + tail + ref_id marker (B1)
  * Compact old turns into a summary system message when history grows
    too large (B2)
  * Estimate token usage and emit WARN / BLOCK verdicts (B3)

It also carries the **G1 fix** at the type level: the
``skip_truncation_for_tools`` set means the ``fetch_tool_result`` tool
itself cannot be truncated (the original infinite-loop root cause was
fetch returning a body that itself got truncated, so the LLM kept
fetching refs returned from inside truncated fetch responses).

Design choices
--------------
* **Facade, not orchestrator.**  This class delegates everything to
  B1/B2/B3 — it does not implement truncation/compaction itself.  That
  keeps existing test coverage in those modules intact and lets us
  evolve internals independently.
* **Global ref store reuse.**  ``ContextManager.ref_store`` is the same
  ``get_global_ref_store()`` singleton the future ``fetch_tool_result``
  tool reads from.  Identity check (``is``) is required for the G1
  round-trip to work.
* **Async only where needed.**  ``check_budget`` / ``record_tool_result``
  are sync (pure-CPU paths).  ``maybe_compact`` /
  ``prepare_chat_messages`` are async because the summarize step is an
  LLM call.
* **No provider import.**  ``llm_for_summarize`` is passed in by the
  caller.  This keeps the unit tests free of provider/transport state.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional, Tuple

from agent.history_compactor import compact_messages, should_compact
from agent.token_budget import BudgetCheckResult, check_budget
from agent.tool_result_truncator import (
    ToolResultRefStore,
    get_global_ref_store,
    maybe_truncate_tool_result,
)
from llm.model_info import ModelContextInfo, resolve as resolve_model_info

logger = logging.getLogger(__name__)


# Type alias matching what history_compactor expects.
SummarizeFn = Callable[[str], Awaitable[str]]


# ─── Phase 1.1.3 Strangler-Fig 回退闸 ───────────────────────────
#
# [context.manager].v2_enabled=false（config.toml 或 env DESKPET_CTX_V2=0）
# 时 ContextConfig 退回 2026-05-15 stop-gap 的绝对值常量路径；默认 True
# 走 per-model 比例（design.md D2）。main.py 启动时读 config 并把布尔传进
# ContextManager；这里的 env 兜底只是给单测/快速回滚用。
def _v2_default_enabled() -> bool:
    raw = os.environ.get("DESKPET_CTX_V2")
    if raw is None:
        return True
    return raw.strip().lower() not in {"0", "false", "no", ""}


# 2026-05-15 stop-gap 绝对值（v2_enabled=false 的 legacy 路径沿用这些）。
# 调研依据：Claude Code compact ~83% / Cline 80% / DeepSeek-TUI 75% /
# Codex effective 95%。v1 是按 800K context 同比例 ×4 放大的写死值。
_LEGACY_TOOL_RESULT_THRESHOLD = 16_000
_LEGACY_TOOL_RESULT_HEAD = 6_000
_LEGACY_TOOL_RESULT_TAIL = 2_000
_LEGACY_COMPACT_MESSAGE_THRESHOLD = 80
_LEGACY_COMPACT_CHAR_THRESHOLD = 300_000
_LEGACY_COMPACT_KEEP_RECENT = 12
# v1 没有 per-model window，compact_at_tokens 用 800K×0.95 的 stop-gap 近似
# （config.toml [agent].context_window_tokens=800000 配套）。
_LEGACY_CONTEXT_WINDOW = 800_000
_LEGACY_EFFECTIVE_PCT = 0.95
_LEGACY_COMPACT_AT_PCT = 0.95


@dataclass
class ContextConfig:
    """Unified configuration for B1 + B2 + B3.

    Phase 1.1.3（design.md D2）：阈值从写死绝对值改为按 resolved model
    window 比例计算的 ``@property``。

    - **v2（默认）**：注入 ``model_info: ModelContextInfo``（由
      ``llm.model_info.resolve()`` 三层解析得到）。所有阈值随 model 窗口
      自动伸缩，切模型零配置编辑。不注入则懒解析 ``_default``。
    - **v1（``v2_enabled=False``）**：Strangler-Fig 回退闸，退回 2026-05-15
      stop-gap 的绝对值常量；忽略 per-model map。

    ``tool_result_head/tail``、``compact_keep_recent``、
    ``skip_truncation_for_tools``、``budget_warn_pct/block_pct`` 两路共用，
    仍是普通字段（AgentLoop 可单独 override 任意一个）。
    """

    # 注入的 per-model 画像。None + v2 → 懒解析 _default；v1 下忽略。
    model_info: Optional[ModelContextInfo] = None
    v2_enabled: bool = field(default_factory=_v2_default_enabled)

    # B1 head/tail：两路共用的稳定切片量（不随 window 变——这是"保留多少
    # 给 LLM 看结构"的经验值，window 大不代表要 head 更多噪音）。
    tool_result_head: int = _LEGACY_TOOL_RESULT_HEAD
    tool_result_tail: int = _LEGACY_TOOL_RESULT_TAIL
    # B1 self-awareness — the G1 fix lives here.  Adding a new "must keep
    # full body" tool is a one-set-edit, not a code change.
    skip_truncation_for_tools: set[str] = field(
        default_factory=lambda: {"fetch_tool_result"},
    )

    # Phase 1.2 File-read dedup（design.md D3）：读取类工具白名单。
    # 同 path 被这些工具重复读 → 历史里旧 tool_result 原地替换成 superseded
    # marker（只动 read-class，不动 write/edit/run_shell）。可一处加白名单。
    read_class_tools: set[str] = field(
        default_factory=lambda: {
            "read_file",
            "mcp_filesystem_read_text_file",
        },
    )

    # B2 compaction message count 阈值（消息条数维度，与 char 维度并列；
    # 仍是普通字段，per-model 比例只作用在 token/char 维度）。
    compact_keep_recent: int = _LEGACY_COMPACT_KEEP_RECENT

    # B3 budget 比例 — 0.80/0.95 已和业界一致，两路共用。分母在 v2 下是
    # model_info.context_window * effective_pct（见 effective_window_tokens）。
    budget_warn_pct: float = 0.80
    budget_block_pct: float = 0.95

    # ─── 内部：解析出当前生效的 ModelContextInfo ───
    def _resolved_model_info(self) -> ModelContextInfo:
        """v2 下返回注入的 model_info；未注入则懒解析 _default。"""
        if self.model_info is not None:
            return self.model_info
        # 未注入：保守按 _default（unknown model）。resolve 落一行日志。
        return resolve_model_info("_default", project_root=None)

    # ─── per-model 比例阈值（D2）/ v1 legacy 绝对值 ───

    @property
    def effective_window_tokens(self) -> int:
        """有效窗口 = context_window * effective_pct（budget 分母用）。"""
        if not self.v2_enabled:
            return int(_LEGACY_CONTEXT_WINDOW * _LEGACY_EFFECTIVE_PCT)
        mi = self._resolved_model_info()
        return int(mi.context_window * mi.effective_pct)

    @property
    def compact_at_tokens(self) -> int:
        """触发 compaction / cycle restart 的水位线（token）。

        v2: window * compact_at_pct（1M×0.75=750_000；200K×0.83=166_000）。
        v1: 800K × 0.95 的 stop-gap 近似。
        """
        if not self.v2_enabled:
            return int(_LEGACY_CONTEXT_WINDOW * _LEGACY_COMPACT_AT_PCT)
        mi = self._resolved_model_info()
        return int(mi.context_window * mi.compact_at_pct)

    @property
    def tool_result_threshold(self) -> int:
        """B1 单条 tool_result 超过此 char 数才切。

        v2: max(8_000, window // 25)（1M→40K；200K/32K→8K floor）。
        v1: 写死 16_000。
        """
        if not self.v2_enabled:
            return _LEGACY_TOOL_RESULT_THRESHOLD
        return max(8_000, self._resolved_model_info().context_window // 25)

    @property
    def compact_message_threshold(self) -> int:
        """B2 历史压缩的消息条数阈值。

        v2: window 越大允许越多 turn（window // 10_000，下限 20）。
        v1: 写死 80。
        """
        if not self.v2_enabled:
            return _LEGACY_COMPACT_MESSAGE_THRESHOLD
        return max(20, self._resolved_model_info().context_window // 10_000)

    @property
    def compact_char_threshold(self) -> int:
        """B2 历史压缩的字符总量阈值。

        v2: compact_at_tokens * ~3.5 char/token 近似（与 B3 token 维度
        同步触发，避免 char 维度提前/滞后于 token 水位）。
        v1: 写死 300_000。
        """
        if not self.v2_enabled:
            return _LEGACY_COMPACT_CHAR_THRESHOLD
        return int(self.compact_at_tokens * 3.5)


class ContextManager:
    """Single entry point for all context-optimization concerns.

    Typical use::

        ctx = ContextManager()

        # chat handler entry: optional preflight compaction
        msgs = await ctx.prepare_chat_messages(
            msgs, model=provider.model, llm_for_summarize=provider,
        )

        # AgentLoop per-iteration: budget guard
        budget = ctx.check_budget(msgs, model=provider.model)
        if budget.verdict is BudgetCheck.BLOCK:
            yield ErrorEvent(reason="context_budget_block", detail=budget.advice)
            return

        # After every tool dispatch:
        content, ref_id = ctx.record_tool_result(
            tool_name=tc.name, result=result_str,
        )
        working_messages.append({..., "content": content})
    """

    def __init__(
        self,
        config: Optional[ContextConfig] = None,
        summarize_fn: Optional[SummarizeFn] = None,
        ref_store: Optional[ToolResultRefStore] = None,
    ) -> None:
        self.config = config or ContextConfig()
        self.summarize_fn = summarize_fn
        # ``ref_store`` defaults to the module-level singleton — same one
        # the ``fetch_tool_result`` tool reads.  Pass a custom store only
        # in tests that need isolation.
        self.ref_store = ref_store if ref_store is not None else get_global_ref_store()
        # Phase 1.2 File-read dedup（D3）：normalized_path → 该 path 最近一次
        # 出现的 message 下标。同 path 再次被 read-class 工具读取时，把这个
        # 旧下标处的 tool_result content 原地替换成 superseded marker。
        self._read_path_seen: dict[str, int] = {}

    # ─────────────── Phase 1.1.4 per-session 工厂 ───────────────

    @classmethod
    def for_session(
        cls,
        *,
        model: str,
        project_root: Optional[Path] = None,
        v2_enabled: bool = True,
        summarize_fn: Optional[SummarizeFn] = None,
        ref_store: Optional[ToolResultRefStore] = None,
    ) -> "ContextManager":
        """按 model + project_root 三层 resolve 注入 ModelContextInfo。

        集成点（design.md D1/D2）：
          - **code mode**：调用方传 ``CodeModeManager.get(sid).project_root``
            → 项目层 ``<root>/.deskpet/context.toml`` 覆盖生效
          - **非 code mode**：``project_root=None`` → 只走 builtin + global 两层
          - ``v2_enabled=False``：Strangler-Fig 回退闸——忽略 per-model map，
            ContextConfig 退回 2026-05-15 stop-gap 绝对值

        ``resolve()`` 内部已落 ``model_context_resolved`` INFO 日志
        （task 1.1.5），这里不重复打。
        """
        if v2_enabled:
            mi = resolve_model_info(model, project_root=project_root)
            config = ContextConfig(model_info=mi, v2_enabled=True)
        else:
            # 回退闸：不解析 per-model，ContextConfig 走 legacy 绝对值。
            config = ContextConfig(v2_enabled=False)
        return cls(
            config=config,
            summarize_fn=summarize_fn,
            ref_store=ref_store,
        )

    # ─────────────────────── B3 token budget ───────────────────────

    def check_budget(
        self, messages: list[dict[str, Any]], *, model: str,
    ) -> BudgetCheckResult:
        """Pre-call token budget check delegating to B3.

        Returns the full ``BudgetCheckResult`` (verdict + estimated
        tokens + window + ratio + advice string).  Caller decides what
        to do with WARN/BLOCK.
        """
        # Phase 1.1 followup: pass the already-resolved per-model window
        # (config.model_info carries the full builtin/global/project
        # 3-layer resolution). v2 → authoritative; v1 rollback → pass
        # None so token_budget falls back to its legacy name table.
        _window = (
            self.config.model_info.context_window
            if self.config.v2_enabled and self.config.model_info is not None
            else None
        )
        return check_budget(
            messages,
            model=model,
            warn_pct=self.config.budget_warn_pct,
            block_pct=self.config.budget_block_pct,
            context_window=_window,
        )

    # ───────────────────── B2 history compaction ─────────────────────

    async def maybe_compact(
        self,
        messages: list[dict[str, Any]],
        *,
        llm_for_summarize: Any,
    ) -> list[dict[str, Any]]:
        """Preflight compaction (Hermes-style, no waiting for overflow).

        Skips work if the history is small (below count + char
        thresholds).  When it does compact, asks ``llm_for_summarize``
        for a short Chinese summary of the middle range; on any
        summarize failure, returns the original list (better to keep
        a long context than silently lose history).
        """
        if not should_compact(
            messages,
            message_threshold=self.config.compact_message_threshold,
            char_threshold=self.config.compact_char_threshold,
        ):
            return list(messages)

        async def _summarize(text: str) -> str:
            r = await llm_for_summarize.chat_with_tools(
                [
                    {
                        "role": "system",
                        "content": (
                            "Compress the following conversation into a concise "
                            "Chinese summary capturing: completed steps, key "
                            "decisions, tool results' outcomes, current state. "
                            "≤ 600 chars."
                        ),
                    },
                    {"role": "user", "content": text[:50_000]},
                ],
                tools=None,
                max_tokens=800,
                temperature=0.1,
            )
            return r.get("content", "") if isinstance(r, dict) else ""

        return await compact_messages(
            messages,
            summarize_fn=_summarize,
            message_threshold=self.config.compact_message_threshold,
            char_threshold=self.config.compact_char_threshold,
            keep_recent=self.config.compact_keep_recent,
        )

    # ──────────── B1 + G1 unified tool result handling ────────────

    def record_tool_result(
        self,
        *,
        tool_name: str,
        result: str,
    ) -> Tuple[str, Optional[str]]:
        """Return ``(content_for_history, ref_id_or_None)``.

        For tools listed in ``skip_truncation_for_tools`` (G1 fix) the
        result is passed through verbatim and no ref is created.  For
        everything else, delegates to B1 ``maybe_truncate_tool_result``.
        """
        if tool_name in self.config.skip_truncation_for_tools:
            # Coerce non-string results to string for caller convenience.
            content = result if isinstance(result, str) else str(result)
            return (content, None)

        return maybe_truncate_tool_result(
            result,
            store=self.ref_store,
            threshold=self.config.tool_result_threshold,
            head_chars=self.config.tool_result_head,
            tail_chars=self.config.tool_result_tail,
        )

    # ──────────── Phase 1.2 File-read dedup（D3）────────────

    @staticmethod
    def _normalize_read_path(raw: Any) -> Optional[str]:
        """把工具 args 里的 path 规范化成稳定 key。

        - 正反斜杠统一、相对段折叠：``Path(p).resolve()``
        - Windows 盘符 / 大小写不敏感文件系统：``casefold()``
          （``G:\\proj\\App.jsx`` 与 ``g:/proj/App.jsx`` → 同一 key）
        - 任意异常（非字符串 / 畸形路径）→ None（调用方据此跳过，绝不抛）
        """
        if not isinstance(raw, str) or not raw.strip():
            return None
        try:
            resolved = Path(raw).resolve()
            return str(resolved).casefold()
        except (OSError, ValueError, RuntimeError):
            return None

    def dedup_file_reads(
        self,
        messages: list[dict[str, Any]],
        *,
        tool_name: str,
        tool_args: dict[str, Any],
        new_index: int,
        iteration: int,
    ) -> int:
        """同 path 重复读 → 历史里旧 tool_result body 原地替换成 superseded
        marker（design.md D3）。

        调用时机：agent loop 把这条 tool message append 进 ``messages``
        **之后**，传入它的下标 ``new_index``。

        语义（spec long-run-context）：
          - 仅对 ``config.read_class_tools`` 生效（read_file /
            mcp_filesystem_read_text_file / 可配白名单）；write/edit/
            run_shell 一律跳过
          - path 经 :meth:`_normalize_read_path` 规范化（Windows 大小写 /
            正反斜杠归一）
          - **不删消息**，只把旧下标那条的 ``content`` 换成 marker——保持
            message 数组长度 + 下标稳定，避免 tool_call/tool_result 配对
            错位
          - dict 记录该 path 最近出现的下标，下一次读再 supersede 它

        返回本次被 supersede 的旧条数（0 或 1，便于调用方落日志/统计）。
        """
        if tool_name not in self.config.read_class_tools:
            return 0
        norm = self._normalize_read_path((tool_args or {}).get("path"))
        if norm is None:
            return 0

        superseded = 0
        prev_index = self._read_path_seen.get(norm)
        if (
            prev_index is not None
            and prev_index != new_index
            and 0 <= prev_index < len(messages)
        ):
            old_msg = messages[prev_index]
            # 只换 content；role/tool_call_id/name 等配对字段一律不动。
            if isinstance(old_msg, dict) and "content" in old_msg:
                disp = (tool_args or {}).get("path")
                old_msg["content"] = (
                    f"<file {disp} was re-read at iteration {iteration}; "
                    "superseded — see the later read>"
                )
                superseded = 1
                logger.info(
                    "p4_file_read_superseded path=%s old_idx=%d "
                    "new_idx=%d iter=%d",
                    str(disp)[:200],
                    prev_index,
                    new_index,
                    iteration,
                )

        # 记录/更新该 path 的最新下标。
        self._read_path_seen[norm] = new_index
        return superseded

    # ─────────────────── High-level prepare ───────────────────

    async def prepare_chat_messages(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str,  # noqa: ARG002 — reserved for future per-model strategy
        llm_for_summarize: Optional[Any] = None,
    ) -> list[dict[str, Any]]:
        """Chat handler entry point.

        Currently runs preflight compaction when a summarize LLM is
        supplied.  Budget BLOCK is intentionally NOT enforced here —
        that's an ErrorEvent decision the AgentLoop owns, not a transform
        ContextManager applies silently.
        """
        if llm_for_summarize is None:
            return list(messages)

        return await self.maybe_compact(
            messages, llm_for_summarize=llm_for_summarize,
        )
