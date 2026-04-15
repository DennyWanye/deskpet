"""P2-1-S8 BillingConfig loader tests."""
from __future__ import annotations

from pathlib import Path

from config import BillingConfig, load_config


def test_billing_config_from_toml(tmp_path):
    raw = {
        "billing": {
            "daily_budget_cny": 5.0,
            "pricing": {"qwen3.6-plus": 8.0, "deepseek-chat": 1.0},
        }
    }
    cfg = BillingConfig.from_toml(raw, db_dir=tmp_path)
    assert cfg.daily_budget_cny == 5.0
    assert cfg.pricing["qwen3.6-plus"] == 8.0
    # Default kept when the key is absent.
    assert cfg.unknown_model_price_cny_per_m_tokens == 20.0
    assert cfg.db_path == tmp_path / "billing.db"


def test_billing_config_defaults():
    cfg = BillingConfig.from_toml({}, db_dir=Path("/tmp"))
    assert cfg.daily_budget_cny == 10.0
    assert cfg.pricing == {}
    assert cfg.unknown_model_price_cny_per_m_tokens == 20.0


def test_billing_config_unknown_keys_tolerated(tmp_path):
    """Future-unknown keys in the billing section shouldn't crash the loader."""
    raw = {
        "billing": {
            "daily_budget_cny": 7.5,
            "future_key_xyz": "ignored",
        }
    }
    cfg = BillingConfig.from_toml(raw, db_dir=tmp_path)
    assert cfg.daily_budget_cny == 7.5


def test_load_config_populates_billing(tmp_path):
    toml_body = """
schema_version = 1

[memory]
db_path = "./data/memory.db"

[billing]
daily_budget_cny = 3.5

[billing.pricing]
"qwen3.6-plus" = 8.0
"""
    p = tmp_path / "config.toml"
    p.write_text(toml_body, encoding="utf-8")
    cfg = load_config(p)
    assert cfg.billing.daily_budget_cny == 3.5
    assert cfg.billing.pricing["qwen3.6-plus"] == 8.0
    # db_path derived from memory.db dir
    assert cfg.billing.db_path.name == "billing.db"


def test_load_config_missing_billing_section_has_defaults(tmp_path):
    """A config.toml without [billing] still yields a usable BillingConfig."""
    p = tmp_path / "config.toml"
    p.write_text("schema_version = 1\n", encoding="utf-8")
    cfg = load_config(p)
    assert cfg.billing.daily_budget_cny == 10.0
    assert cfg.billing.pricing == {}
