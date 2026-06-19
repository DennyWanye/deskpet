# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""TG-1 — [tools.last_mile] / [tools.verifier] config schema（WI-T0.3）。

PRD §3 D10 invariant 表落到 _validate_flag_invariants；与 [memory.v2] 嵌套
dispatch 同模式（_load_section 平铺，[tools] 父表把子表 pop 出来单独构建）。

测试组对照 plans/2026-05-23-tool-last-mile-upgrade/01-TDD.md §B TG-1。
"""
from __future__ import annotations

import pytest

import config as _config_mod
from config import (
    AppConfig,
    ConfigError,
    ToolsConfig,
    ToolsLastMileConfig,
    ToolsVerifierConfig,
    load_config,
)


@pytest.fixture(autouse=True)
def _isolate_load_config_cache():
    """清 load_config 的 process-wide cache，消除测试间顺序依赖。

    load_config 缓存键含 (path, mtime, size)。本组多个用例对同一 tmp config.toml
    连续重写后重读（如 test_t1_11 先写空 model 再写 unknown model），warm 进程下
    两次写可能落同一 mtime tick；config.py 已用 size 兜底，这里再加 cache 还原作为
    隔离纵深，防其它测试遗留的 _cfg_cache 串进来。
    """
    _config_mod._reset_load_config_cache()
    yield
    _config_mod._reset_load_config_cache()


def _write(tmp_path, body: str):
    p = tmp_path / "config.toml"
    p.write_text(body, encoding="utf-8")
    return str(p)


# ─── T1-1 ~ T1-6 基础解析 ───────────────────────────────────

def test_t1_1_no_tools_section_all_defaults(tmp_path):
    """无 [tools] 段 → 全部 7 项 last_mile + 7 项 verifier 字段为安全默认值
    (字节级一致保证的前提)。"""
    cfg = load_config(_write(tmp_path, ""))
    tools = cfg.tools
    assert isinstance(tools, ToolsConfig)
    # last_mile defaults
    lm = tools.last_mile
    assert isinstance(lm, ToolsLastMileConfig)
    assert lm.artifact_envelope is False
    assert lm.frontend_artifact_card is False
    assert lm.tauri_artifact_ops is False
    assert lm.default_artifact_dir == ""
    assert lm.outline_preview_default is False
    assert lm.artifact_dir_retention_days == 30
    # verifier defaults
    v = tools.verifier
    assert isinstance(v, ToolsVerifierConfig)
    assert v.emit_receipts is False
    assert v.verify_gate_mode == "off"
    assert v.extractor_fallback_enabled is True
    assert v.ephemeral_subagent_model == "haiku"
    assert v.run_build is False
    assert v.run_tests is False
    assert v.claim_patterns_file == "verify/claim_patterns.yaml"


def test_t1_2_verify_gate_mode_shadow_parses(tmp_path):
    cfg = load_config(_write(tmp_path,
        '[tools.verifier]\nemit_receipts = true\nverify_gate_mode = "shadow"\n'))
    assert cfg.tools.verifier.verify_gate_mode == "shadow"
    assert cfg.tools.verifier.emit_receipts is True


def test_t1_3_verify_gate_mode_invalid_rejected(tmp_path):
    with pytest.raises(ConfigError, match=r"VG-INVARIANT-0"):
        load_config(_write(tmp_path,
            '[tools.verifier]\nverify_gate_mode = "bananas"\n'))


def test_t1_4_retention_days_negative_rejected(tmp_path):
    with pytest.raises(ConfigError, match=r"VG-INVARIANT-6"):
        load_config(_write(tmp_path,
            "[tools.last_mile]\nartifact_dir_retention_days = -1\n"))


def test_t1_5_unknown_key_does_not_crash(tmp_path):
    """_load_section 已有 unknown-key 容忍语义；这里只是确认 [tools.*] 不破。"""
    cfg = load_config(_write(tmp_path,
        "[tools.last_mile]\nartifact_envelope = true\nbogus_key = 1\n"))
    assert cfg.tools.last_mile.artifact_envelope is True


def test_t1_6_default_appconfig_has_tools():
    """AppConfig() 默认（无 config 文件）也要带可用的 tools。"""
    cfg = AppConfig()
    assert isinstance(cfg.tools, ToolsConfig)
    assert isinstance(cfg.tools.last_mile, ToolsLastMileConfig)
    assert isinstance(cfg.tools.verifier, ToolsVerifierConfig)


# ─── T1-7 ~ T1-11 invariant 矩阵 ────────────────────────────

def test_t1_7_strict_requires_receipts(tmp_path):
    """VG-INVARIANT-1：verify_gate_mode != off 必须 emit_receipts=true。"""
    with pytest.raises(ConfigError, match=r"VG-INVARIANT-1"):
        load_config(_write(tmp_path,
            '[tools.verifier]\n'
            'verify_gate_mode = "strict"\n'
            'emit_receipts = false\n'))


def test_t1_7b_shadow_requires_receipts(tmp_path):
    """VG-INVARIANT-1 补：shadow 同样 != off，shadow + emit_receipts=false 必须报错。

    FEAT-A3：t1_7 只覆盖 strict 分支；shadow 分支（出厂默认翻 shadow 的候选）
    此前无显式断言。shadow != "off" 故落入 VG-INVARIANT-1 同一拦截，须确认报错
    而非静默放行（否则 ledger 永空、end_turn 永被阻塞）。
    """
    with pytest.raises(ConfigError, match=r"VG-INVARIANT-1"):
        load_config(_write(tmp_path,
            '[tools.verifier]\n'
            'verify_gate_mode = "shadow"\n'
            'emit_receipts = false\n'))


def test_t1_8_run_build_with_off_auto_promotes_to_shadow(tmp_path, caplog):
    """run_build=true + verify_gate_mode=off → warn + 自动转 shadow。"""
    import logging
    caplog.set_level(logging.WARNING)
    cfg = load_config(_write(tmp_path,
        '[tools.verifier]\n'
        'emit_receipts = true\n'
        'run_build = true\n'
        'verify_gate_mode = "off"\n'))
    assert cfg.tools.verifier.verify_gate_mode == "shadow"
    assert any("auto-promoting verify_gate_mode" in r.message for r in caplog.records)


def test_t1_9_frontend_card_without_envelope_auto_disables(tmp_path, caplog):
    """frontend_artifact_card=true + artifact_envelope=false → warn + 自动关 frontend。"""
    import logging
    caplog.set_level(logging.WARNING)
    cfg = load_config(_write(tmp_path,
        "[tools.last_mile]\n"
        "frontend_artifact_card = true\n"
        "artifact_envelope = false\n"))
    assert cfg.tools.last_mile.frontend_artifact_card is False
    assert any("auto-disabling frontend_artifact_card" in r.message
               for r in caplog.records)


def test_t1_10_retention_days_too_large_rejected(tmp_path):
    with pytest.raises(ConfigError, match=r"VG-INVARIANT-6"):
        load_config(_write(tmp_path,
            "[tools.last_mile]\nartifact_dir_retention_days = 366\n"))


def test_t1_11_ephemeral_model_default_and_reject(tmp_path, caplog):
    """N5：空 model → 默认 'haiku' + warn；unknown_model_x → ConfigError(VG-INVARIANT-5)。"""
    import logging
    caplog.set_level(logging.WARNING)

    # (a) 空字符串触发默认
    cfg = load_config(_write(tmp_path,
        '[tools.verifier]\n'
        'emit_receipts = true\n'
        'verify_gate_mode = "strict"\n'
        'ephemeral_subagent_model = ""\n'))
    assert cfg.tools.verifier.ephemeral_subagent_model == "haiku"
    assert any("defaulting to 'haiku'" in r.message for r in caplog.records)

    # (b) 未知模型 → ConfigError
    with pytest.raises(ConfigError, match=r"VG-INVARIANT-5"):
        load_config(_write(tmp_path,
            '[tools.verifier]\n'
            'emit_receipts = true\n'
            'verify_gate_mode = "strict"\n'
            'ephemeral_subagent_model = "unknown_model_x"\n'))


# ─── 兼容性回归：memory-v2 / 现有段不受影响 ───────────────────

def test_default_load_does_not_break_memory_v2(tmp_path):
    """同一 config 里有 [memory.v2] 和 [tools.last_mile] 时互不干扰。"""
    cfg = load_config(_write(tmp_path,
        "[memory]\nembedding_model = \"bge-m3\"\n"
        "[memory.v2]\nfacts_extract = true\n"
        "[tools.last_mile]\nartifact_envelope = true\n"))
    assert cfg.memory.v2.facts_extract is True
    assert cfg.tools.last_mile.artifact_envelope is True
