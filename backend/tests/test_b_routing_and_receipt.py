# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations

import json
import logging

import pytest


@pytest.mark.asyncio
async def test_receipt_args_strip_injected_keys(caplog, monkeypatch):
    from deskpet.tools.registry import ToolRegistry
    import deskpet.tools.receipt_store as receipt_store

    seen: dict[str, object] = {}

    def _handler(args, task_id):
        seen.update(args)
        return json.dumps({"ok": True}, ensure_ascii=False)

    class _Store:
        key = b"\x33" * 32

        def __init__(self) -> None:
            self.receipt_args = None

    def _fake_emit_receipt(store, *, args, **kwargs):
        json.dumps(args, ensure_ascii=False)
        store.receipt_args = dict(args)
        return object()

    store = _Store()
    monkeypatch.setattr(receipt_store, "emit_receipt", _fake_emit_receipt)
    registry = ToolRegistry()
    registry.register(
        "probe_fetch",
        "web",
        {
            "name": "probe_fetch",
            "description": "probe",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        },
        _handler,
    )
    registry.set_session_context(
        "s1",
        {
            "_session_id": "s1",
            "_image_worker": object(),
            "_write_scope_root": "/path/to/deskpet",
        },
    )
    registry.set_receipt_store_provider(lambda: store)

    caplog.set_level(logging.WARNING)
    env = await registry.execute_tool(
        "probe_fetch",
        {"url": "https://example.com"},
        session_id="s1",
        task_id="t1",
    )

    assert env["ok"] is True
    assert seen["_image_worker"] is not None
    assert seen["_session_id"] == "s1"
    assert seen["url"] == "https://example.com"
    assert store.receipt_args == {"url": "https://example.com"}
    assert not any(str(k).startswith("_") for k in store.receipt_args)
    assert not any(
        "receipt_store_provider raised in execute_tool" in r.message
        for r in caplog.records
    )


def test_generate_image_description_disambiguates():
    from deskpet.tools import image_tools

    desc = image_tools._SCHEMA["description"]
    assert "AI" in desc
    assert "文生图" in desc
    assert "生成" in desc
    assert "不要" in desc
    assert "搜索" in desc
    assert "web_fetch" in desc
    assert "保存" in desc or "本地" in desc


def test_web_fetch_description_not_for_images():
    from deskpet.tools import web_tools

    desc = web_tools._SCHEMA_FETCH["description"]
    assert "网页文本" in desc
    assert "不是" in desc or "不用于" in desc
    assert "生成" in desc
    assert "获取图片" in desc or "图片" in desc
    assert "generate_image" in desc
