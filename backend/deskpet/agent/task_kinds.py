# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""事务分型（task-kind）注册表 — 子代理并发驱动的「多种事务」路由地基。

plan: plans/2026-06-21-subagent-concurrency-driver/ WI-0.1

每个 ``KindProfile`` 把一种「事务」（research / code / doc / web / fileops /
general）映射到：工具子集 + 迭代上限 + system framing + 可选模型覆盖 + 并发
lane cap。``agent_parallel`` / ``spawn_subagents`` 的每个子任务带一个 ``kind``
字段，由本模块解析成 profile，决定该子代理怎么跑。

设计要点
--------
* **递归守门**：任何 kind 的工具子集都会被剔除 spawn 类工具（``agent`` /
  ``agent_parallel`` / ``spawn_team`` / ``spawn_subagents`` / ``await_subagents``），
  保证子代理不能再 spawn 子代理 → 扁平 fan-out、depth=1。
* **未知 kind 安全回退**：解析不到 → ``general``（只读集），永不抛。
* **配置覆盖**：``load_kind_overrides`` 从 ``config.raw['agent']['subagent_kinds']``
  合并覆盖；单条坏覆盖逐条 try/except 跳过，不污染整表（防 b05823b 式
  config 静默失效）。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, replace
from typing import Any

# ── WI-OC-1：子代理 spawn 显式 depth 计数 + 上界 ──────────────────────────
# 现状（基线）：递归守门只靠 `_strip_forbidden` 剔 spawn 类工具 → 子代理拿不到
# spawn 工具 → 隐式 depth=1，**无显式数值/上界**。本块加显式 depth 计数 + 上界，
# 作为 *第二道* 防线（defence-in-depth，与 `_strip_forbidden` 并存、不取代）。
#
# 设计：
#   * depth 通过进程级 env ``DESKPET_SUBAGENT_DEPTH`` 沿 spawn 边界传播（子
#     AgentLoop 在同进程内跑，但概念上「更深一层」）。父 spawn 时把
#     ``depth+1`` 注入子 runner 的环境。
#   * flag ``agent.subagent_explicit_depth`` 默认 **False** → 完全不查 depth，
#     仍只靠 strip（strip 已保证 depth=1，故 OFF 行为字节不变 = BC）。
#   * flag ON 才启用上界检查 + 允许「显式 depth>1 受控嵌套」（未来扩展位）。
#   * 硬上限 ``HARD_MAX_SPAWN_DEPTH=3``（对齐 openhuman）—— 即使配置
#     ``max_spawn_depth`` 调更大也被 clamp，不可超过。
_DEPTH_ENV = "DESKPET_SUBAGENT_DEPTH"
HARD_MAX_SPAWN_DEPTH = 3  # 硬上限（对齐 openhuman），任何配置都不能超过
_DEFAULT_MAX_SPAWN_DEPTH = 1  # 默认上界 = 扁平 fan-out（与现状隐式 depth=1 等价）


class SpawnDepthExceeded(RuntimeError):
    """spawn 深度超过允许上界 —— 与「剥 spawn 工具」同风格的拒绝信号。

    仅当 flag ``agent.subagent_explicit_depth`` 为 True 时才可能抛出。
    携带 ``depth`` / ``limit`` 供上层组装 forbidden 拒绝消息。
    """

    def __init__(self, depth: int, limit: int) -> None:
        self.depth = depth
        self.limit = limit
        super().__init__(
            f"子代理 spawn 深度 {depth} 超过上界 {limit}（递归守门：拒绝再 spawn）"
        )


def current_spawn_depth() -> int:
    """读当前进程的 spawn 深度（来自 env ``DESKPET_SUBAGENT_DEPTH``）。

    顶层父代理无此 env → 0。解析失败 → 0（永不抛，安全回退）。
    """
    try:
        return max(0, int(os.environ.get(_DEPTH_ENV, "0")))
    except (TypeError, ValueError):
        return 0


def depth_gate_enabled(raw_agent_cfg: dict[str, Any] | None) -> bool:
    """flag ``agent.subagent_explicit_depth`` —— 默认 **False**（BC）。

    缺省 / 类型错 → False（OFF），不查 depth、仍靠 strip。
    """
    if not isinstance(raw_agent_cfg, dict):
        return False
    return bool(raw_agent_cfg.get("subagent_explicit_depth", False))


def resolve_max_spawn_depth(raw_agent_cfg: dict[str, Any] | None) -> int:
    """配置上界 ``agent.max_spawn_depth``（默认 1），clamp 到硬上限 3。

    缺省 / 解析失败 → 默认 1。<1 → 1。>HARD_MAX → HARD_MAX（不可逾越）。
    """
    raw_val: Any = _DEFAULT_MAX_SPAWN_DEPTH
    if isinstance(raw_agent_cfg, dict):
        raw_val = raw_agent_cfg.get("max_spawn_depth", _DEFAULT_MAX_SPAWN_DEPTH)
    try:
        val = int(raw_val)
    except (TypeError, ValueError):
        val = _DEFAULT_MAX_SPAWN_DEPTH
    if val < 1:
        val = 1
    return min(val, HARD_MAX_SPAWN_DEPTH)


def check_spawn_depth(
    raw_agent_cfg: dict[str, Any] | None,
    *,
    current_depth: int | None = None,
) -> None:
    """在每个 spawn 入口调用：flag ON 且子代理将达/超上界 → 抛 SpawnDepthExceeded。

    flag OFF → no-op（直接返回，BC：行为字节不变，仍靠 strip 守门）。

    上界语义：``current_depth`` 是父代理当前深度（顶层=0）；子代理深度 =
    ``current_depth + 1``；若子深度 > ``max_spawn_depth`` 上界 → 拒绝。
    例：默认 max=1，顶层(0) spawn → 子深度 1 ≤ 1 放行；子代理(1) 再 spawn →
    子深度 2 > 1 拒绝（=现状扁平 fan-out）。硬上限 3 同样不可逾越。
    """
    if not depth_gate_enabled(raw_agent_cfg):
        return
    depth = current_spawn_depth() if current_depth is None else max(0, int(current_depth))
    limit = resolve_max_spawn_depth(raw_agent_cfg)
    child_depth = depth + 1
    if child_depth > limit:
        raise SpawnDepthExceeded(depth=child_depth, limit=limit)


def child_depth_env(current_depth: int | None = None) -> dict[str, str]:
    """构造传给子 runner 的 depth env 增量（``{DESKPET_SUBAGENT_DEPTH: depth+1}``）。

    供 spawn 入口在起子 AgentLoop 前注入，使子代理的 ``current_spawn_depth()``
    比父深一层。纯函数，不改全局 env（调用方决定怎么注入）。
    """
    depth = current_spawn_depth() if current_depth is None else max(0, int(current_depth))
    return {_DEPTH_ENV: str(depth + 1)}


# 递归守门：任何 kind 的工具子集都不得含这些
# （与 agent_parallel._FORBIDDEN_NESTED_TOOLS / teammate_tools.FORBIDDEN_TEAMMATE_TOOLS 对齐）
_FORBIDDEN_IN_KIND = frozenset(
    {
        "agent",
        "agent_parallel",
        "spawn_team",
        "spawn_subagents",
        "await_subagents",
        "deepresearch",
    }
)


@dataclass(frozen=True)
class KindProfile:
    """一种事务类型的子代理画像。"""

    kind: str
    tools: tuple[str, ...]
    max_iterations: int
    framing: str = ""
    model: str | None = None
    lane_concurrency: int = 2


# 工具名全部对真实注册表核实（registration.py / os_tools / ppt_tools /
# research_tools / image_tools / excel_tools / doc_tools）：
#   read_file write_file edit_file glob grep list_directory run_shell
#   web_search web_fetch ppt_create doc_create excel_create deepresearch
#   generate_image skill_invoke
_BUILTIN_KINDS: dict[str, KindProfile] = {
    "general": KindProfile(
        "general",
        ("read_file", "list_directory", "glob", "grep", "web_search"),
        15,
        framing="你是通用只读子代理，专注调查并返回简洁结论。",
        lane_concurrency=2,
    ),
    "research": KindProfile(
        "research",
        ("web_search", "web_fetch", "read_file"),
        12,
        framing="你是调研子代理：检索权威来源、交叉验证、给带依据的结论。",
        lane_concurrency=2,
    ),
    "code": KindProfile(
        "code",
        (
            "read_file",
            "write_file",
            "edit_file",
            "glob",
            "grep",
            "run_shell",
            "list_directory",
        ),
        20,
        framing="你是编码子代理：读现状→改代码→自检（读回/跑测试）。",
        lane_concurrency=2,
    ),
    "fileops": KindProfile(
        "fileops",
        ("read_file", "write_file", "glob", "grep", "list_directory"),
        12,
        framing="你是文件操作子代理：按契约读写文件，不越界。",
        lane_concurrency=3,
    ),
    "doc": KindProfile(
        "doc",
        (
            "ppt_create",
            "doc_create",
            "excel_create",
            "read_file",
            "web_search",
            "generate_image",
        ),
        15,
        framing="你是文档生成子代理：产出 PPT/Word/Excel，必报完整产物路径。",
        lane_concurrency=1,
    ),
    "web": KindProfile(
        "web",
        ("web_search", "web_fetch"),
        8,
        framing="你是联网快查子代理：快速找事实/网址，不深挖。",
        lane_concurrency=3,
    ),
}

_DEFAULT_KIND = "general"


def _strip_forbidden(tools: tuple[str, ...]) -> tuple[str, ...]:
    """剔除递归守门工具（保证子代理不能再 spawn）。"""
    return tuple(t for t in tools if t not in _FORBIDDEN_IN_KIND)


def resolve_kind(
    name: str | None, *, overrides: dict[str, KindProfile] | None = None
) -> KindProfile:
    """名字 → KindProfile。

    * 未知 kind → ``general``（只读安全回退，R5）。
    * 返回的 profile 工具子集已 ``_strip_forbidden``（递归守门 → depth=1）。
    """
    table = overrides or _BUILTIN_KINDS
    key = (name or "").strip().lower()
    prof = (
        table.get(key)
        or table.get(_DEFAULT_KIND)
        or _BUILTIN_KINDS[_DEFAULT_KIND]
    )
    return replace(prof, tools=_strip_forbidden(prof.tools))


def known_kinds(overrides: dict[str, KindProfile] | None = None) -> list[str]:
    """已知 kind 名列表（含 overrides 新增）。"""
    return sorted((overrides or _BUILTIN_KINDS).keys())


def load_kind_overrides(
    raw_agent_cfg: dict[str, Any] | None,
) -> dict[str, KindProfile]:
    """从 ``config.raw['agent']['subagent_kinds']`` 合并覆盖到内置默认。

    缺省 / 格式错 → 返回内置默认（**不抛**，防 config 单例陷阱 R2）。
    单条坏覆盖逐条 try/except 跳过，整表仍可用。
    """
    merged: dict[str, KindProfile] = dict(_BUILTIN_KINDS)
    section: Any = {}
    if isinstance(raw_agent_cfg, dict):
        section = raw_agent_cfg.get("subagent_kinds") or {}
    if not isinstance(section, dict):
        return merged
    for name, spec in section.items():
        if not isinstance(spec, dict):
            continue
        try:
            base = merged.get(name) or _BUILTIN_KINDS[_DEFAULT_KIND]
            tools = spec.get("tools", base.tools)
            if not isinstance(tools, (list, tuple)):
                raise TypeError("tools must be a list")
            merged[name] = replace(
                base,
                kind=name,
                tools=tuple(str(t) for t in tools),
                max_iterations=int(spec.get("max_iterations", base.max_iterations)),
                framing=str(spec.get("framing", base.framing)),
                model=spec.get("model", base.model),
                lane_concurrency=int(
                    spec.get("lane_concurrency", base.lane_concurrency)
                ),
            )
        except Exception:  # noqa: BLE001 — 单条坏覆盖不污染整表
            continue
    return merged


__all__ = [
    "KindProfile",
    "resolve_kind",
    "known_kinds",
    "load_kind_overrides",
    "_FORBIDDEN_IN_KIND",
    "_BUILTIN_KINDS",
    "_DEFAULT_KIND",
    # WI-OC-1：显式 depth 计数 + 上界
    "SpawnDepthExceeded",
    "HARD_MAX_SPAWN_DEPTH",
    "current_spawn_depth",
    "depth_gate_enabled",
    "resolve_max_spawn_depth",
    "check_spawn_depth",
    "child_depth_env",
]
