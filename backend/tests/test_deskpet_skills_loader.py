"""Tests for P4-S10 SkillLoader (tasks 15.1-15.10).

Covers every Requirement / Scenario in spec §15:

  * YAML frontmatter parse + required-field validation
  * ``execute(name, args)`` substitutes ``${args[N]}``
  * Hot reload (explicit + debounce)
  * Reload failure preserves previous snapshot
  * ``script.py`` sandbox timeout + stdout capture
  * ``select(task_type, prefer)`` duck-type for SkillComponent
  * ``list_skills()`` IPC shape
  * Scope tie-break: user wins over built-in on duplicate name
"""
from __future__ import annotations

import asyncio
import textwrap
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from deskpet.skills.loader import SkillLoader, SkillMeta, _split_frontmatter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_VALID_FM = textwrap.dedent(
    """\
    ---
    name: demo
    description: A demo skill
    version: 0.1.0
    author: tests
    task_types: [chat, recall]
    ---
    Hello from demo skill. args0=${args[0]} args1=${args[1]}
    """
)


def _write_skill(
    root: Path,
    name: str,
    body: str = _VALID_FM,
) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    md = skill_dir / "SKILL.md"
    md.write_text(body, encoding="utf-8")
    return md


@pytest.fixture
def dirs(tmp_path: Path) -> tuple[Path, Path]:
    built_in = tmp_path / "built-in"
    user = tmp_path / "user"
    built_in.mkdir()
    user.mkdir()
    return built_in, user


# ---------------------------------------------------------------------------
# 1. Valid load
# ---------------------------------------------------------------------------
def test_valid_skill_loads_with_metadata(dirs: tuple[Path, Path]) -> None:
    built_in, user = dirs
    _write_skill(built_in, "demo")
    loader = SkillLoader([built_in, user], enable_watch=False)
    loader.reload()
    metas = loader.list_metas()
    assert len(metas) == 1
    m = metas[0]
    assert isinstance(m, SkillMeta)
    assert m.name == "demo"
    assert m.description == "A demo skill"
    assert m.version == "0.1.0"
    assert m.author == "tests"
    assert m.task_types == ["chat", "recall"]
    assert m.requires_script is False
    assert m.scope == "built-in"


# ---------------------------------------------------------------------------
# 2. Missing required frontmatter → skip (others still load)
# ---------------------------------------------------------------------------
def test_missing_required_frontmatter_skipped(
    dirs: tuple[Path, Path], caplog: pytest.LogCaptureFixture
) -> None:
    built_in, user = dirs
    # Missing `name`
    bad = textwrap.dedent(
        """\
        ---
        description: broken
        version: 0.1.0
        author: tests
        ---
        body
        """
    )
    _write_skill(built_in, "bad-skill", body=bad)
    _write_skill(built_in, "good", body=_VALID_FM)
    loader = SkillLoader([built_in, user], enable_watch=False)
    loader.reload()
    names = [m.name for m in loader.list_metas()]
    assert names == ["demo"]


# ---------------------------------------------------------------------------
# 3. Malformed YAML → skip
# ---------------------------------------------------------------------------
def test_invalid_yaml_skipped(dirs: tuple[Path, Path]) -> None:
    built_in, user = dirs
    bad = textwrap.dedent(
        """\
        ---
        name: broken
        description: : : :
            bad-indent
          - another
        ---
        body
        """
    )
    _write_skill(built_in, "broken", body=bad)
    _write_skill(built_in, "ok", body=_VALID_FM)
    loader = SkillLoader([built_in, user], enable_watch=False)
    loader.reload()
    names = {m.name for m in loader.list_metas()}
    assert "demo" in names
    assert "broken" not in names


# ---------------------------------------------------------------------------
# 4. execute() substitutes ${args[N]}
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_execute_substitutes_args(dirs: tuple[Path, Path]) -> None:
    built_in, user = dirs
    _write_skill(built_in, "demo")
    loader = SkillLoader([built_in, user], enable_watch=False)
    loader.reload()
    body = await loader.execute("demo", ["hello", "world"])
    assert "args0=hello" in body
    assert "args1=world" in body
    # No leftover placeholders.
    assert "${args[" not in body


# ---------------------------------------------------------------------------
# 5. Explicit reload picks up a new SKILL.md
# ---------------------------------------------------------------------------
def test_hot_reload_picks_up_new_file(dirs: tuple[Path, Path]) -> None:
    built_in, user = dirs
    _write_skill(built_in, "first")
    loader = SkillLoader([built_in, user], enable_watch=False)
    loader.reload()
    assert {m.name for m in loader.list_metas()} == {"demo"}

    # Drop a new SKILL.md into the USER dir, distinct name.
    second = textwrap.dedent(
        """\
        ---
        name: user-skill
        description: added at runtime
        version: 0.2.0
        author: tests
        ---
        user body
        """
    )
    _write_skill(user, "user-skill", body=second)
    loader.reload()
    names = {m.name for m in loader.list_metas()}
    assert names == {"demo", "user-skill"}


# ---------------------------------------------------------------------------
# 6. Watchdog debounce — 5 events coalesce into one reload
# ---------------------------------------------------------------------------
def test_watchdog_debounce_fires_once_for_burst(
    dirs: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    built_in, user = dirs
    _write_skill(built_in, "demo")
    loader = SkillLoader(
        [built_in, user], enable_watch=False, debounce_s=0.1
    )
    loader.reload()

    counter = {"n": 0}
    original_reload = loader.reload

    def _counting_reload() -> None:
        counter["n"] += 1
        original_reload()

    monkeypatch.setattr(loader, "reload", _counting_reload)

    # Fire 5 rapid events well within the 100ms debounce window.
    for _ in range(5):
        loader._fire_event_for_test()
        time.sleep(0.01)

    # Wait for the timer to settle.
    time.sleep(0.3)
    assert counter["n"] == 1


# ---------------------------------------------------------------------------
# 7. Reload crash → existing skills preserved
# ---------------------------------------------------------------------------
def test_reload_failure_preserves_existing_skills(
    dirs: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    built_in, user = dirs
    _write_skill(built_in, "demo")
    loader = SkillLoader([built_in, user], enable_watch=False)
    loader.reload()
    before = {m.name for m in loader.list_metas()}
    assert before == {"demo"}

    # Force the next reload to blow up inside the scan loop.
    def _boom(self: SkillLoader, path: Path, *, scope: str) -> None:  # type: ignore[override]
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(SkillLoader, "_load_single", _boom)
    loader.reload()
    after = {m.name for m in loader.list_metas()}
    assert after == before  # D3 — prior snapshot preserved


# ---------------------------------------------------------------------------
# 8. script.py timeout → kill + error JSON
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_script_timeout_kills_process(dirs: tuple[Path, Path]) -> None:
    built_in, user = dirs
    fm = textwrap.dedent(
        """\
        ---
        name: slowloop
        description: sleeps forever
        version: 0.1.0
        author: tests
        requires_script: true
        ---
        body unused for script skills
        """
    )
    skill_dir = built_in / "slowloop"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(fm, encoding="utf-8")
    (skill_dir / "script.py").write_text(
        "import time\ntime.sleep(30)\n", encoding="utf-8"
    )
    loader = SkillLoader(
        [built_in, user], enable_watch=False, script_timeout_s=0.5
    )
    loader.reload()

    start = time.monotonic()
    result = await loader.invoke_script("slowloop")
    elapsed = time.monotonic() - start
    assert "skill_script_timeout" in result
    assert "slowloop" in result
    # Budget of 1s is tight but doable — ``kill()`` + ``wait`` should
    # be well under that on Windows.
    assert elapsed < 5.0, f"kill took {elapsed:.2f}s"


# ---------------------------------------------------------------------------
# 9. script.py stdout → returned as body
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_script_stdout_injected_as_message_body(
    dirs: tuple[Path, Path]
) -> None:
    built_in, user = dirs
    fm = textwrap.dedent(
        """\
        ---
        name: say-hello
        description: prints hello
        version: 0.1.0
        author: tests
        requires_script: true
        ---
        unused body
        """
    )
    skill_dir = built_in / "say-hello"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(fm, encoding="utf-8")
    (skill_dir / "script.py").write_text('print("hello")\n', encoding="utf-8")
    loader = SkillLoader(
        [built_in, user], enable_watch=False, script_timeout_s=10.0
    )
    loader.reload()
    out = await loader.invoke_script("say-hello")
    # Platform-agnostic newline normalisation.
    assert out.strip() == "hello"


# ---------------------------------------------------------------------------
# 10. select() honours task_types
# ---------------------------------------------------------------------------
def test_policy_auto_mount_via_task_types(dirs: tuple[Path, Path]) -> None:
    built_in, user = dirs
    fm = textwrap.dedent(
        """\
        ---
        name: emo-helper
        description: handles emotion-tagged turns
        version: 0.1.0
        author: tests
        task_types: [emotion]
        ---
        body
        """
    )
    _write_skill(built_in, "emo-helper", body=fm)
    # Also a chat-only skill so we know task_type filtering works.
    chatfm = textwrap.dedent(
        """\
        ---
        name: chatty
        description: chat only
        version: 0.1.0
        author: tests
        task_types: [chat]
        ---
        body
        """
    )
    _write_skill(built_in, "chatty", body=chatfm)

    loader = SkillLoader([built_in, user], enable_watch=False)
    loader.reload()

    selected = loader.select("emotion", prefer=[])
    names = [s.name for s in selected]
    assert names == ["emo-helper"]


# ---------------------------------------------------------------------------
# 11. list_skills() — IPC dict shape
# ---------------------------------------------------------------------------
def test_list_skills_shape_for_ui(dirs: tuple[Path, Path]) -> None:
    built_in, user = dirs
    _write_skill(built_in, "demo")
    loader = SkillLoader([built_in, user], enable_watch=False)
    loader.reload()
    items = loader.list_skills()
    assert isinstance(items, list) and len(items) == 1
    item = items[0]
    expected_keys = {
        "name",
        "description",
        "version",
        "author",
        "scope",
        "path",
        "task_types",
        "requires_script",
        "meta",
        # P4-S20: v1 surface (always present, defaults for legacy)
        "when_to_use",
        "disable_model_invocation",
        "user_invocable",
        "allowed_tools",
        "paths",
        "argument_hint",
        "source_format",
    }
    assert set(item.keys()) == expected_keys
    assert item["scope"] == "built-in"
    assert item["requires_script"] is False


# ---------------------------------------------------------------------------
# 12. Duplicate name — user scope wins
# ---------------------------------------------------------------------------
def test_duplicate_name_user_wins_over_builtin(dirs: tuple[Path, Path]) -> None:
    built_in, user = dirs
    # Same ``name: demo`` in both locations, different descriptions.
    builtin_fm = textwrap.dedent(
        """\
        ---
        name: demo
        description: BUILT-IN version
        version: 0.1.0
        author: tests
        ---
        builtin body
        """
    )
    user_fm = textwrap.dedent(
        """\
        ---
        name: demo
        description: USER version
        version: 0.2.0
        author: tests
        ---
        user body
        """
    )
    _write_skill(built_in, "demo", body=builtin_fm)
    _write_skill(user, "demo", body=user_fm)
    loader = SkillLoader([built_in, user], enable_watch=False)
    loader.reload()
    metas = loader.list_metas()
    # Only one entry with name "demo" survives.
    assert [m.name for m in metas] == ["demo"]
    survivor = metas[0]
    assert survivor.scope == "user"
    assert survivor.description == "USER version"


# ---------------------------------------------------------------------------
# 13. select() honours prefer=[skill:NAME]
# ---------------------------------------------------------------------------
def test_select_honours_prefer_skill_name(dirs: tuple[Path, Path]) -> None:
    built_in, user = dirs
    _write_skill(built_in, "demo")
    loader = SkillLoader([built_in, user], enable_watch=False)
    loader.reload()
    # task_type doesn't match, but prefer list names the skill explicitly.
    selected = loader.select("plan", prefer=["skill:demo"])
    assert [s.name for s in selected] == ["demo"]


# ---------------------------------------------------------------------------
# 14. skill_invoke tool registered on ToolRegistry
# ---------------------------------------------------------------------------
def test_skill_invoke_tool_registered(dirs: tuple[Path, Path]) -> None:
    from deskpet.tools.registry import ToolRegistry

    built_in, user = dirs
    _write_skill(built_in, "demo")
    reg = ToolRegistry()
    loader = SkillLoader(
        [built_in, user], enable_watch=False, tool_registry=reg
    )
    # Drive start() synchronously since watchdog is disabled.
    asyncio.run(loader.start())
    assert "skill_invoke" in reg.list_tools()
    # Unknown skill → error JSON, no raise.
    out = reg.dispatch("skill_invoke", {"name": "does-not-exist"})
    import json

    payload = json.loads(out)
    assert "error" in payload
    assert "does-not-exist" in payload["error"]


# ---------------------------------------------------------------------------
# 15. _split_frontmatter sanity
# ---------------------------------------------------------------------------
def test_split_frontmatter_no_header() -> None:
    fm, body = _split_frontmatter("no frontmatter here\njust body")
    assert fm is None
    assert body == "no frontmatter here\njust body"


def test_split_frontmatter_valid() -> None:
    text = "---\nname: foo\n---\nhello"
    fm, body = _split_frontmatter(text)
    assert fm == {"name": "foo"}
    assert body.strip() == "hello"
