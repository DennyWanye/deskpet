"""Phase 1.1 — per-model 上下文窗口 + compaction 阈值的内置表 + 三层 override 解析。

Why
---
deskpet 的智能上下文系统设计于 32K–200K context 时代，所有阈值是写死的
绝对值。`deepseek-v4-pro` 现在是 1M context，硬编码导致：拿到 1M 的车按
200K 限速跑（浪费 80% 容量），切到 claude-sonnet（200K）又会爆（全局单
值）。这个模块是新的"上下文预算大脑"——把"每个模型的窗口 + compaction
触发点"做成 per-model 矩阵，并支持用户/项目两级 override。

抄 Codex `codex-rs/models-manager/src/model_info.rs` 的思路：内置 dataclass
表打底，用户/项目 TOML 深合并覆盖（后者只覆盖出现的字段）。

三层解析链（design.md D1）::

    内置 BUILTIN
      ← %APPDATA%/deskpet/model_overrides.toml   (全局用户层)
      ← <project_root>/.deskpet/context.toml      (项目层，仅 code mode)

- `resolve()` 是纯函数、无副作用、可单测（全局层路径由
  `paths.user_data_dir()` 决定，测试用 `DESKPET_USER_DATA_DIR` 钉到 tmp）
- 非 code mode：`project_root=None`，只走前两层
- 缺失 model → 退 `_default` 保守窗口（32K）
- 启动 + 每次 resolve 落 INFO 日志：
  `model_context_resolved model=%s window=%d source=%s`

为什么 TOML 不 JSON：与 config.toml 一致；用户手编友好；项目级
`.deskpet/context.toml` 可进项目 git 让团队共享。

为什么不进 config.toml 的 [agent] 段：config.toml 是 app 级单值，per-model
是矩阵，混在一起会逼用户在 app 配置里写一堆模型。独立文件 + 独立 UI 卡片
更清晰。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Optional

import tomllib

import paths as _paths

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelContextInfo:
    """单个模型的上下文预算画像（不可变值对象）。

    字段语义：
      * ``context_window``     —— 模型名义上下文窗口（token）
      * ``effective_pct``      —— 有效利用率上限（留给输出 + 安全余量），
                                  budget 计算的分母用 window × effective_pct
      * ``compact_at_pct``     —— 触发 compaction / cycle restart 的水位线
                                  （window 比例）。DeepSeek-TUI 论文：context
                                  越大召回越差，所以 1M 模型只用到 0.75。
      * ``recall_sweet_tokens``—— 召回甜点区（retrieval 应该把有效上下文
                                  控制在此线附近，Phase 2+ 用）。
      * ``model``              —— 解析时用户实际请求的 model 名（即使缺失
                                  走 _default，也保留原名便于日志/UI）。
      * ``source``             —— 解析来源链尾：builtin / global / project。
    """

    model: str
    context_window: int
    effective_pct: float
    compact_at_pct: float
    recall_sweet_tokens: int
    source: str = "builtin"


# ─────────────────────────── 内置表（design.md D1）───────────────────────────
#
# recall_sweet_tokens 取值依据：DeepSeek-TUI 引论文 Figure 9——deepseek-v4
# 在 256K 召回 0.76 / 1M 仅 0.59，所以 1M 模型把甜点区压在 ~384K（≈0.38），
# 200K 模型甜点区取 window 一半，32K 小模型甜点区 ≈ window×0.5。
BUILTIN: dict[str, ModelContextInfo] = {
    "deepseek-v4-pro": ModelContextInfo(
        model="deepseek-v4-pro",
        context_window=1_000_000,
        effective_pct=0.95,
        compact_at_pct=0.75,
        recall_sweet_tokens=384_000,
    ),
    "claude-sonnet-4-5": ModelContextInfo(
        model="claude-sonnet-4-5",
        context_window=200_000,
        effective_pct=0.95,
        compact_at_pct=0.83,
        recall_sweet_tokens=100_000,
    ),
    "claude-opus-4-5": ModelContextInfo(
        model="claude-opus-4-5",
        context_window=200_000,
        effective_pct=0.95,
        compact_at_pct=0.83,
        recall_sweet_tokens=100_000,
    ),
    "gpt-5-pro": ModelContextInfo(
        model="gpt-5-pro",
        context_window=1_000_000,
        effective_pct=0.95,
        compact_at_pct=0.80,
        recall_sweet_tokens=384_000,
    ),
    "gemini-2.5-pro": ModelContextInfo(
        model="gemini-2.5-pro",
        context_window=1_000_000,
        effective_pct=0.95,
        compact_at_pct=0.80,
        recall_sweet_tokens=384_000,
    ),
    # 缺失 model 的保守兜底：本地小模型 / 未知 endpoint。32K 是 ollama
    # gemma/qwen 一类常见上限，宁可保守也别假设大窗口爆 context。
    "_default": ModelContextInfo(
        model="_default",
        context_window=32_000,
        effective_pct=0.90,
        compact_at_pct=0.80,
        recall_sweet_tokens=16_000,
    ),
}

# resolve() 允许 override 修改的字段白名单。model/source 是解析过程算出来
# 的，用户不该（也不能）通过 TOML 覆盖。
_OVERRIDABLE_FIELDS = frozenset(
    {
        "context_window",
        "effective_pct",
        "compact_at_pct",
        "recall_sweet_tokens",
    }
)


def _read_toml(path: Path) -> dict[str, Any]:
    """读一个 TOML 文件 → dict。缺文件 / 解析失败 → {}（绝不抛，解析必须健壮）。"""
    try:
        if not path.is_file():
            return {}
        with open(path, "rb") as f:
            return tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        logger.warning(
            "model_info_override_parse_failed path=%s err=%s",
            path,
            str(exc)[:200],
        )
        return {}


def load_global_overrides() -> dict[str, Any]:
    """读全局用户层 ``%APPDATA%/deskpet/model_overrides.toml``。

    路径由 `paths.user_data_dir()` 决定（portable / classic / 测试 env
    override 都自动兼容）。缺文件返回 ``{}``。
    """
    return _read_toml(_paths.user_data_dir() / "model_overrides.toml")


def load_project_overrides(project_root: Optional[Path]) -> dict[str, Any]:
    """读项目层 ``<project_root>/.deskpet/context.toml``。

    ``project_root=None``（非 code mode）→ ``{}``，项目层被跳过。
    """
    if project_root is None:
        return {}
    return _read_toml(Path(project_root) / ".deskpet" / "context.toml")


def _model_section(overrides: dict[str, Any], model: str) -> dict[str, Any]:
    """从一个 override dict 里抽出 ``[models."<model>"]`` 段（缺失 → {}）。"""
    models = overrides.get("models")
    if not isinstance(models, dict):
        return {}
    section = models.get(model)
    return section if isinstance(section, dict) else {}


def _apply_override(info: ModelContextInfo, section: dict[str, Any]) -> ModelContextInfo:
    """把一个 model 段的字段深合并进 info —— 只覆盖出现且在白名单内的字段。"""
    patch: dict[str, Any] = {}
    for key, value in section.items():
        if key not in _OVERRIDABLE_FIELDS:
            logger.warning(
                "model_info_override_ignored_unknown_field model=%s field=%s",
                info.model,
                key,
            )
            continue
        patch[key] = value
    if not patch:
        return info
    return replace(info, **patch)


def resolve(model: str, project_root: Optional[Path] = None) -> ModelContextInfo:
    """三层解析某个 model 的 ModelContextInfo（纯函数 + 落日志）。

    解析链：BUILTIN[model] (缺失 → BUILTIN["_default"])
            ← global ``model_overrides.toml``
            ← project ``<root>/.deskpet/context.toml``（仅 project_root 非 None）

    深合并：后层只覆盖**出现**的字段，未出现字段保留前层值。``source``
    记为解析链中最后一个真正改动了字段的层（project > global > builtin）。
    """
    base = BUILTIN.get(model)
    if base is None:
        # 缺失 model：拿 _default 的参数，但 model 名仍记用户实际请求的，
        # 便于日志/UI 显示"你用的是 some-local-7b，按 32K 兜底"。
        base = replace(BUILTIN["_default"], model=model)

    info = replace(base, source="builtin")

    # 全局层
    global_section = _model_section(load_global_overrides(), model)
    if global_section:
        merged = _apply_override(info, global_section)
        if merged != info:
            info = replace(merged, source="global")
        else:
            info = merged

    # 项目层（仅 code mode；project_root=None 时 loader 返回 {} 自动跳过）
    project_section = _model_section(load_project_overrides(project_root), model)
    if project_section:
        merged = _apply_override(info, project_section)
        if merged != info:
            info = replace(merged, source="project")
        else:
            info = merged

    logger.info(
        "model_context_resolved model=%s window=%d source=%s",
        info.model,
        info.context_window,
        info.source,
    )
    return info
