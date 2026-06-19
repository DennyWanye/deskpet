# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""[image] 段配置真能被工具读到的回归测试。

历史 bug(真机 UI 测 TC-P2-05 同根因): image_tools 各读配置函数用
``import config as _cfg; _cfg.config.raw.get("image")``,但 ``config`` 模块
**并无** ``config`` 属性(已加载单例是 main.py 的 ``main.config``,工具拿不到)
→ 恒抛 AttributeError 被吞 → ``[image] model/quality/trust_env_proxy/
async_enabled`` 全部恒落默认值,用户配了等于没配。

修法: ``config.standalone_config_section("image")`` 直读磁盘 config(借
load_config 进程级缓存)。本测试**证伪** "[image] 配被读到":所有 fixture
值都故意 != 默认值,若代码回落默认(旧死代码行为),断言必失败。
"""
from __future__ import annotations

from pathlib import Path

import pytest

import config
from deskpet.tools import image_tools as it


def _point_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str) -> Path:
    """写一个 tmp config.toml,让 DESKPET_CONFIG 指向它,并清 load_config 缓存。"""
    cfg = tmp_path / "config.toml"
    cfg.write_text(body, encoding="utf-8")
    monkeypatch.setenv("DESKPET_CONFIG", str(cfg))
    config._reset_load_config_cache()
    return cfg


def test_image_section_values_are_read(tmp_path, monkeypatch):
    """[image] 段全字段非默认值 → 工具必须读到这些非默认值(证伪死代码)。"""
    # 每个值都故意偏离 image_tools 里的默认(gpt-image-2 / medium / False / True)
    _point_config(
        tmp_path,
        monkeypatch,
        "[image]\n"
        'model = "custom-image-model-xyz"\n'
        'quality = "high"\n'
        "trust_env_proxy = true\n"
        "async_enabled = false\n",
    )

    assert it._image_model() == "custom-image-model-xyz"
    assert it._image_quality() == "high"
    assert it._trust_env_proxy() is True   # 默认 False — 读不到就会是 False
    assert it._async_enabled() is False    # 默认 True  — 读不到就会是 True


def test_llm_base_url_fallback_is_read(tmp_path, monkeypatch):
    """无 llm_runtime.json 时,_resolve_endpoint 的 base_url 兜底应来自
    config ``[llm].base_url``(旧死代码恒返回空串)。"""
    # 把 user_data_dir 指到一个没有 llm_runtime.json 的空目录,逼 base_url
    # 走 config [llm] 兜底分支。
    empty_ud = tmp_path / "ud"
    empty_ud.mkdir()
    monkeypatch.setattr(it, "_workspace_dir", lambda: empty_ud, raising=False)
    monkeypatch.setattr("paths.user_data_dir", lambda: empty_ud, raising=False)

    _point_config(
        tmp_path,
        monkeypatch,
        "[llm]\n"
        'base_url = "https://relay.example.test/v1"\n',
    )

    base_url, _api_key = it._resolve_endpoint()
    assert base_url == "https://relay.example.test/v1"


def test_missing_section_falls_back_to_defaults(tmp_path, monkeypatch):
    """[image] 段缺失 → 工具回落各自默认值(不抛,helper 返回 {})。"""
    _point_config(tmp_path, monkeypatch, "[other]\nx = 1\n")

    assert it._image_model() == "gpt-image-2"
    assert it._image_quality() == "medium"
    assert it._trust_env_proxy() is False
    assert it._async_enabled() is True


def test_standalone_config_section_robustness(tmp_path, monkeypatch):
    """config.standalone_config_section: 段存在返 dict,缺失/非 dict 返 {}。"""
    _point_config(
        tmp_path,
        monkeypatch,
        "[image]\nmodel = \"m\"\n\n[scalar]\nnot_a_table = 1\n",
    )

    assert config.standalone_config_section("image") == {"model": "m"}
    assert config.standalone_config_section("does_not_exist") == {}
    # 注:[scalar] 本身是 table;这里验证不存在的纯标量键路径不会炸,返回 {}
    assert config.standalone_config_section("nope") == {}
