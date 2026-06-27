# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Unit tests for first-run model provisioner (Option A / COS 直下).

不联网：注入 fake fetch_url（返回 canned manifest + 文件 bytes）。验证缺失
检测、状态机、manifest 驱动下载（含嵌套路径）、错误降级，以及目标目录走注入
的 models_dir（不被 user_models_dir override 影响）。
"""
from __future__ import annotations

import json
from pathlib import Path

from deskpet.model_provisioner import _MODELS, ModelProvisioner


def _populate(models_dir: Path, subdir: str, sentinel: str | None) -> None:
    d = models_dir / subdir
    d.mkdir(parents=True, exist_ok=True)
    (d / (sentinel or "weights.bin")).write_bytes(b"x" * 16)


def _make_fetch(manifests: dict[str, dict], file_bytes: bytes = b"data1234"):
    """构造 fake fetch_url：manifest.json → JSON；其它 URL → file_bytes。"""

    def fetch(url: str) -> bytes:
        if url.endswith("/manifest.json"):
            subdir = url.split("/")[-2]
            return json.dumps(manifests[subdir]).encode("utf-8")
        return file_bytes

    return fetch


def _noop_fetch(url: str) -> bytes:
    return b""


def test_missing_lists_all_when_empty(tmp_path: Path) -> None:
    prov = ModelProvisioner(models_dir=tmp_path, fetch_url=_noop_fetch)
    assert {m[0] for m in prov.missing()} == {m[0] for m in _MODELS}


def test_missing_excludes_ready_models(tmp_path: Path) -> None:
    _populate(tmp_path, "faster-whisper-large-v3-turbo", "model.bin")
    prov = ModelProvisioner(models_dir=tmp_path, fetch_url=_noop_fetch)
    assert [m[0] for m in prov.missing()] == ["bge-m3-int8"]


def test_sentinel_required_for_whisper(tmp_path: Path) -> None:
    # 目录存在但缺 model.bin 哨兵 → 仍算缺失（半下载不算就绪）。
    (tmp_path / "faster-whisper-large-v3-turbo").mkdir(parents=True)
    (tmp_path / "faster-whisper-large-v3-turbo" / "config.json").write_text("{}")
    prov = ModelProvisioner(models_dir=tmp_path, fetch_url=_noop_fetch)
    assert "faster-whisper-large-v3-turbo" in {m[0] for m in prov.missing()}


def test_run_downloads_via_manifest_and_reaches_ready(tmp_path: Path) -> None:
    manifests = {
        "bge-m3-int8": {
            "files": [{"path": "config.json", "size": 8}, {"path": "1_Pooling/config.json", "size": 8}],
            "total_bytes": 16,
        },
        "faster-whisper-large-v3-turbo": {
            "files": [{"path": "model.bin", "size": 8}, {"path": "config.json", "size": 8}],
            "total_bytes": 16,
        },
    }
    prov = ModelProvisioner(models_dir=tmp_path, fetch_url=_make_fetch(manifests))
    prov._run()

    assert prov.status()["state"] == "ready"
    # 嵌套路径正确落盘。
    assert (tmp_path / "bge-m3-int8" / "1_Pooling" / "config.json").read_bytes() == b"data1234"
    assert (tmp_path / "faster-whisper-large-v3-turbo" / "model.bin").is_file()
    assert prov.missing() == []


def test_run_noop_when_all_ready(tmp_path: Path) -> None:
    for subdir, sentinel in _MODELS:
        _populate(tmp_path, subdir, sentinel)
    called = False

    def fetch(url: str) -> bytes:
        nonlocal called
        called = True
        return b""

    prov = ModelProvisioner(models_dir=tmp_path, fetch_url=fetch)
    prov._run()
    assert prov.status()["state"] == "ready"
    assert prov.status()["total"] == 0
    assert called is False


def test_run_error_degrades_to_error_state(tmp_path: Path) -> None:
    def boom(url: str) -> bytes:
        raise RuntimeError("cos unreachable")

    prov = ModelProvisioner(models_dir=tmp_path, fetch_url=boom)
    prov._run()
    st = prov.status()
    assert st["state"] == "error"
    assert "cos unreachable" in (st["error"] or "")


def test_total_bytes_from_manifest(tmp_path: Path) -> None:
    manifests = {
        "bge-m3-int8": {"files": [{"path": "a.bin", "size": 100}], "total_bytes": 100},
        "faster-whisper-large-v3-turbo": {"files": [{"path": "model.bin", "size": 50}], "total_bytes": 50},
    }
    seen_total: list[int] = []
    orig = ModelProvisioner._update

    def spy(self, **kw):  # 捕获 total_bytes 被设置的时刻
        if "total_bytes" in kw and kw["total_bytes"]:
            seen_total.append(kw["total_bytes"])
        orig(self, **kw)

    prov = ModelProvisioner(models_dir=tmp_path, fetch_url=_make_fetch(manifests))
    prov._update = spy.__get__(prov, ModelProvisioner)  # type: ignore[method-assign]
    prov._run()
    assert 100 in seen_total and 50 in seen_total


def test_cdn_base_env_override(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DESKPET_MODEL_CDN_BASE", "https://example.com/m/")
    captured: list[str] = []

    def fetch(url: str) -> bytes:
        captured.append(url)
        if url.endswith("/manifest.json"):
            return json.dumps({"files": [], "total_bytes": 0}).encode()
        return b""

    prov = ModelProvisioner(models_dir=tmp_path, fetch_url=fetch)
    prov._run()
    # 末尾斜杠被剥离；manifest URL 用了 override 的 base。
    assert any(u.startswith("https://example.com/m/bge-m3-int8/manifest.json") for u in captured)


def test_status_reports_downloaded_bytes_from_disk(tmp_path: Path) -> None:
    prov = ModelProvisioner(models_dir=tmp_path, fetch_url=_noop_fetch)
    sub = "bge-m3-int8"
    (tmp_path / sub).mkdir(parents=True)
    (tmp_path / sub / "part.bin").write_bytes(b"y" * 4096)
    prov._update(state="downloading", current=sub, index=1, total=1, total_bytes=2_300_000_000)
    assert prov.status()["downloaded_bytes"] == 4096


# --- control-WS handler 契约（前端 ModelDownloadBanner 依赖字段名）-----------


class _FakeWS:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_json(self, obj: dict) -> None:
        self.sent.append(obj)


def test_ipc_handler_returns_status(tmp_path: Path) -> None:
    import asyncio

    import context as ctx_mod
    import p4_ipc

    prov = ModelProvisioner(models_dir=tmp_path, fetch_url=_noop_fetch)
    sc = ctx_mod.ServiceContext()
    sc.register("model_provisioner", prov)
    ws = _FakeWS()
    asyncio.run(p4_ipc._handle_model_provision_status(ws, {}, sc))

    assert len(ws.sent) == 1
    msg = ws.sent[0]
    assert msg["type"] == "model_provision_status_response"
    assert set(msg["payload"]).issuperset({"state"})
    assert msg["payload"]["state"] in {"idle", "checking", "downloading", "ready", "error"}


def test_ipc_handler_unregistered_returns_ready() -> None:
    import asyncio

    import context as ctx_mod
    import p4_ipc

    sc = ctx_mod.ServiceContext()
    ws = _FakeWS()
    asyncio.run(p4_ipc._handle_model_provision_status(ws, {}, sc))
    assert ws.sent[0]["payload"]["state"] == "ready"
