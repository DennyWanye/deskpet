# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""TG-12 — 字节级一致回归 (WI-T3.3)。

PRD §3 G5 + D1 末段 + D10 末段 + TG-2 T2-5b 守护的"flag-off 字节级一致"
联合验证：

  - flag-off 时 execute_tool envelope dict 不得 emit 'artifacts' 键
  - flag-off 时 envelope JSON 字段集与 main 分支完全一致
  - 全 flag off 时 result 字段顺序 + 类型 100% 不变

测试组对照 plans/.../01-TDD.md §B TG-12 T12-1/T12-2/T12-5。
"""
from __future__ import annotations

import json

import pytest


@pytest.mark.asyncio
async def test_t12_1_envelope_keys_unchanged_when_flag_off():
    """T12-1: flag-off envelope 字段集精确等于 {ok, result, error}。"""
    from deskpet.tools.registry import ToolRegistry

    registry = ToolRegistry()

    def _h(params, task_id):
        return {"ok": True, "path": "/tmp/x.pptx", "slide_count": 3}

    registry.register(
        "fake_ppt_t12",
        "testing",
        {"name": "fake_ppt_t12", "description": "t", "parameters": {}},
        _h,
    )

    # 无 provider → BC 路径
    env = await registry.execute_tool("fake_ppt_t12", {}, session_id="s")
    keys = set(env.keys())
    assert keys == {"ok", "result", "error"}, f"unexpected keys: {keys}"


@pytest.mark.asyncio
async def test_t12_1_envelope_keys_with_provider_flag_off():
    """T12-1 b: provider 已设 + artifact_envelope=False 也不加键。"""
    from deskpet.tools.registry import ToolRegistry

    class _Cfg:
        class last_mile:
            artifact_envelope = False

    registry = ToolRegistry()

    def _h(params, task_id):
        return {"ok": True, "path": "/tmp/x.pptx"}

    registry.register(
        "fake_t12b", "testing",
        {"name": "fake_t12b", "description": "t", "parameters": {}}, _h,
    )
    registry.set_tools_config_provider(lambda: _Cfg)

    env = await registry.execute_tool("fake_t12b", {}, session_id="s")
    assert "artifacts" not in env, (
        "flag-off envelope must NOT emit 'artifacts' key (T2-5b 字节级硬保证)"
    )


@pytest.mark.asyncio
async def test_t12_2_no_provider_field_order_stable():
    """T12-2: envelope 字段顺序固定 ok/result/error（用于 golden file 对账）。"""
    from deskpet.tools.registry import ToolRegistry

    registry = ToolRegistry()

    def _h(params, task_id):
        return {"ok": True, "value": 42}

    registry.register(
        "fake_t12c", "testing",
        {"name": "fake_t12c", "description": "t", "parameters": {}}, _h,
    )
    env = await registry.execute_tool("fake_t12c", {}, session_id="s")
    assert list(env.keys()) == ["ok", "result", "error"]


@pytest.mark.asyncio
async def test_t12_5_provider_flag_on_adds_artifacts_in_stable_order():
    """flag-on 时字段顺序：{ok, result, error, artifacts}（artifacts 最末）。"""
    from deskpet.tools.registry import ToolRegistry

    class _Cfg:
        class last_mile:
            artifact_envelope = True

    registry = ToolRegistry()

    def _h(params, task_id):
        return {"ok": True, "path": "/tmp/x.pptx"}

    registry.register(
        "fake_t12d", "testing",
        {"name": "fake_t12d", "description": "t", "parameters": {}}, _h,
    )
    registry.set_tools_config_provider(lambda: _Cfg)

    env = await registry.execute_tool("fake_t12d", {}, session_id="s")
    keys = list(env.keys())
    assert keys[:3] == ["ok", "result", "error"]
    assert keys[-1] == "artifacts"


def test_t12_3_envelope_dict_round_trip_json_stable():
    """JSON 序列化稳定（sort_keys 不生效时仍保 dataclass 字段顺序）。"""
    envelope = {"ok": True, "result": "{}", "error": None}
    s1 = json.dumps(envelope, ensure_ascii=False)
    s2 = json.dumps(envelope, ensure_ascii=False)
    assert s1 == s2
    # 经过一轮 loads/dumps 后字段顺序保留
    s3 = json.dumps(json.loads(s1), ensure_ascii=False)
    assert s3 == s1


@pytest.mark.asyncio
async def test_t12_provider_exception_falls_back_byte_identical():
    """provider 异常 → 字节级回落到 BC 路径，不加 artifacts 键。"""
    from deskpet.tools.registry import ToolRegistry

    def _bad():
        raise RuntimeError("synthetic")

    registry = ToolRegistry()

    def _h(params, task_id):
        return {"ok": True, "path": "/tmp/x.pptx"}

    registry.register(
        "fake_t12_bad", "testing",
        {"name": "fake_t12_bad", "description": "t", "parameters": {}}, _h,
    )
    registry.set_tools_config_provider(_bad)

    env = await registry.execute_tool("fake_t12_bad", {}, session_id="s")
    assert "artifacts" not in env
