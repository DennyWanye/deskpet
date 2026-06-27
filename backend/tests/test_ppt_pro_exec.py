# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
import pytest_asyncio

from deskpet.tools import ppt_tools
from deskpet.tools.ppt_tools import SlideOutline


@pytest_asyncio.fixture(autouse=True)
async def _clean_ppt_pro_state():
    old_ctx = dict(getattr(ppt_tools, "_PPT_PRO_CTX", {}))
    for task in list(getattr(ppt_tools, "_PPT_PRO_RUNNING", {}).values()):
        task.cancel()
    await asyncio.sleep(0)
    getattr(ppt_tools, "_PPT_PRO_TASKS", set()).clear()
    getattr(ppt_tools, "_PPT_PRO_RUNNING", {}).clear()
    getattr(ppt_tools, "_PPT_PRO_RUNNING_TOPIC", {}).clear()
    yield
    for task in list(getattr(ppt_tools, "_PPT_PRO_RUNNING", {}).values()):
        task.cancel()
    for task in list(getattr(ppt_tools, "_PPT_PRO_TASKS", set())):
        task.cancel()
    await asyncio.gather(
        *list(getattr(ppt_tools, "_PPT_PRO_TASKS", set())),
        return_exceptions=True,
    )
    getattr(ppt_tools, "_PPT_PRO_TASKS", set()).clear()
    getattr(ppt_tools, "_PPT_PRO_RUNNING", {}).clear()
    getattr(ppt_tools, "_PPT_PRO_RUNNING_TOPIC", {}).clear()
    if hasattr(ppt_tools, "_PPT_PRO_CTX"):
        ppt_tools._PPT_PRO_CTX.clear()
        ppt_tools._PPT_PRO_CTX.update(old_ctx)


def _slides() -> list[SlideOutline]:
    return [
        SlideOutline(
            layout="image_full",
            title="Cover",
            subtitle="Sub",
            bullets=["one", "two", "three"],
            image_prompt="cover prompt",
        ),
        SlideOutline(
            layout="image_full",
            title="Insight",
            bullets=["alpha", "beta", "gamma"],
            image_prompt="insight prompt",
        ),
    ]


def test_render_pro_probe_false_uses_template_and_skips_image_gen(monkeypatch):
    calls: list[tuple[object, dict]] = []
    monkeypatch.setattr(ppt_tools, "probe_image_reachable", lambda *, timeout_s: False, raising=False)
    # Template fallback uses the deterministic bundled-template path (bug#2 fix),
    # not the legacy _default_template / vision picker.
    monkeypatch.setattr(ppt_tools, "_fallback_template_path", lambda: "tpl")
    monkeypatch.setattr(ppt_tools, "_disk_preflight", lambda **_k: None)

    def fake_ppt_create(outline, **kwargs):
        calls.append((outline, kwargs))
        return {"ok": True, "path": "deck.pptx", "artifacts": []}

    monkeypatch.setattr(ppt_tools, "ppt_create", fake_ppt_create)
    messages: list[str] = []

    result = ppt_tools._render_pro(
        _slides(),
        theme="minimal",
        title="Deck",
        author="Tester",
        output_path=None,
        image_mode=True,
        probe_timeout_s=0.01,
        notify=messages.append,
    )

    assert result["ok"] is True
    outline, kwargs = calls[0]
    assert kwargs["skip_image_gen"] is True
    assert kwargs["template"] == "tpl"
    # _render_pro passes asdict() dicts to ppt_create (bug#2-b fix), not SlideOutline.
    assert all(slide["image_prompt"] is None for slide in outline)
    assert messages


@pytest.mark.parametrize("error_kind", ["connectivity", "model_unavailable"])
def test_autofill_first_image_connectivity_or_model_unavailable_falls_back(monkeypatch, error_kind):
    calls = []

    def fake_generate(prompts, *, size, model=None):
        calls.append(list(prompts))
        return [{"prompt": prompts[0], "path": None, "error": "nope", "error_kind": error_kind}]

    monkeypatch.setattr(ppt_tools, "generate_images", fake_generate, raising=False)

    reachable, n_ok = ppt_tools._autofill_with_connectivity_gate(_slides())

    assert (reachable, n_ok) == (False, 0)
    assert len(calls) == 1


def test_autofill_first_image_content_failure_continues(monkeypatch):
    calls = []

    def fake_generate(prompts, *, size, model=None):
        calls.append(list(prompts))
        if len(calls) == 1:
            return [{"prompt": prompts[0], "path": None, "error": "blocked", "error_kind": "content"}]
        return [{"prompt": prompts[0], "path": "ok.png", "error": None, "error_kind": None}]

    slides = _slides()
    monkeypatch.setattr(ppt_tools, "generate_images", fake_generate, raising=False)

    reachable, n_ok = ppt_tools._autofill_with_connectivity_gate(slides)

    assert (reachable, n_ok) == (True, 1)
    assert slides[0].image_path is None
    assert slides[1].image_path == "ok.png"
    assert len(calls) == 2


def test_autofill_all_images_fail_returns_fallback(monkeypatch):
    def fake_generate(prompts, *, size, model=None):
        return [{"prompt": p, "path": None, "error": "blocked", "error_kind": "content"} for p in prompts]

    monkeypatch.setattr(ppt_tools, "generate_images", fake_generate, raising=False)

    assert ppt_tools._autofill_with_connectivity_gate(_slides()) == (False, 0)


def test_render_pro_all_success_uses_image_path_without_second_generation(monkeypatch):
    monkeypatch.setattr(ppt_tools, "probe_image_reachable", lambda *, timeout_s: True, raising=False)

    def fake_generate(prompts, *, size, model=None):
        return [{"prompt": p, "path": f"{i}.png", "error": None, "error_kind": None} for i, p in enumerate(prompts)]

    calls: list[tuple[object, dict]] = []
    monkeypatch.setattr(ppt_tools, "generate_images", fake_generate, raising=False)
    monkeypatch.setattr(ppt_tools, "_disk_preflight", lambda **_k: None)
    monkeypatch.setattr(ppt_tools, "ppt_create", lambda outline, **kwargs: calls.append((outline, kwargs)) or {"ok": True})

    result = ppt_tools._render_pro(
        _slides(),
        theme="minimal",
        title="Deck",
        author="Tester",
        output_path=None,
        image_mode=True,
        probe_timeout_s=0.01,
        notify=lambda _msg: None,
    )

    assert result["ok"] is True
    outline, kwargs = calls[0]
    assert kwargs["skip_image_gen"] is True
    assert "template" not in kwargs or kwargs["template"] is None
    # outline items are asdict() dicts (bug#2-b), not SlideOutline objects.
    assert [s["image_path"] for s in outline] == ["0.png", "0.png"]


def test_disk_preflight_blocks_when_low_and_passes_when_enough(monkeypatch):
    # Low free space → clear user-facing error.
    monkeypatch.setattr(ppt_tools, "_drive_free_bytes", lambda _p: 10 * 1024 * 1024)
    msg = ppt_tools._disk_preflight(image_mode=True, pages=8)
    assert msg is not None and "磁盘空间不足" in msg
    # Plenty of space → no block.
    monkeypatch.setattr(ppt_tools, "_drive_free_bytes", lambda _p: 50 * 1024 * 1024 * 1024)
    assert ppt_tools._disk_preflight(image_mode=True, pages=8) is None
    # Unknown free space (probe failed) → must never block a valid render.
    monkeypatch.setattr(ppt_tools, "_drive_free_bytes", lambda _p: None)
    assert ppt_tools._disk_preflight(image_mode=True, pages=8) is None


def test_render_pro_aborts_early_on_low_disk(monkeypatch):
    monkeypatch.setattr(ppt_tools, "_disk_preflight", lambda **_k: "磁盘空间不足：C:\\ 仅剩 5MB，请清理后重试。")
    called = {"ppt_create": 0, "probe": 0}
    monkeypatch.setattr(ppt_tools, "ppt_create", lambda *a, **k: called.__setitem__("ppt_create", called["ppt_create"] + 1) or {"ok": True})
    monkeypatch.setattr(ppt_tools, "probe_image_reachable", lambda *, timeout_s: called.__setitem__("probe", called["probe"] + 1) or True, raising=False)

    result = ppt_tools._render_pro(
        _slides(), theme="minimal", title="D", author="T",
        output_path=None, image_mode=True, probe_timeout_s=0.01, notify=lambda _m: None,
    )

    assert result["ok"] is False
    assert "磁盘空间不足" in result["error"]
    assert called["ppt_create"] == 0  # aborted before any render
    assert called["probe"] == 0       # aborted before image probe too


def test_render_pro_partial_image_failure_notifies(monkeypatch):
    monkeypatch.setattr(ppt_tools, "probe_image_reachable", lambda *, timeout_s: True, raising=False)
    monkeypatch.setattr(ppt_tools, "_disk_preflight", lambda **_k: None)
    seq = {"n": 0}

    def fake_generate(prompts, *, size, model=None):
        seq["n"] += 1
        if seq["n"] == 1:  # first image (connectivity gate) succeeds
            return [{"prompt": prompts[0], "path": "cover.png", "error": None, "error_kind": None}]
        return [{"prompt": prompts[0], "path": None, "error": "blocked", "error_kind": "content"}]

    monkeypatch.setattr(ppt_tools, "generate_images", fake_generate, raising=False)
    monkeypatch.setattr(ppt_tools, "ppt_create", lambda outline, **kwargs: {"ok": True, "path": "deck.pptx"})
    messages: list[str] = []

    result = ppt_tools._render_pro(
        _slides(), theme="minimal", title="D", author="T",
        output_path=None, image_mode=True, probe_timeout_s=0.01, notify=messages.append,
    )

    assert result["ok"] is True
    # Reachable (gate passed) but 1 of 2 images failed → user is told, not silent.
    assert any("没生成成功" in m for m in messages)
    assert any("1/2" in m for m in messages)


@pytest.mark.asyncio
async def test_report_done_open_failure_tells_user_the_path(monkeypatch):
    """No associated app (e.g. no WPS/PowerPoint) → _open_image_file returns
    False → message must not claim '已自动打开', must give the saved path."""
    monkeypatch.setattr(ppt_tools, "_open_image_file", lambda path: False)
    msgs: list[str] = []

    async def notifier(_sid, message):
        msgs.append(message)

    await ppt_tools._ppt_pro_report_done(
        {"ok": True, "path": "C:/x/deck.pptx", "artifacts": []},
        notifier=notifier, session_id="s",
    )

    assert any("已保存到" in m for m in msgs)
    assert all("已自动打开" not in m for m in msgs)


@pytest.mark.asyncio
async def test_report_done_open_success_says_opened(monkeypatch):
    monkeypatch.setattr(ppt_tools, "_open_image_file", lambda path: True)
    msgs: list[str] = []

    async def notifier(_sid, message):
        msgs.append(message)

    await ppt_tools._ppt_pro_report_done(
        {"ok": True, "path": "C:/x/deck.pptx", "artifacts": []},
        notifier=notifier, session_id="s",
    )

    assert any("已自动打开" in m for m in msgs)


def test_degrade_to_template_clears_image_fields_and_keeps_content():
    degraded = ppt_tools._degrade_to_template(_slides())

    assert degraded[0].layout in {"bullet", "section"}
    assert degraded[0].title == "Cover"
    assert degraded[0].subtitle == "Sub"
    assert degraded[0].bullets == ["one", "two", "three"]
    assert all(s.image_prompt is None and s.image_path is None for s in degraded)


def test_ppt_create_explicit_skip_false_preserves_default_image_autofill(monkeypatch, tmp_path):
    called = {"autofill": 0}

    monkeypatch.setattr(ppt_tools, "_HAS_PPTX", True)
    monkeypatch.setattr(ppt_tools, "_autofill_image_prompts", lambda slides: called.__setitem__("autofill", called["autofill"] + 1))
    monkeypatch.setattr(
        ppt_tools,
        "_render_fromscratch",
        lambda slides, theme_obj, out_path, **kwargs: {"ok": True, "path": str(out_path), "artifacts": []},
    )
    monkeypatch.setattr(ppt_tools, "_visual_review_loop", lambda *a, **k: None)
    monkeypatch.setattr(ppt_tools, "_maybe_render_preview", lambda result: None)

    result = ppt_tools.ppt_create(
        [{"layout": "image_full", "title": "T", "image_prompt": "draw it"}],
        output_path=str(tmp_path / "deck.pptx"),
        skip_image_gen=False,
    )

    assert result["ok"] is True
    assert called["autofill"] == 1


@pytest.mark.asyncio
async def test_handle_ppt_pro_returns_immediately_and_starts_task(monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()

    async def fake_orchestrate(**kwargs):
        started.set()
        await release.wait()

    notices: list[tuple[str, str]] = []
    ppt_tools.set_ppt_pro_services(
        outline_propose=lambda *a, **k: None,
        notifier=lambda sid, msg: notices.append((sid, msg)),
        run_blocking=lambda fn: fn(),
    )
    monkeypatch.setattr(ppt_tools, "_ppt_pro_orchestrate", fake_orchestrate)

    out = await ppt_tools._handle_ppt_pro({"topic": "AI agents", "_session_id": "s1"}, "task")

    assert out["status"] == "researching"
    await asyncio.wait_for(started.wait(), timeout=1)
    assert "s1" in ppt_tools._PPT_PRO_RUNNING
    release.set()


@pytest.mark.asyncio
async def test_handle_ppt_pro_dedupes_same_topic_and_cancels_different_topic(monkeypatch):
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def fake_orchestrate(**kwargs):
        started.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    notices: list[tuple[str, str]] = []

    async def notifier(sid, msg):
        notices.append((sid, msg))

    ppt_tools.set_ppt_pro_services(
        outline_propose=lambda *a, **k: None,
        notifier=notifier,
        run_blocking=lambda fn: fn(),
    )
    monkeypatch.setattr(ppt_tools, "_ppt_pro_orchestrate", fake_orchestrate)

    first = await ppt_tools._handle_ppt_pro({"topic": "Topic A", "_session_id": "s1"}, "task")
    await asyncio.wait_for(started.wait(), timeout=1)
    same = await ppt_tools._handle_ppt_pro({"topic": "Topic A", "_session_id": "s1"}, "task")
    old = ppt_tools._PPT_PRO_RUNNING["s1"]
    other = await ppt_tools._handle_ppt_pro({"topic": "Topic B", "_session_id": "s1"}, "task")
    await asyncio.wait_for(cancelled.wait(), timeout=1)

    assert first["status"] == "researching"
    assert same["status"] == "already_running"
    assert other["status"] == "researching"
    assert ppt_tools._PPT_PRO_RUNNING["s1"] is not old
    assert ppt_tools._PPT_PRO_RUNNING_TOPIC["s1"] == "Topic B"


@pytest.mark.asyncio
async def test_orchestrate_accept_renders_and_reports(monkeypatch):
    events: list[tuple[str, str]] = []
    pushed: list[tuple[str, list[dict], str]] = []
    receipts: list[tuple[str, dict]] = []

    async def notifier(sid, msg):
        events.append((sid, msg))

    async def outline_propose(session_id, **kwargs):
        assert kwargs["sources_count"] == 2
        assert "outline_md" in kwargs
        return {"action": "accept"}

    async def fake_llm():
        return lambda prompt: "[]"

    async def fake_draft(*args, **kwargs):
        return _slides()

    def fake_render():
        return {
            "ok": True,
            "path": "deck.pptx",
            "artifacts": [{"kind": "file", "path": "deck.pptx"}],
        }

    async def run_blocking(fn):
        return fn()

    monkeypatch.setattr(ppt_tools, "_research_topic_for_ppt", lambda *a, **k: SimpleNamespace(citations=[1, 2]))
    monkeypatch.setattr(ppt_tools, "_save_and_index_research", lambda *a, **k: None)
    monkeypatch.setattr(ppt_tools, "_draft_outline_from_research", fake_draft)
    monkeypatch.setattr(ppt_tools, "_render_pro", lambda *a, **k: fake_render())
    monkeypatch.setattr("deskpet.tools.research_tools._resolve_default_llm_call", fake_llm)
    monkeypatch.setattr(ppt_tools, "_open_image_file", lambda path: True)
    ppt_tools.set_ppt_pro_services(
        artifact_pusher=lambda sid, artifacts, text: pushed.append((sid, artifacts, text)),
        receipt_reporter=lambda sid, **kw: receipts.append((sid, kw)),
    )

    await ppt_tools._ppt_pro_orchestrate(
        topic="AI",
        pages=2,
        depth="light",
        theme="minimal",
        image_mode=True,
        title="Deck",
        author="Tester",
        output_path=None,
        outline_propose=outline_propose,
        notifier=notifier,
        run_blocking=run_blocking,
        session_id="s1",
    )

    assert pushed[0][0] == "s1"
    assert receipts[0][1]["outcome"] == "ok"
    assert events


@pytest.mark.asyncio
async def test_orchestrate_cancel_reuse_modify_and_render_timeout_paths(monkeypatch):
    async def fake_llm():
        return lambda prompt: "[]"

    async def fake_draft(*args, **kwargs):
        return _slides()

    monkeypatch.setattr(ppt_tools, "_research_topic_for_ppt", lambda *a, **k: None)
    monkeypatch.setattr(ppt_tools, "_draft_outline_from_research", fake_draft)
    monkeypatch.setattr("deskpet.tools.research_tools._resolve_default_llm_call", fake_llm)
    monkeypatch.setattr(ppt_tools, "_ppt_pro_cfg", lambda: SimpleNamespace(
        enabled=True,
        default_depth="light",
        max_revisions=2,
        research_timeout_s=0.1,
        confirm_timeout_s=0.1,
        image_probe_timeout_s=0.1,
        render_timeout_s=0.01,
        save_research=False,
        outline_history=True,
    ))

    cancel_events: list[str] = []

    async def cancel_propose(*args, **kwargs):
        return {"action": "cancel"}

    await ppt_tools._ppt_pro_orchestrate(
        topic="Cancel",
        pages=2,
        depth="light",
        theme="minimal",
        image_mode=False,
        title="",
        author="Tester",
        output_path=None,
        outline_propose=cancel_propose,
        notifier=lambda sid, msg: cancel_events.append(msg),
        run_blocking=lambda fn: fn(),
        session_id="s1",
    )
    assert cancel_events

    actions = iter([
        {"action": "modify", "feedback": "change page 2"},
        {"action": "reuse", "reuse_id": "old"},
    ])
    monkeypatch.setattr(
        ppt_tools,
        "ppt_outline_store",
        SimpleNamespace(get_outline=lambda oid: {"slides_json": json.dumps([{"layout": "bullet", "title": "Reused"}])}),
        raising=False,
    )
    monkeypatch.setattr(ppt_tools, "_render_pro", lambda *a, **k: {"ok": True, "path": "deck.pptx", "artifacts": []})

    async def run_blocking(fn):
        return fn()

    await ppt_tools._ppt_pro_orchestrate(
        topic="Reuse",
        pages=2,
        depth="light",
        theme="minimal",
        image_mode=False,
        title="",
        author="Tester",
        output_path=None,
        outline_propose=lambda *a, **k: next(actions),
        notifier=lambda sid, msg: None,
        run_blocking=run_blocking,
        session_id="s1",
    )

    timeout_events: list[str] = []

    async def slow_run_blocking(fn):
        await asyncio.sleep(0.05)

    await ppt_tools._ppt_pro_orchestrate(
        topic="Timeout",
        pages=2,
        depth="light",
        theme="minimal",
        image_mode=False,
        title="",
        author="Tester",
        output_path=None,
        outline_propose=lambda *a, **k: {"action": "accept"},
        notifier=lambda sid, msg: timeout_events.append(msg),
        run_blocking=slow_run_blocking,
        session_id="s1",
    )
    assert timeout_events


@pytest.mark.asyncio
async def test_runner_identity_guard_keeps_new_task_when_old_finally_runs_late(monkeypatch):
    release_old = asyncio.Event()
    release_new = asyncio.Event()
    count = {"n": 0}

    async def fake_orchestrate(**kwargs):
        count["n"] += 1
        if count["n"] == 1:
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                await release_old.wait()
                raise
        await release_new.wait()

    async def notifier(sid, msg):
        return None

    ppt_tools.set_ppt_pro_services(
        outline_propose=lambda *a, **k: None,
        notifier=notifier,
        run_blocking=lambda fn: fn(),
    )
    monkeypatch.setattr(ppt_tools, "_ppt_pro_orchestrate", fake_orchestrate)

    await ppt_tools._handle_ppt_pro({"topic": "A", "_session_id": "s1"}, "task")
    await asyncio.sleep(0)
    old = ppt_tools._PPT_PRO_RUNNING["s1"]
    await ppt_tools._handle_ppt_pro({"topic": "B", "_session_id": "s1"}, "task")
    new = ppt_tools._PPT_PRO_RUNNING["s1"]

    release_old.set()
    await asyncio.gather(old, return_exceptions=True)

    assert ppt_tools._PPT_PRO_RUNNING["s1"] is new
    assert ppt_tools._PPT_PRO_RUNNING_TOPIC["s1"] == "B"
    release_new.set()
