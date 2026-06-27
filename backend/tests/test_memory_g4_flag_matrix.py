# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""G4 — memory-v2 flag 矩阵 / 契约测试（记忆系统严测 Phase 3）。

## 背景
盘点发现 flag 覆盖只到"二值开关"，缺：
- dataclass 默认全 False 的**字节级契约根基**没有直接断言
- F4 把 config.toml 的 workspace_memory 改 true，但 dataclass 默认仍 False
  —— 这个"出厂开但契约保 False"的关键不变量没测
- flag 依赖关系（enhanced_retriever / entity_path 依赖 facts_extract）没断言

## 本文件验什么
- G4.1 dataclass 默认：MemoryV2Config 全 flag 默认 False（字节级契约根基）
- G4.2 F4 不变量：config.toml workspace_memory=true，但 dataclass 默认仍 False
  （F4 出厂开 ≠ 破坏字节级契约）
- G4.3 config.toml 其余 flag 仍 False（F4 只动 workspace_memory）
- G4.4 flag 依赖关系自洽：enhanced_retriever / entity_path 标注依赖 facts_extract

不测 main.py lifespan 的运行时 warn 逻辑（内联在 lifespan，需起整个 app，
归 e2e/启动测试；本文件聚焦可单测的 config 契约层）。
"""
from __future__ import annotations

import dataclasses
import tomllib
from pathlib import Path

from config import MemoryV2Config

# 记忆 v2 的全部 flag（与 MemoryV2Config 字段对应）
_ALL_FLAGS = [
    "feedback_loop", "facts_extract", "rerank", "enhanced_retriever",
    "chunking", "query_rewrite", "workspace_memory", "reflection",
    "cross_key_merge", "memory_forget", "entity_path", "episodic_to_semantic",
]

# 仓库 config.toml（出厂模板）
_REPO_CONFIG = Path(__file__).resolve().parent.parent.parent / "config.toml"


# ----------------------------------------------------------------------
# G4.1 — dataclass 默认全 False（字节级契约根基）
# ----------------------------------------------------------------------
def test_g4_1_dataclass_defaults_all_false() -> None:
    """MemoryV2Config 全部 flag 字段的 **dataclass 默认值** 必须 False。

    这是 Strangler-Fig "全 flag 默认 False = 字节级等同 gen-1" 契约的根基。
    任何 flag 默认改 True 都会破坏字节级一致性 → test_byte_level_consistency
    会红。这里直接钉死默认值，让"误改默认"在 config 层就被抓。
    """
    cfg = MemoryV2Config()
    for flag in _ALL_FLAGS:
        assert hasattr(cfg, flag), f"MemoryV2Config 缺 flag 字段: {flag}"
        assert getattr(cfg, flag) is False, (
            f"dataclass 默认 {flag} 必须 False（字节级契约根基），"
            f"实际 = {getattr(cfg, flag)}"
        )


def test_g4_1b_no_unexpected_bool_flag_defaults_true() -> None:
    """防御：MemoryV2Config 不该有任何 bool 字段默认 True（防新增 flag 默认开）。"""
    for f in dataclasses.fields(MemoryV2Config):
        if f.type == "bool" or f.default is True or f.default is False:
            if isinstance(f.default, bool):
                assert f.default is False, (
                    f"flag {f.name} dataclass 默认 True —— 破坏字节级契约。"
                    f"新功能默认必须 False，出厂开靠 config.toml。"
                )


# ----------------------------------------------------------------------
# G4.2 — F4 不变量：config.toml 出厂开 workspace_memory，dataclass 仍 False
# ----------------------------------------------------------------------
def test_g4_2_f4_invariant_toml_true_dataclass_false() -> None:
    """F4 核心不变量：config.toml workspace_memory=true，但 dataclass 默认 False。

    F4 让 code 工作记忆出厂可用（config.toml），同时**不动 dataclass 默认**
    以保字节级契约。这两者必须同时成立 —— 否则要么功能没开（toml 也 false），
    要么破坏了契约（dataclass true）。
    """
    assert _REPO_CONFIG.is_file(), f"找不到仓库 config.toml: {_REPO_CONFIG}"
    raw = tomllib.loads(_REPO_CONFIG.read_text(encoding="utf-8"))
    toml_v2 = raw.get("memory", {}).get("v2", {})

    # config.toml 出厂开
    assert toml_v2.get("workspace_memory") is True, (
        "F4: config.toml [memory.v2] workspace_memory 应为 true（出厂开 code 工作记忆）"
    )
    # dataclass 默认仍 False（契约不破）
    assert MemoryV2Config().workspace_memory is False, (
        "F4: dataclass 默认 workspace_memory 必须仍 False（保字节级契约）"
    )


# ----------------------------------------------------------------------
# G4.3 — config.toml 出厂开集合（审计 #4 点亮语义记忆栈 + F4 工作记忆）
# ----------------------------------------------------------------------
# 2026-06-02 审计 #4：config.toml 出厂开的 v2 flag 集合。dataclass 默认仍全
# False（字节契约，见 G4.1）；这里是出厂运行配置层。改这个集合 = 改"出厂默认
# 开哪些 v2 功能"，需同步本断言。
_FACTORY_ON_FLAGS = {
    "workspace_memory",    # F4 (2026-05-31) code 工作记忆
    "facts_extract",       # 审计 #4 写入端事实抽取
    "enhanced_retriever",  # 审计 #4 facts 进 RRF
    "cross_key_merge",     # 审计 #4 跨 key 冲突消解
}


def test_g4_3_factory_on_set_matches_audit_decision() -> None:
    """config.toml [memory.v2]：出厂开的 flag 恰为 _FACTORY_ON_FLAGS，其余 false。

    审计 #4（2026-06-02）点亮 facts_extract + enhanced_retriever + cross_key_merge
    （F4 已开 workspace_memory）。dataclass 默认仍全 False（G4.1 钉住字节契约），
    出厂运行行为靠本 toml。这条钉住"哪些出厂开"，防误开/漏开/误关。
    """
    raw = tomllib.loads(_REPO_CONFIG.read_text(encoding="utf-8"))
    toml_v2 = raw.get("memory", {}).get("v2", {})
    for flag, val in toml_v2.items():
        if not isinstance(val, bool):
            continue
        if flag in _FACTORY_ON_FLAGS:
            assert val is True, (
                f"config.toml [memory.v2] {flag} 应出厂开（审计 #4 / F4），实际 {val}"
            )
        else:
            assert val is False, (
                f"config.toml [memory.v2] {flag}={val} —— 非出厂开集合，应为 False"
            )
    # 反向：出厂开集合里的 flag 都得在 toml 真出现（防漏配）。
    for flag in _FACTORY_ON_FLAGS:
        assert flag in toml_v2, (
            f"出厂开 flag {flag} 未出现在 config.toml [memory.v2]（漏配）"
        )


# ----------------------------------------------------------------------
# G4.4 — flag 依赖关系自洽（enhanced_retriever / entity_path 依赖 facts_extract）
# ----------------------------------------------------------------------
def test_g4_4_dependency_flags_exist_and_independent() -> None:
    """依赖语义断言：enhanced_retriever / entity_path 与 facts_extract 都存在
    且可独立设置（main.py lifespan 据此 warn 但不挡 boot）。

    这里只验字段独立性（能各自设 True/False 不互相强制），运行时降级 warn
    归 e2e。核心防止：将来重构把这些 flag 合并/删除导致依赖校验失效。
    """
    cfg = MemoryV2Config(enhanced_retriever=True, facts_extract=False)
    # 能构造出"依赖未满足"的组合（main.py 会 warn）—— 字段独立
    assert cfg.enhanced_retriever is True
    assert cfg.facts_extract is False

    cfg2 = MemoryV2Config(entity_path=True, facts_extract=True)
    assert cfg2.entity_path is True and cfg2.facts_extract is True
