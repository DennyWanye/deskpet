# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""TG-0.3 — 子代理并发驱动配置 flag + 并发段读取。"""
from __future__ import annotations

from config import AppConfig, get_subagent_concurrency


def test_default_flags_off():  # 0.3.1
    f = AppConfig().features
    assert f.subagent_driver is False
    assert f.agent_team is False
    assert f.subagent_nonblocking is False


def test_concurrency_default():  # 0.3.2
    glob, lanes = get_subagent_concurrency(AppConfig())
    assert glob == 4
    assert lanes["research"] == 2 and lanes["doc"] == 1 and lanes["web"] == 3


def test_concurrency_reads_raw():  # 0.3.3 ★ b05823b 回归：证开关真生效，非靠默认侥幸
    cfg = AppConfig(
        raw={
            "agent": {
                "concurrency": {
                    "global_concurrency": 6,
                    "lane_caps": {"research": 5, "code": 4},
                }
            }
        }
    )
    glob, lanes = get_subagent_concurrency(cfg)
    assert glob == 6
    assert lanes["research"] == 5 and lanes["code"] == 4
    # 未覆盖的 lane 保留默认
    assert lanes["doc"] == 1


def test_concurrency_no_raw_stub_does_not_raise():  # 0.3.4 防 config 单例陷阱 R2
    class _Stub:  # 无 .raw 属性
        pass

    glob, lanes = get_subagent_concurrency(_Stub())
    assert glob == 4
    assert lanes["general"] == 2


def test_concurrency_bad_values_fall_back():  # 多做：坏值不崩
    cfg = AppConfig(raw={"agent": {"concurrency": {"global_concurrency": "x",
                                                   "lane_caps": {"code": "y"}}}})
    glob, lanes = get_subagent_concurrency(cfg)
    assert glob == 4  # 坏 global 回退
    assert lanes["code"] == 2  # 坏 lane 回退默认
