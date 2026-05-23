"""TG-2 — ToolArtifact 信封 + registry 包装（WI-T1.1）。

PRD §3 D1 信封 + D5 sha256 异步；测试对照 plans/2026-05-23-tool-last-mile-upgrade/01-TDD.md §B TG-2。

注意 T2-5/T2-5b 的字节级硬保证：flag OFF 时 envelope dict 不得 emit
``artifacts`` 键（不是空数组，是缺键）。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from pathlib import Path

import pytest

from deskpet.tools.artifact import (
    ToolArtifact,
    extract_artifacts_from_result,
    sha256_file_async,
)


# ─── T2-1 ~ T2-4 dataclass 基础 ─────────────────────────────

def test_t2_1_to_dict_field_order_stable():
    """字段顺序固定（用于字节级 golden file 对账）。"""
    art = ToolArtifact(
        kind="file",
        path="C:\\out\\x.pptx",
        title="x.pptx",
        created_at="2026-05-23T00:00:00Z",
    )
    d = art.to_dict()
    # required keys present
    for k in ("kind", "title", "created_at"):
        assert k in d
    # field-order 稳定（dataclass 顺序由定义决定）
    keys = list(d.keys())
    assert keys.index("kind") < keys.index("path") < keys.index("title")


def test_t2_2_file_kind_requires_path():
    with pytest.raises(ValueError, match=r"kind='file' requires path"):
        ToolArtifact(kind="file", path=None, title="x", created_at="2026-05-23T00:00:00Z")


def test_t2_3_url_kind_requires_url():
    with pytest.raises(ValueError, match=r"kind='url' requires url"):
        ToolArtifact(kind="url", url=None, title="x", created_at="2026-05-23T00:00:00Z")


def test_t2_4_preview_truncated_at_2kb():
    long = "x" * 3000
    art = ToolArtifact(
        kind="text", title="t", preview=long, created_at="2026-05-23T00:00:00Z"
    )
    assert art.preview is not None
    assert len(art.preview) <= 2048
    assert art.preview.endswith("…(truncated)")


# ─── T2-5 / T2-5b 字节级硬保证 ──────────────────────────────

def test_t2_5_extract_returns_empty_when_no_path():
    """无 path/url 字段的 result → 0 artifacts（registry 应不加 key）。"""
    arts = extract_artifacts_from_result(
        tool_name="some_tool",
        result_json='{"ok": true, "message": "done"}',
    )
    assert arts == []


def test_t2_5b_envelope_omits_artifacts_key_when_empty():
    """flag-off / 无 artifact 时，envelope 必须缺 'artifacts' 键
    （不是 'artifacts': []）—— 字节级一致硬保证。"""
    # 验证 registry 包装逻辑（在 artifact.py 提供的 helper）
    from deskpet.tools.artifact import maybe_add_artifacts
    base = {"ok": True, "result": "{\"ok\": true}", "error": None}
    enhanced = maybe_add_artifacts(
        envelope=base, tool_name="x", enable=False,  # flag OFF
    )
    assert "artifacts" not in enhanced
    # 即便 enable=True 但无可提取 path 也不加键
    enhanced2 = maybe_add_artifacts(
        envelope=base, tool_name="x", enable=True,
    )
    assert "artifacts" not in enhanced2


# ─── T2-6 path → 单 artifact 推断 ───────────────────────────

def test_t2_6_infer_single_file_artifact_from_path():
    arts = extract_artifacts_from_result(
        tool_name="ppt_create",
        result_json='{"ok": true, "path": "C:\\\\out\\\\x.pptx", "slide_count": 5}',
    )
    assert len(arts) == 1
    a = arts[0]
    assert a.kind == "file"
    assert a.path == "C:\\out\\x.pptx"
    assert a.title == "x.pptx"  # basename


# ─── T2-7 多调用不串号 ─────────────────────────────────────

@pytest.mark.asyncio
async def test_t2_7_concurrent_extracts_no_crosstalk():
    async def run(tool_name: str, path: str):
        return extract_artifacts_from_result(
            tool_name=tool_name,
            result_json=json.dumps({"ok": True, "path": path}),
        )
    results = await asyncio.gather(
        *[run("ppt_create", f"C:\\out\\a{i}.pptx") for i in range(10)]
    )
    paths = {r[0].path for r in results}
    assert len(paths) == 10  # 10 个不同 path 互不串扰


# ─── T2-8 大文件 sha256 异步不阻塞 ──────────────────────────

@pytest.mark.asyncio
async def test_t2_8_large_file_sha256_uses_executor(tmp_path: Path):
    """100MB 文件触发线程池 sha256，期间另一个轻量任务调度延迟 < 50ms。"""
    big = tmp_path / "big.bin"
    big.write_bytes(b"\0" * (100 * 1024 * 1024))  # 100MB

    sha_task = asyncio.create_task(sha256_file_async(big))

    # 同时跑一个轻量任务，测调度延迟
    start = time.monotonic()
    await asyncio.sleep(0)  # yield event loop
    sched_delay_ms = (time.monotonic() - start) * 1000
    assert sched_delay_ms < 50, f"event loop blocked {sched_delay_ms}ms"

    sha = await sha_task
    # sha256 of 100MB zeros
    expected = hashlib.sha256(b"\0" * (100 * 1024 * 1024)).hexdigest()
    assert sha == expected


# ─── T2-9 sha256 超时 → sha256_pending ──────────────────────

@pytest.mark.asyncio
async def test_t2_9_sha256_timeout_marks_pending(tmp_path: Path, monkeypatch):
    """sha256 超时 → 返回 None，调用方可标 sha256_pending。

    用 monkeypatch 让底层 _sha256_file_sync 强制阻塞 1s，再用 0.05s timeout
    必然触发 asyncio.TimeoutError 路径（小文件 sha256 微秒级，普通 timeout
    无法可靠触发 race）。
    """
    f = tmp_path / "x.bin"
    f.write_bytes(b"hello")

    from deskpet.tools import artifact as art_mod

    def _slow_sha(path):
        import time
        time.sleep(1.0)
        return "deadbeef" * 8

    monkeypatch.setattr(art_mod, "_sha256_file_sync", _slow_sha)
    sha = await sha256_file_async(f, timeout_s=0.05)
    assert sha is None  # 调用方据此走 sha256_pending 分支


@pytest.mark.asyncio
async def test_t2_9b_sha256_missing_file_returns_none(tmp_path: Path):
    """额外保险：path 不存在 → 返回 None（不报错），调用方按 missing_file 处理。"""
    sha = await sha256_file_async(tmp_path / "nope.bin")
    assert sha is None


# ─── registry 集成（WI-T1.1 wire-up） ───────────────────────


def _make_fake_registry():
    """构造干净的 ToolRegistry + 注册一个 mock tool 用于集成测试。"""
    from deskpet.tools.registry import ToolRegistry

    registry = ToolRegistry()

    def _handler(params, task_id):
        # 返回 dict，registry 内部 JSON 化为 envelope.result
        return {"ok": True, "path": params.get("path", "/tmp/x.pptx")}

    registry.register(
        name="fake_ppt",
        toolset="testing",
        schema={"name": "fake_ppt", "description": "test", "parameters": {}},
        handler=_handler,
    )
    return registry


@pytest.mark.asyncio
async def test_t2_int_no_provider_bc_envelope_unchanged():
    """BC：没设 tools_config_provider → envelope 不含 artifacts 键（字节级保证）。"""
    registry = _make_fake_registry()
    env = await registry.execute_tool(
        "fake_ppt", {"path": "/tmp/demo.pptx"}, session_id="s1"
    )
    assert env["ok"] is True
    assert "artifacts" not in env


@pytest.mark.asyncio
async def test_t2_int_provider_flag_on_adds_artifacts():
    """provider 返回 flag=True 且 result 有 path → envelope 加 artifacts。"""
    registry = _make_fake_registry()

    class _FakeLastMile:
        artifact_envelope = True

    class _FakeCfg:
        last_mile = _FakeLastMile()

    registry.set_tools_config_provider(lambda: _FakeCfg())

    env = await registry.execute_tool(
        "fake_ppt", {"path": "/tmp/demo.pptx"}, session_id="s1"
    )
    assert env["ok"] is True
    assert "artifacts" in env
    assert len(env["artifacts"]) == 1
    art = env["artifacts"][0]
    assert art["kind"] == "file"
    assert art["path"] == "/tmp/demo.pptx"


@pytest.mark.asyncio
async def test_t2_int_provider_flag_off_omits_artifacts():
    """provider 返回 flag=False → envelope 不加键（与无 provider 一致）。"""
    registry = _make_fake_registry()

    class _FakeLastMile:
        artifact_envelope = False

    class _FakeCfg:
        last_mile = _FakeLastMile()

    registry.set_tools_config_provider(lambda: _FakeCfg())

    env = await registry.execute_tool(
        "fake_ppt", {"path": "/tmp/demo.pptx"}, session_id="s1"
    )
    assert env["ok"] is True
    assert "artifacts" not in env


@pytest.mark.asyncio
async def test_t2_int_provider_exception_does_not_break_dispatch(caplog):
    """provider 异常 → warn log + envelope 仍正常返回（never break dispatch）。"""
    import logging
    caplog.set_level(logging.WARNING)

    registry = _make_fake_registry()
    def _bad_provider():
        raise RuntimeError("synthetic config crash")
    registry.set_tools_config_provider(_bad_provider)

    env = await registry.execute_tool(
        "fake_ppt", {"path": "/tmp/x.pptx"}, session_id="s1"
    )
    assert env["ok"] is True
    assert "artifacts" not in env
    # warn log 存在
    assert any("tools_config_provider raised" in r.message for r in caplog.records)
