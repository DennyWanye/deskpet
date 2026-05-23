"""TG-1 — [memory.v2] config schema（WI-M0.3）。

[memory.v2] / [memory.v2.facts] 是 [memory] 的嵌套子表；_load_section 只做
平铺解析，故 load_config 把 v2 pop 出来单独构建（_load_memory_v2）。
"""
from __future__ import annotations

from config import load_config, MemoryV2Config, MemoryV2FactsConfig


def _write(tmp_path, body: str):
    p = tmp_path / "config.toml"
    p.write_text(body, encoding="utf-8")
    return str(p)


def test_t1_1_no_v2_section_all_flags_false(tmp_path):
    cfg = load_config(_write(tmp_path, "[memory]\nembedding_model = \"bge-m3\"\n"))
    v2 = cfg.memory.v2
    assert isinstance(v2, MemoryV2Config)
    for flag in ("feedback_loop", "facts_extract", "rerank",
                 "enhanced_retriever", "chunking", "query_rewrite",
                 "workspace_memory", "reflection"):
        assert getattr(v2, flag) is False, flag
    # nested facts defaults
    assert isinstance(v2.facts, MemoryV2FactsConfig)
    assert v2.facts.min_user_chars == 8
    assert v2.facts.facts_weight == 0.2


def test_t1_2_explicit_flag_true(tmp_path):
    cfg = load_config(_write(tmp_path,
        "[memory]\n[memory.v2]\nfacts_extract = true\nrerank = true\n"))
    assert cfg.memory.v2.facts_extract is True
    assert cfg.memory.v2.rerank is True
    assert cfg.memory.v2.enhanced_retriever is False  # untouched → default


def test_t1_3_nested_facts_section(tmp_path):
    cfg = load_config(_write(tmp_path,
        "[memory]\n[memory.v2.facts]\nmin_user_chars = 12\nfacts_weight = 0.35\n"))
    assert cfg.memory.v2.facts.min_user_chars == 12
    assert cfg.memory.v2.facts.facts_weight == 0.35
    # flags still default
    assert cfg.memory.v2.facts_extract is False


def test_t1_4_unknown_key_in_v2_does_not_crash(tmp_path):
    cfg = load_config(_write(tmp_path,
        "[memory]\n[memory.v2]\nfacts_extract = true\nbogus_key = 1\n"))
    # unknown key dropped by _load_section, known flag still parsed
    assert cfg.memory.v2.facts_extract is True


def test_default_appconfig_has_v2():
    """AppConfig() 默认（无 config 文件）也要带可用的 v2。"""
    from config import AppConfig
    assert isinstance(AppConfig().memory.v2, MemoryV2Config)
