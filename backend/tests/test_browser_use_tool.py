# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""P5: browser_use_tool unit tests.

NEVER launches a real browser or LLM — the browser-use ``Agent`` /
``ChatOpenAI`` are mocked via a fake ``browser_use`` package injected
into ``sys.modules``, so the background worker exercises the real job
plumbing (thread → job store → poll) without any network or Chromium.

Coverage:

* flag OFF → ``{ok:false,error:"disabled"}`` and tool is still registered.
* localhost enforcement: non-local ``start_url`` rejected; ``allow_external``
  + localhost both accepted.
* instant fire-and-quick-return shape ``{ok:true,status:"running",job_id}``,
  then ``action='result'`` eventually yields the mocked ``done`` result.
* creds resolution prefers ``llm_runtime.json`` over the config section.
"""
from __future__ import annotations

import json
import sys
import time
import types
from pathlib import Path

import pytest

from deskpet.tools import browser_use_tool as but
from deskpet.tools.registry import registry


# ---------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point user_data_dir + config at tmp, reset cached config, and make
    sure no real DESKPET_BROWSER_USE_ENABLED leaks in from the shell."""
    monkeypatch.setenv("DESKPET_USER_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("DESKPET_BROWSER_USE_ENABLED", raising=False)
    monkeypatch.delenv("DESKPET_CLOUD_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    # Empty config.toml so [code_e2e] defaults apply unless a test writes one.
    cfg = tmp_path / "config.toml"
    cfg.write_text("", encoding="utf-8")
    monkeypatch.setenv("DESKPET_CONFIG", str(cfg))
    but.reset_cache()
    yield
    but.reset_cache()


def _write_cfg(tmp_path: Path, body: str) -> None:
    (tmp_path / "config.toml").write_text(body, encoding="utf-8")
    but.reset_cache()


def _enable(tmp_path: Path) -> None:
    _write_cfg(tmp_path, "[code_e2e]\nbrowser_use_enabled = true\n")


@pytest.fixture
def fake_browser_use(monkeypatch: pytest.MonkeyPatch):
    """Inject a fake ``browser_use`` package so the worker's lazy import
    succeeds without the real dependency / Chromium."""
    calls: dict[str, object] = {}

    class _FakeHistory:
        def final_result(self) -> str:
            return "E2E PASSED: reply bubble appeared"

    class _FakeAgent:
        def __init__(self, task: str, llm: object, **kw: object) -> None:
            calls["task"] = task
            calls["llm"] = llm

        async def run(self, max_steps: int = 40) -> _FakeHistory:
            calls["max_steps"] = max_steps
            return _FakeHistory()

    class _FakeChatOpenAI:
        def __init__(self, model: str, base_url: str, api_key: str, **kw):
            calls["model"] = model
            calls["base_url"] = base_url
            calls["api_key"] = api_key

    pkg = types.ModuleType("browser_use")
    pkg.Agent = _FakeAgent  # type: ignore[attr-defined]
    llm_mod = types.ModuleType("browser_use.llm")
    llm_mod.ChatOpenAI = _FakeChatOpenAI  # type: ignore[attr-defined]
    pkg.llm = llm_mod  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "browser_use", pkg)
    monkeypatch.setitem(sys.modules, "browser_use.llm", llm_mod)
    return calls


def _dispatch(args: dict) -> dict:
    return json.loads(registry.dispatch("run_browser_task", args))


def _wait_for_terminal(job_id: str, timeout: float = 5.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        res = _dispatch({"action": "result", "job_id": job_id})
        if res.get("status") in {"done", "error"} or res.get("ok") is False and res.get("status") != "running":
            return res
        if res.get("status") != "running":
            return res
        time.sleep(0.05)
    return _dispatch({"action": "result", "job_id": job_id})


# ---------------------------------------------------------------------
# Registration + flag-off
# ---------------------------------------------------------------------
def test_tool_is_registered():
    assert "run_browser_task" in registry.list_tools()
    spec = registry.get("run_browser_task")
    assert spec is not None and spec.toolset == "e2e"


def test_flag_off_returns_disabled(tmp_path: Path):
    # No [code_e2e] section → default OFF.
    res = _dispatch({"task": "open localhost and click Send"})
    assert res["ok"] is False
    assert res["error"] == "disabled"
    assert "browser_use_enabled" in res["hint"]


def test_flag_off_via_explicit_false(tmp_path: Path):
    _write_cfg(tmp_path, "[code_e2e]\nbrowser_use_enabled = false\n")
    res = _dispatch({"task": "x"})
    assert res["ok"] is False and res["error"] == "disabled"


# ---------------------------------------------------------------------
# Localhost enforcement
# ---------------------------------------------------------------------
def test_external_url_rejected(tmp_path: Path, fake_browser_use):
    _enable(tmp_path)
    res = _dispatch(
        {"task": "do thing", "start_url": "https://example.com/login"}
    )
    assert res["ok"] is False
    assert "non-local" in res["error"]
    assert "allow_external" in res["hint"]


def test_external_url_allowed_with_flag(tmp_path: Path, fake_browser_use):
    _enable(tmp_path)
    _write_cfg(
        tmp_path,
        "[code_e2e]\nbrowser_use_enabled = true\napi_key = 'k-test'\n",
    )
    res = _dispatch(
        {
            "task": "do thing",
            "start_url": "https://example.com",
            "allow_external": True,
        }
    )
    assert res["ok"] is True and res["status"] == "running"


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:5173",
        "127.0.0.1:8100",
        "http://[::1]:3000",
        "http://app.localhost:5173",
        "",  # empty → model opens dev server itself
    ],
)
def test_local_urls_accepted(tmp_path: Path, fake_browser_use, url: str):
    _write_cfg(
        tmp_path,
        "[code_e2e]\nbrowser_use_enabled = true\napi_key = 'k-test'\n",
    )
    res = _dispatch({"task": "verify reply", "start_url": url})
    assert res["ok"] is True, res
    assert res["status"] == "running"
    assert "job_id" in res


# ---------------------------------------------------------------------
# Fire-and-quick-return async shape + result polling
# ---------------------------------------------------------------------
def test_start_returns_running_then_done(tmp_path: Path, fake_browser_use):
    _write_cfg(
        tmp_path,
        "[code_e2e]\nbrowser_use_enabled = true\napi_key = 'k-test'\n",
    )
    started = _dispatch(
        {"task": "open localhost:5173, send a message, verify reply"}
    )
    assert started["ok"] is True
    assert started["status"] == "running"
    job_id = started["job_id"]
    assert job_id

    final = _wait_for_terminal(job_id)
    assert final["ok"] is True
    assert final["status"] == "done"
    assert "E2E PASSED" in final["result"]
    # The fake Agent saw the localhost guard injected into the prompt.
    assert "localhost" in str(fake_browser_use["task"]).lower()


def test_task_required_when_enabled(tmp_path: Path, fake_browser_use):
    _enable(tmp_path)
    res = _dispatch({"task": "   "})
    assert res["ok"] is False
    assert "task is required" in res["error"]


def test_missing_api_key_blocks_start(tmp_path: Path, fake_browser_use):
    # Enabled but no api_key anywhere (config / runtime / env).
    _enable(tmp_path)
    res = _dispatch({"task": "verify something"})
    assert res["ok"] is False
    assert "api_key" in res["error"]


def test_result_unknown_job(tmp_path: Path):
    _write_cfg(
        tmp_path,
        "[code_e2e]\nbrowser_use_enabled = true\napi_key = 'k-test'\n",
    )
    res = _dispatch({"action": "result", "job_id": "deadbeef"})
    assert res["ok"] is False
    assert "unknown job_id" in res["error"]


# ---------------------------------------------------------------------
# Creds resolution
# ---------------------------------------------------------------------
def test_llm_runtime_json_overrides_config(tmp_path: Path):
    _write_cfg(
        tmp_path,
        "[code_e2e]\nbrowser_use_enabled = true\n"
        "base_url = 'https://cfg.example/v1'\napi_key = 'cfg-key'\n"
        "model = 'cfg-model'\n",
    )
    (tmp_path / "llm_runtime.json").write_text(
        json.dumps(
            {
                "base_url": "https://chinzy.com/v1",
                "model": "gpt-5.5",
                "api_key": "runtime-key",
            }
        ),
        encoding="utf-8",
    )
    creds = but._resolve_creds()
    assert creds["base_url"] == "https://chinzy.com/v1"
    assert creds["model"] == "gpt-5.5"
    assert creds["api_key"] == "runtime-key"


def test_creds_default_to_chinzy(tmp_path: Path):
    _write_cfg(tmp_path, "[code_e2e]\nbrowser_use_enabled = true\n")
    creds = but._resolve_creds()
    assert creds["base_url"] == "https://chinzy.com/v1"
    assert creds["model"] == "gpt-5.5"
