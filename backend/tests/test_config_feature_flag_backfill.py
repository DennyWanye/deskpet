# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""2026-06-23 — `seed_user_config_if_missing` additively backfills new
factory feature-flag keys into an **existing unified** user config.

Gap fixed (follow-up to WI-OH-4 curation 死链, commit b8d57bf3): the seed
function only ever (a) seeded a full config on first run or (b) wholesale-
replaced a *legacy* ``[llm.local]/[llm.cloud]`` config. An existing unified
``%APPDATA%\\deskpet\\config.toml`` was never touched, so any new factory
flag (e.g. ``[memory.v2] curation_nudge / auto_learnings``) only reached
*fresh* installs — 存量用户 never got it and the feature stayed dark.

These tests pin the additive merge contract:
  * missing allow-listed feature-flag keys ARE backfilled from the bundle
  * user-customised values are PRESERVED (never overwritten)
  * user comments survive the round-trip
  * excluded sections ([llm], api_key, …) are NEVER touched
  * the merge is idempotent and degrades gracefully (no tomlkit / no bundle)
"""
from __future__ import annotations

import sys
from pathlib import Path

import tomli
import pytest

import config as cfg


# A bundle default carrying NEW factory flags the existing user never had.
BUNDLE_TOML = """\
schema_version = 1

[backend]
host = "127.0.0.1"
port = 8100

[llm]
model = "gpt-5.5"
base_url = "https://relay.example/v1"
api_key = ""
max_tokens = 8192

[image]
model = "gpt-image-2"
async_enabled = true
max_concurrent = 2

[memory.v2]
facts_extract = true
# self-curation nudge — the WI-OH-4 flag that wasn't reaching 存量用户
curation_nudge = true
curation_nudge_every_n_turns = 2
auto_learnings = true

[memory.v2.facts]
min_user_chars = 8
facts_weight = 0.2
"""

# An existing 存量 install: unified schema, pre-curation_nudge, with the user
# having *customised* a couple of values (facts_extract off, a custom model).
OLD_USER_TOML = """\
schema_version = 1

[backend]
host = "127.0.0.1"
port = 8100

[llm]
model = "my-custom-model"
base_url = "https://relay.example/v1"

[memory.v2]
facts_extract = false   # user turned this OFF on purpose
"""


@pytest.fixture
def fake_dirs(tmp_path, monkeypatch):
    """Override user_data_dir + bundle source. Yields (user_dir, bundle_src)."""
    user_dir = tmp_path / "user"
    user_dir.mkdir()
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    bundle_src = bundle_dir / "config.toml"
    bundle_src.write_text(BUNDLE_TOML, encoding="utf-8")

    monkeypatch.setenv("DESKPET_USER_DATA_DIR", str(user_dir))
    monkeypatch.setattr(cfg, "_bundle_default_config_path", lambda: bundle_src)
    yield user_dir, bundle_src


def _load(p: Path) -> dict:
    with open(p, "rb") as f:
        return tomli.load(f)


# ---------------------------------------------------------------------------
# Core contract: backfill missing flags, preserve user customisation
# ---------------------------------------------------------------------------


def test_missing_curation_flags_are_backfilled(fake_dirs):
    user_dir, _bundle = fake_dirs
    user_target = user_dir / "config.toml"
    user_target.write_text(OLD_USER_TOML, encoding="utf-8")

    result = cfg.seed_user_config_if_missing()
    assert result == user_target

    data = _load(user_target)
    v2 = data["memory"]["v2"]
    # The actual bug: these now reach 存量用户.
    assert v2["curation_nudge"] is True
    assert v2["auto_learnings"] is True
    assert v2["curation_nudge_every_n_turns"] == 2


def test_user_customised_value_is_preserved(fake_dirs):
    user_dir, _bundle = fake_dirs
    user_target = user_dir / "config.toml"
    user_target.write_text(OLD_USER_TOML, encoding="utf-8")

    cfg.seed_user_config_if_missing()

    data = _load(user_target)
    # facts_extract bundle=true but user set false → MUST stay false.
    assert data["memory"]["v2"]["facts_extract"] is False
    # custom llm model untouched.
    assert data["llm"]["model"] == "my-custom-model"


def test_user_comments_survive_roundtrip(fake_dirs):
    user_dir, _bundle = fake_dirs
    user_target = user_dir / "config.toml"
    user_target.write_text(OLD_USER_TOML, encoding="utf-8")

    cfg.seed_user_config_if_missing()

    text = user_target.read_text(encoding="utf-8")
    assert "# user turned this OFF on purpose" in text


def test_missing_whole_allowlisted_section_is_added(fake_dirs):
    user_dir, _bundle = fake_dirs
    user_target = user_dir / "config.toml"
    user_target.write_text(OLD_USER_TOML, encoding="utf-8")

    cfg.seed_user_config_if_missing()

    data = _load(user_target)
    # [image] absent in OLD_USER_TOML → whole section copied from bundle.
    assert data["image"]["model"] == "gpt-image-2"
    assert data["image"]["async_enabled"] is True
    assert data["image"]["max_concurrent"] == 2


def test_nested_subtable_backfilled(fake_dirs):
    user_dir, _bundle = fake_dirs
    user_target = user_dir / "config.toml"
    user_target.write_text(OLD_USER_TOML, encoding="utf-8")

    cfg.seed_user_config_if_missing()

    data = _load(user_target)
    # [memory.v2.facts] missing entirely → backfilled.
    assert data["memory"]["v2"]["facts"]["min_user_chars"] == 8
    assert data["memory"]["v2"]["facts"]["facts_weight"] == 0.2


# ---------------------------------------------------------------------------
# Excluded sections: privacy / endpoint / api_key must NOT be touched
# ---------------------------------------------------------------------------


def test_excluded_llm_keys_not_backfilled(fake_dirs):
    user_dir, _bundle = fake_dirs
    user_target = user_dir / "config.toml"
    user_target.write_text(OLD_USER_TOML, encoding="utf-8")

    cfg.seed_user_config_if_missing()

    data = _load(user_target)
    llm = data["llm"]
    # bundle has max_tokens + api_key under [llm]; [llm] is NOT allow-listed,
    # so neither may be injected into the user's file.
    assert "max_tokens" not in llm
    assert "api_key" not in llm


# ---------------------------------------------------------------------------
# Idempotency + graceful degradation
# ---------------------------------------------------------------------------


def test_idempotent_second_run_no_change(fake_dirs):
    user_dir, _bundle = fake_dirs
    user_target = user_dir / "config.toml"
    user_target.write_text(OLD_USER_TOML, encoding="utf-8")

    cfg.seed_user_config_if_missing()
    after_first = user_target.read_text(encoding="utf-8")
    mtime_first = user_target.stat().st_mtime_ns

    cfg.seed_user_config_if_missing()
    after_second = user_target.read_text(encoding="utf-8")

    # Nothing missing the 2nd time → file content stable, file not rewritten.
    assert after_second == after_first
    assert user_target.stat().st_mtime_ns == mtime_first


def test_backup_written_with_original_content(fake_dirs):
    user_dir, _bundle = fake_dirs
    user_target = user_dir / "config.toml"
    user_target.write_text(OLD_USER_TOML, encoding="utf-8")

    cfg.seed_user_config_if_missing()

    bak = user_target.with_suffix(".pre-migrate-bak")
    assert bak.exists()
    assert bak.read_text(encoding="utf-8") == OLD_USER_TOML


def test_already_current_config_not_rewritten(fake_dirs):
    """A user whose config already has every bundle flag → no-op (no backup,
    no rewrite)."""
    user_dir, _bundle = fake_dirs
    user_target = user_dir / "config.toml"
    # Bundle == user → nothing missing.
    user_target.write_text(BUNDLE_TOML, encoding="utf-8")
    mtime_before = user_target.stat().st_mtime_ns

    cfg.seed_user_config_if_missing()

    assert user_target.stat().st_mtime_ns == mtime_before
    assert not user_target.with_suffix(".pre-migrate-bak").exists()


def test_no_bundle_source_is_graceful(monkeypatch, tmp_path):
    user_dir = tmp_path / "user"
    user_dir.mkdir()
    user_target = user_dir / "config.toml"
    user_target.write_text(OLD_USER_TOML, encoding="utf-8")
    monkeypatch.setenv("DESKPET_USER_DATA_DIR", str(user_dir))
    monkeypatch.setattr(cfg, "_bundle_default_config_path", lambda: None)

    result = cfg.seed_user_config_if_missing()

    assert result == user_target
    # No bundle → can't backfill; file untouched, no backup.
    assert user_target.read_text(encoding="utf-8") == OLD_USER_TOML
    assert not user_target.with_suffix(".pre-migrate-bak").exists()


def test_merge_helper_skips_gracefully_without_tomlkit(fake_dirs, monkeypatch):
    """If tomlkit can't be imported, the merge no-ops (returns False) instead
    of raising — startup must never break on a backfill problem."""
    user_dir, bundle_src = fake_dirs
    user_target = user_dir / "config.toml"
    user_target.write_text(OLD_USER_TOML, encoding="utf-8")

    # Force `import tomlkit` to raise inside the helper.
    monkeypatch.setitem(sys.modules, "tomlkit", None)

    changed = cfg._merge_missing_feature_flags(user_target, bundle_src)

    assert changed is False
    # File untouched, no backfill happened.
    data = _load(user_target)
    assert "curation_nudge" not in data["memory"]["v2"]


def test_legacy_schema_still_wholesale_replaced_not_merged(fake_dirs):
    """Regression guard: a legacy [llm.local]/[llm.cloud] config still takes
    the wholesale-replace path (not the additive merge), so the sealos-401
    fix is unaffected."""
    user_dir, _bundle = fake_dirs
    user_target = user_dir / "config.toml"
    user_target.write_text(
        'schema_version = 1\n\n[llm]\nstrategy = "cloud_first"\n\n'
        '[llm.local]\nmodel = "gemma4:e4b"\n\n'
        '[llm.cloud]\nbase_url = "https://vcrppsmofoyv.cloud.sealos.io/v1"\n',
        encoding="utf-8",
    )

    cfg.seed_user_config_if_missing()

    data = _load(user_target)
    # Wholesale replaced with bundle → unified [llm], no legacy subtables.
    assert "local" not in data["llm"]
    assert "cloud" not in data["llm"]
    assert data["llm"]["model"] == "gpt-5.5"
    # legacy-bak (not pre-migrate-bak) is the path the wholesale-replace uses.
    assert user_target.with_suffix(".legacy-bak").exists()
