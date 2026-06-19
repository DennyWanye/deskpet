# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations

import ssl
from pathlib import Path


class _FakeClient:
    post_calls = 0

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def post(self, url, **kwargs):
        type(self).post_calls += 1
        raise ssl.SSLError("UNEXPECTED_EOF_WHILE_READING")


def test_generate_png_retries_on_ssl_error(monkeypatch):
    import deskpet.tools.image_tools as m

    _FakeClient.post_calls = 0
    sleeps: list[float] = []

    monkeypatch.setattr(m.httpx, "Client", _FakeClient)
    monkeypatch.setattr(m, "_resolve_endpoint", lambda: ("https://relay.example/v1", "key"))
    monkeypatch.setattr(m.time, "sleep", lambda seconds: sleeps.append(seconds))

    png, hint = m._generate_png("complex image", m._DEFAULT_SIZE, m._DEFAULT_MODEL)

    assert png is None
    assert _FakeClient.post_calls == m._MAX_ATTEMPTS
    assert len(sleeps) == m._MAX_ATTEMPTS - 1
    assert hint is not None
    assert "SSLError" in hint or "瞬时" in hint


def test_generate_images_batch_returns_paths(monkeypatch, tmp_path):
    import deskpet.tools.image_tools as m

    monkeypatch.setattr(m, "_generate_png", lambda prompt, size, model: (b"\x89PNG fake", None))
    monkeypatch.setattr(m, "_workspace_dir", lambda: tmp_path)

    out = m.generate_images(["a", "b", ""])

    assert len(out) == 3
    for item in out[:2]:
        assert item["error"] is None
        assert item["path"] is not None
        assert Path(item["path"]).exists()
        assert Path(item["path"]).read_bytes() == b"\x89PNG fake"
    assert out[2]["prompt"] == ""
    assert out[2]["path"] is None
    assert out[2]["error"] == "empty prompt"
