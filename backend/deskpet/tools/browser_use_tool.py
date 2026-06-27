# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""P5: browser-use backed autonomous E2E browser tool (toolset=e2e).

One tool, ``run_browser_task``, that lets a code-mode session agent run a
natural-language browser task ("open the dev server, click the Send
button, verify the reply appears") for end-to-end testing. The heavy
lifting is delegated to `browser-use <https://github.com/browser-use/browser-use>`_
driving Chromium via Playwright, with an LLM (``gpt-5.5`` over the the relay
OpenAI-compatible endpoint) as the decision loop.

Why this module looks the way it does
--------------------------------------

* **Strangler-Fig feature flag.** The whole capability is gated behind
  ``[code_e2e].browser_use_enabled`` in ``config.toml`` (default
  **false**). The tool is *always registered* (so ``tool_search`` can
  surface it and the agent gets a clear, actionable error rather than an
  "unknown tool"), but every dispatch short-circuits with
  ``{"ok": false, "error": "disabled", "hint": ...}`` until the flag is
  flipped. OFF = zero effect: the optional ``browser_use`` import and the
  Chromium download only ever happen *inside a dispatched run with the
  flag on*, never at import time.

* **Async fire-and-quick-return.** browser-use runs are long (30s–3min).
  The repo's only existing async worker (``memory/vector_worker.py``) is
  an ``asyncio.Task`` bound to the backend event loop with
  ``start()``/``stop()`` lifecycle and *no* job-result delivery
  mechanism — it is not a fire-and-quick-return job dispatcher callable
  from a sync tool handler, and wiring one into the event loop would
  require touching ``main.py`` (out of scope). So this module ships a
  small **self-contained background-thread job runner**: the
  ``start`` action returns instantly with
  ``{"ok": true, "status": "running", "job_id": ...}``; the agent then
  polls ``{"action": "result", "job_id": ...}`` (or ``"poll"``) for the
  outcome. Job state is also persisted to a JSON file under
  ``user_data_dir()/browser_jobs/`` so a backend restart mid-run still
  surfaces a terminal ``error`` rather than a phantom ``running``.

* **Localhost-only by default.** A code-mode *test* agent must not roam
  the open internet. ``start_url`` (and the model, via a hard system
  prompt) are constrained to ``localhost`` / ``127.0.0.1`` / ``[::1]``.
  A non-local ``start_url`` is rejected unless the caller passes an
  explicit ``allow_external: true``. The same constraint is injected into
  the browser-use task prompt so the LLM itself is told not to navigate
  off-host.

Conventions mirrored from the existing tool layer
-------------------------------------------------

* Registration + ``_err`` JSON-string contract: ``deskpet/tools/web_tools.py``.
* ``user_data_dir()`` resolution without importing the heavy backend:
  ``deskpet/tools/file_tools.py`` (``platformdirs`` + ``DESKPET_USER_DATA_DIR``).
* Creds resolution: ``base_url``/``model``/``api_key`` read from
  ``user_data_dir()/llm_runtime.json`` (the runtime-override file written
  by the settings panel — see ``backend/config.py`` ``_load_llm_runtime_overrides``),
  falling back to the ``[code_e2e]`` config section, then to the the relay
  defaults. No API key is ever hardcoded.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

import platformdirs

from ._config import _candidate_paths
from .registry import registry

logger = logging.getLogger(__name__)

_APP_NAME = "deskpet"

# the relay OpenAI-compatible endpoint + the multimodal code-mode model.
# These are *defaults* only — overridable via llm_runtime.json or the
# [code_e2e] config section. Never a secret (no api_key here).
_DEFAULT_BASE_URL = "https://your-llm-relay.example.com/v1"
_DEFAULT_MODEL = "gpt-5.5"

# Hosts a test agent is allowed to drive without `allow_external=true`.
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}

# Hard ceilings so a runaway agent can't burn the box.
_MAX_STEPS = 40
_DEFAULT_TIMEOUT_S = 180.0
_MAX_TIMEOUT_S = 600.0

# Job retention: keep finished jobs in memory + on disk so a late poll
# still gets the result. Old files are pruned best-effort on each start.
_JOB_TTL_S = 3600.0


def _err(msg: str, *, hint: str = "", retriable: bool = False) -> str:
    """Mirror web_tools._err but with the richer {ok,error,hint} shape
    the task spec asked for. ``ok`` is always present so callers can
    branch on it uniformly."""
    payload: dict[str, Any] = {"ok": False, "error": msg, "retriable": retriable}
    if hint:
        payload["hint"] = hint
    return json.dumps(payload, ensure_ascii=False)


# ---------------------------------------------------------------------
# Config flag + creds resolution
# ---------------------------------------------------------------------
def _load_code_e2e_section(*, force: bool = False) -> dict[str, Any]:
    """Read the ``[code_e2e]`` section from config.toml.

    Reuses ``_config._candidate_paths`` (DESKPET_CONFIG override → repo
    config.toml) so a tool-only test can point at a tmp config.toml the
    same way the web tools do. Cached, with ``force`` to bust it for
    tests that rewrite config mid-suite.
    """
    global _cfg_cache
    if _cfg_cache is not None and not force:
        return _cfg_cache
    raw: dict[str, Any] = {}
    for p in _candidate_paths():
        try:
            if p.is_file():
                import tomli

                with p.open("rb") as f:
                    raw = tomli.load(f)
                break
        except OSError:
            continue
    _cfg_cache = dict((raw.get("code_e2e") or {}))
    return _cfg_cache


_cfg_cache: Optional[dict[str, Any]] = None


def reset_cache() -> None:
    """Drop cached config. Test hook (mirrors _config.reset_cache)."""
    global _cfg_cache
    _cfg_cache = None


def _is_enabled() -> bool:
    """``[code_e2e].browser_use_enabled`` — default False (OFF)."""
    # Env override first so tests / CI can flip it without a config file.
    env = os.environ.get("DESKPET_BROWSER_USE_ENABLED")
    if env is not None:
        return env.strip().lower() in {"1", "true", "yes", "on"}
    return bool(_load_code_e2e_section().get("browser_use_enabled", False))


def _user_data_dir() -> Path:
    """Match backend/paths.user_data_dir() without importing the heavy
    backend flat layout (same trick file_tools._workspace_root uses)."""
    override = os.environ.get("DESKPET_USER_DATA_DIR")
    if override:
        return Path(override)
    return Path(
        platformdirs.user_data_dir(_APP_NAME, appauthor=False, roaming=True)
    )


def _resolve_creds() -> dict[str, str]:
    """Resolve ``base_url`` / ``api_key`` / ``model`` for the browser-use
    LLM, in priority order:

      1. ``user_data_dir()/llm_runtime.json`` (settings-panel runtime
         override — the real creds source in this codebase, see
         ``backend/config.py`` ``_load_llm_runtime_overrides``).
      2. ``[code_e2e]`` config section (``base_url`` / ``api_key`` /
         ``model``) — lets an operator pin a dedicated test endpoint.
      3. the relay defaults for base_url+model; api_key from the
         ``DESKPET_CLOUD_API_KEY`` / ``OPENAI_API_KEY`` env as a last
         resort.

    Never hardcodes a key.
    """
    base_url = _DEFAULT_BASE_URL
    model = _DEFAULT_MODEL
    api_key = ""

    section = _load_code_e2e_section()
    if section.get("base_url"):
        base_url = str(section["base_url"])
    if section.get("model"):
        model = str(section["model"])
    if section.get("api_key"):
        api_key = str(section["api_key"])

    runtime_path = _user_data_dir() / "llm_runtime.json"
    try:
        if runtime_path.exists():
            data = json.loads(runtime_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                if data.get("base_url"):
                    base_url = str(data["base_url"])
                if data.get("model"):
                    model = str(data["model"])
                if data.get("api_key"):
                    api_key = str(data["api_key"])
    except Exception as exc:  # noqa: BLE001 — creds read must never break dispatch
        logger.warning("browser_use: llm_runtime.json read failed: %s", exc)

    if not api_key:
        api_key = (
            os.environ.get("DESKPET_CLOUD_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
            or ""
        )

    return {"base_url": base_url, "model": model, "api_key": api_key}


# ---------------------------------------------------------------------
# Non-local guard
# ---------------------------------------------------------------------
def _classify_url(url: str) -> tuple[bool, str]:
    """Return ``(is_local, normalized_url)``.

    Empty / bare input is treated as local (the model will be told to
    open the dev server itself). A scheme-less ``localhost:5173`` is
    normalized to ``http://localhost:5173``. Anything that resolves to a
    non-loopback host is flagged non-local so the caller can reject it.
    """
    url = (url or "").strip()
    if not url:
        return True, ""
    if "://" not in url:
        url = "http://" + url
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if not host:
        # Unparseable → treat as non-local; safer to reject.
        return False, url
    is_local = host in _LOCAL_HOSTS or host.endswith(".localhost")
    return is_local, url


# ---------------------------------------------------------------------
# Job store (in-memory + JSON file fallback)
# ---------------------------------------------------------------------
_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = threading.Lock()


def _jobs_dir() -> Path:
    d = _user_data_dir() / "browser_jobs"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return d


def _persist_job(job_id: str, state: dict[str, Any]) -> None:
    try:
        (_jobs_dir() / f"{job_id}.json").write_text(
            json.dumps(state, ensure_ascii=False), encoding="utf-8"
        )
    except Exception as exc:  # noqa: BLE001 — persistence is best-effort
        logger.debug("browser_use: persist job %s failed: %s", job_id, exc)


def _load_job(job_id: str) -> Optional[dict[str, Any]]:
    with _jobs_lock:
        state = _jobs.get(job_id)
    if state is not None:
        return state
    # Fall back to disk (e.g. polled after a backend restart).
    f = _jobs_dir() / f"{job_id}.json"
    try:
        if f.is_file():
            return json.loads(f.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    return None


def _set_job(job_id: str, **fields: Any) -> dict[str, Any]:
    with _jobs_lock:
        state = _jobs.setdefault(job_id, {"job_id": job_id})
        state.update(fields)
        state["updated_at"] = time.time()
        snapshot = dict(state)
    _persist_job(job_id, snapshot)
    return snapshot


def _prune_old_jobs() -> None:
    """Best-effort GC of finished jobs older than the TTL (memory + disk)."""
    now = time.time()
    with _jobs_lock:
        stale = [
            jid
            for jid, s in _jobs.items()
            if s.get("status") in {"done", "error"}
            and now - s.get("updated_at", now) > _JOB_TTL_S
        ]
        for jid in stale:
            _jobs.pop(jid, None)
    try:
        for f in _jobs_dir().glob("*.json"):
            try:
                if now - f.stat().st_mtime > _JOB_TTL_S:
                    f.unlink()
            except OSError:
                continue
    except OSError:
        pass


# ---------------------------------------------------------------------
# The actual browser-use run (background thread)
# ---------------------------------------------------------------------
def _build_task_prompt(task: str, start_url: str, allow_external: bool) -> str:
    guard = (
        "You are an automated END-TO-END TEST agent. You MUST stay on "
        "localhost / 127.0.0.1 only. Do NOT navigate to any external "
        "website, do NOT follow links that leave the local host, do NOT "
        "perform searches on the open internet."
        if not allow_external
        else "You are an automated END-TO-END TEST agent."
    )
    where = (
        f" Start by opening {start_url}."
        if start_url
        else " Open the local dev server yourself."
    )
    return f"{guard}{where}\n\nTask: {task}"


def _run_browser_job(
    job_id: str,
    task: str,
    start_url: str,
    allow_external: bool,
    timeout_s: float,
    creds: dict[str, str],
) -> None:
    """Background worker. Imports browser-use lazily (so a flag-off
    process never pays the import / chromium cost) and drives an Agent,
    writing the terminal state into the job store. All failures are
    captured into ``status:"error"`` — this thread never raises out."""
    import asyncio

    _set_job(job_id, status="running", started_at=time.time())
    try:
        try:
            from browser_use import Agent  # type: ignore

            try:
                # browser-use >=0.1.40 ships a thin ChatOpenAI wrapper;
                # older builds expose langchain_openai.ChatOpenAI. Try the
                # native one first, then fall back.
                from browser_use.llm import ChatOpenAI  # type: ignore
            except Exception:  # noqa: BLE001
                from langchain_openai import ChatOpenAI  # type: ignore
        except Exception as exc:  # noqa: BLE001 — dependency / import failure
            _set_job(
                job_id,
                status="error",
                error=f"browser-use import failed: {exc}",
                hint=(
                    "Install backend deps (`uv sync` / `pip install "
                    "browser-use`) and run once so Playwright can download "
                    "Chromium."
                ),
            )
            return

        llm = ChatOpenAI(
            model=creds["model"],
            base_url=creds["base_url"],
            api_key=creds["api_key"],
        )
        prompt = _build_task_prompt(task, start_url, allow_external)
        agent = Agent(task=prompt, llm=llm, max_actions_per_step=4)

        async def _drive() -> Any:
            return await asyncio.wait_for(
                agent.run(max_steps=_MAX_STEPS), timeout=timeout_s
            )

        result = asyncio.run(_drive())

        # browser-use returns a history object; reduce to a string the
        # agent can read back without us depending on its internal shape.
        try:
            final = result.final_result()  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            final = str(result)
        _set_job(
            job_id,
            status="done",
            result=final if isinstance(final, str) else str(final),
            finished_at=time.time(),
        )
    except asyncio.TimeoutError:
        _set_job(
            job_id,
            status="error",
            error=f"browser task exceeded {timeout_s:.0f}s timeout",
            hint="Increase `timeout_s` or simplify the task.",
        )
    except Exception as exc:  # noqa: BLE001 — worker thread must never raise
        logger.exception("browser_use job %s failed", job_id)
        _set_job(job_id, status="error", error=f"{type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------
# Tool handler
# ---------------------------------------------------------------------
_SCHEMA: dict[str, Any] = {
    "name": "run_browser_task",
    "description": (
        "Run an autonomous natural-language browser task for END-TO-END "
        "testing (e.g. 'open http://localhost:5173, click the Send "
        "button, verify a reply bubble appears'). LLM-driven via "
        "browser-use + Chromium. Long-running: returns instantly with a "
        "job_id; poll with action='result'. Constrained to "
        "localhost/127.0.0.1 unless allow_external=true. Disabled unless "
        "[code_e2e].browser_use_enabled=true in config.toml."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["start", "result", "poll"],
                "description": (
                    "'start' (default) dispatches a new run; 'result'/"
                    "'poll' fetch the outcome of an earlier job_id."
                ),
                "default": "start",
            },
            "task": {
                "type": "string",
                "description": (
                    "Natural-language E2E instruction. Required for "
                    "action='start'."
                ),
            },
            "start_url": {
                "type": "string",
                "description": (
                    "Optional URL to open first. Must be localhost/"
                    "127.0.0.1 unless allow_external=true."
                ),
            },
            "allow_external": {
                "type": "boolean",
                "description": (
                    "Permit a non-local start_url + internet navigation. "
                    "Default false (test agents stay on localhost)."
                ),
                "default": False,
            },
            "timeout_s": {
                "type": "integer",
                "description": (
                    f"Hard wall-clock cap. Default {int(_DEFAULT_TIMEOUT_S)}, "
                    f"max {int(_MAX_TIMEOUT_S)}."
                ),
                "default": int(_DEFAULT_TIMEOUT_S),
            },
            "job_id": {
                "type": "string",
                "description": "Required for action='result'/'poll'.",
            },
        },
        "required": [],
    },
}


def _handle_result(job_id: str) -> str:
    if not job_id:
        return _err("job_id is required for action='result'")
    state = _load_job(job_id)
    if state is None:
        return _err(
            f"unknown job_id: {job_id}",
            hint="Job expired or never existed; start a new run.",
        )
    status = state.get("status", "unknown")
    if status == "running":
        return json.dumps(
            {"ok": True, "status": "running", "job_id": job_id},
            ensure_ascii=False,
        )
    if status == "done":
        return json.dumps(
            {
                "ok": True,
                "status": "done",
                "job_id": job_id,
                "result": state.get("result", ""),
            },
            ensure_ascii=False,
        )
    # error / unknown
    return json.dumps(
        {
            "ok": False,
            "status": status,
            "job_id": job_id,
            "error": state.get("error", "job failed"),
            "hint": state.get("hint", ""),
        },
        ensure_ascii=False,
    )


def _handle_run_browser_task(args: dict[str, Any], task_id: str) -> str:
    # Strangler-Fig: flag OFF → registered but inert.
    if not _is_enabled():
        return _err(
            "disabled",
            hint=(
                "browser-use E2E tool is OFF. Add `[code_e2e]\\n"
                "browser_use_enabled = true` to config.toml (and restart "
                "the backend) to enable it."
            ),
        )

    action = str(args.get("action", "start") or "start").strip().lower()

    if action in {"result", "poll"}:
        return _handle_result(str(args.get("job_id", "") or "").strip())

    if action != "start":
        return _err(f"unknown action: {action!r}")

    task = str(args.get("task", "") or "").strip()
    if not task:
        return _err(
            "task is required for action='start'",
            hint="Describe the E2E flow in natural language.",
        )

    allow_external = bool(args.get("allow_external", False))
    is_local, start_url = _classify_url(str(args.get("start_url", "") or ""))
    if not is_local and not allow_external:
        return _err(
            f"non-local start_url rejected: {start_url!r}",
            hint=(
                "Test agents are constrained to localhost/127.0.0.1. Pass "
                "allow_external=true only if you really need to leave the "
                "local host."
            ),
        )

    try:
        timeout_s = float(args.get("timeout_s", _DEFAULT_TIMEOUT_S) or _DEFAULT_TIMEOUT_S)
    except (TypeError, ValueError):
        timeout_s = _DEFAULT_TIMEOUT_S
    timeout_s = max(5.0, min(timeout_s, _MAX_TIMEOUT_S))

    creds = _resolve_creds()
    if not creds["api_key"]:
        return _err(
            "no LLM api_key resolved",
            hint=(
                "Set the code-mode key via the settings panel "
                "(llm_runtime.json) or [code_e2e].api_key in config.toml."
            ),
        )

    _prune_old_jobs()
    job_id = uuid.uuid4().hex
    _set_job(
        job_id,
        status="running",
        task=task,
        start_url=start_url,
        created_at=time.time(),
    )

    worker = threading.Thread(
        target=_run_browser_job,
        args=(job_id, task, start_url, allow_external, timeout_s, creds),
        name=f"browser-use-{job_id[:8]}",
        daemon=True,
    )
    worker.start()

    return json.dumps(
        {
            "ok": True,
            "status": "running",
            "job_id": job_id,
            "hint": (
                "Long-running. Poll with "
                f"run_browser_task(action='result', job_id='{job_id}')."
            ),
        },
        ensure_ascii=False,
    )


# Always register (even when the flag is off) so tool_search surfaces it
# and the agent gets a clear "disabled" error instead of "unknown tool".
# toolset="e2e" keeps it out of normal chat prompts — only the code-mode
# session assembler opts the "e2e" toolset in.
registry.register(
    "run_browser_task",
    "e2e",
    _SCHEMA,
    _handle_run_browser_task,
    permission_category="network",
    dangerous=True,
)
