"""SkillLoader — SKILL.md hot-reload engine (P4-S10, tasks 15.1-15.9).

Scans two directories for ``SKILL.md`` files:

* ``<user_data_dir>/deskpet/skills/built-in/`` — shipped with the app
* ``<user_data_dir>/deskpet/skills/user/``     — user-editable

Each ``SKILL.md`` starts with a YAML frontmatter block delimited by
``---`` lines. Required fields: ``name``, ``description``, ``version``,
``author``. Missing a required field → warn + skip (the rest of the set
keeps loading — soft failure, spec §15.2).

Runtime:

* ``reload()`` is synchronous and rescans every directory.
* ``start()`` kicks off a ``watchdog`` observer on the *user* dir with
  a 1 second debounce timer so a burst of 5 events triggers exactly one
  reload (D3: hot reload must never regress on watch failures — a
  reload crash keeps the prior skill set intact).
* ``execute(name, args)`` returns the rendered Markdown body with
  ``${args[0]}``-style placeholders substituted in. Caller injects the
  string as ``{"role": "user", "content": text}``.
* ``invoke_script(name, args)`` optionally spawns a restricted
  subprocess when ``requires_script: true`` and a sibling ``script.py``
  exists. Timeouts ``process.kill()`` the subprocess.

Design: this module does NOT import from ``deskpet.agent.*`` — the
skill registry stays orthogonal to the assembler. The SkillComponent
(see ``deskpet/agent/assembler/components/skill.py``) duck-types
``.select(task_type, prefer)``; we honour that contract here.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

import structlog
import yaml

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
_REQUIRED_FIELDS: tuple[str, ...] = ("name", "description", "version", "author")


@dataclass
class SkillMeta:
    """Parsed SKILL.md metadata (single skill)."""

    name: str
    description: str
    version: str
    author: str
    scope: str  # "built-in" | "user"
    path: str
    task_types: list[str] = field(default_factory=list)
    requires_script: bool = False
    # Any frontmatter keys beyond the known set land here so the UI
    # (P4-S11 MemoryPanel) can surface them without a loader change.
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def summary(self) -> str:
        """Alias consumed by SkillComponent via ``_skill_attr``."""
        return self.description

    def to_dict(self) -> dict[str, Any]:
        """UI-friendly dict (task 15.9 IPC surface)."""
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "author": self.author,
            "scope": self.scope,
            "path": self.path,
            "task_types": list(self.task_types),
            "requires_script": bool(self.requires_script),
            # meta preserves unknown keys without leaking internals.
            "meta": dict(self.meta),
        }


# ---------------------------------------------------------------------------
# Frontmatter parsing
# ---------------------------------------------------------------------------
def _split_frontmatter(text: str) -> tuple[Optional[dict[str, Any]], str]:
    """Return (frontmatter_dict | None, body_text).

    Accepts either ``---\\n...\\n---\\n<body>`` or a bare body (no
    frontmatter → returns ``(None, body)``). Malformed YAML raises
    ``yaml.YAMLError`` which the caller converts to a warning.
    """
    stripped = text.lstrip("\ufeff")  # BOM tolerance
    if not stripped.startswith("---"):
        return None, text
    # Locate the closing ``---`` on its own line.
    lines = stripped.splitlines(keepends=True)
    if not lines or not lines[0].strip() == "---":
        return None, text
    end_idx = -1
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break
    if end_idx == -1:
        # Unterminated frontmatter — treat as malformed.
        raise yaml.YAMLError("unterminated YAML frontmatter")
    fm_raw = "".join(lines[1:end_idx])
    body = "".join(lines[end_idx + 1 :])
    data = yaml.safe_load(fm_raw) or {}
    if not isinstance(data, dict):
        raise yaml.YAMLError(f"frontmatter must be a mapping, got {type(data).__name__}")
    return data, body


def _substitute_args(body: str, args: list[str]) -> str:
    """Replace ``${args[0]}`` / ``${args[1]}`` … tokens.

    Markdown bodies contain ``{`` / ``}`` freely, so ``str.format()`` is
    unsafe. We do a simple left-to-right scan for the literal token
    ``${args[N]}``. Missing indices substitute the empty string (so a
    template that expects 2 args invoked with 1 doesn't throw).
    """
    if not args:
        return body.replace("${args[", "${args[")  # no-op, keeps style
    out = body
    for i, val in enumerate(args):
        out = out.replace(f"${{args[{i}]}}", str(val))
    # Clear any remaining unfilled slots so the rendered message never
    # shows ``${args[3]}`` to the model.
    import re

    out = re.sub(r"\$\{args\[\d+\]\}", "", out)
    return out


# ---------------------------------------------------------------------------
# Default skill dirs
# ---------------------------------------------------------------------------
def _default_skill_dirs() -> list[Path]:
    """``[<user_data>/skills/built-in, <user_data>/skills/user]``.

    Falls back to ``~/.deskpet/skills/...`` if platformdirs is absent —
    covers minimal dev environments.
    """
    override = os.environ.get("DESKPET_SKILLS_DIR")
    if override:
        root = Path(override)
        return [root / "built-in", root / "user"]
    try:
        import platformdirs  # type: ignore

        base = Path(
            platformdirs.user_data_dir("deskpet", appauthor=False, roaming=True)
        ) / "skills"
    except Exception:
        base = Path.home() / ".deskpet" / "skills"
    return [base / "built-in", base / "user"]


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------
class SkillLoader:
    """Scans + watches SKILL.md directories. Public API (spec §15)::

        loader = SkillLoader([built_in_dir, user_dir])
        await loader.start()
        metas = loader.list_skills()
        txt = await loader.execute("recall-yesterday")
        await loader.stop()

    Thread-safety:
      ``reload()`` holds an internal lock so concurrent watchdog events
      and explicit ``list_skills()`` callers see a consistent snapshot.
    """

    def __init__(
        self,
        skill_dirs: Optional[list[Path]] = None,
        *,
        enable_watch: bool = True,
        script_timeout_s: float = 10.0,
        debounce_s: float = 1.0,
        tool_registry: Any = None,
    ) -> None:
        self._dirs: list[Path] = [
            Path(d) for d in (skill_dirs if skill_dirs is not None else _default_skill_dirs())
        ]
        self._enable_watch = enable_watch
        self._script_timeout_s = float(script_timeout_s)
        self._debounce_s = float(debounce_s)
        self._tool_registry = tool_registry
        # Scope inference: index 0 is built-in, index 1+ is user. The
        # first dir containing the path wins.
        self._lock = threading.Lock()
        self._skills: dict[str, SkillMeta] = {}  # name → meta
        self._observer: Any = None
        self._debounce_timer: Optional[threading.Timer] = None
        self._started = False
        # Tool registry integration — register once per loader so names
        # re-bound on reload always dispatch to the live body.
        self._tool_registered = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def start(self) -> None:
        """Initial scan + watchdog install on the user dir."""
        if self._started:
            return
        self._started = True
        self.reload()
        self._register_skill_invoke_tool()
        if self._enable_watch and len(self._dirs) >= 2:
            self._install_watchdog()

    async def stop(self) -> None:
        """Cancel timer, halt observer, join its thread."""
        if not self._started:
            return
        self._started = False
        if self._debounce_timer is not None:
            try:
                self._debounce_timer.cancel()
            except Exception:
                pass
            self._debounce_timer = None
        if self._observer is not None:
            try:
                self._observer.stop()
                self._observer.join(timeout=2.0)
            except Exception as exc:  # noqa: BLE001
                logger.warning("skill.observer_stop_error", error=str(exc))
            self._observer = None

    # ------------------------------------------------------------------
    # Reload
    # ------------------------------------------------------------------
    def reload(self) -> None:
        """Re-scan every directory; swap the cache atomically.

        On per-file failure we log and continue so a single bad SKILL.md
        never wedges the loader. On a catastrophic exception during the
        merge we preserve the previous cache (D3).
        """
        new_map: dict[str, SkillMeta] = {}
        try:
            for idx, d in enumerate(self._dirs):
                scope = "built-in" if idx == 0 else "user"
                if not d.exists():
                    continue
                for skill_dir in sorted(p for p in d.iterdir() if p.is_dir()):
                    skill_md = skill_dir / "SKILL.md"
                    if not skill_md.is_file():
                        continue
                    meta = self._load_single(skill_md, scope=scope)
                    if meta is None:
                        continue
                    # Tie-break: user scope wins over built-in. We iterate
                    # built-in first, so when a later "user" entry arrives
                    # with the same name we overwrite.
                    prior = new_map.get(meta.name)
                    if prior is not None and prior.scope == "user" and scope != "user":
                        continue
                    new_map[meta.name] = meta
        except Exception as exc:  # noqa: BLE001 — preserve prior snapshot
            logger.warning(
                "skill.reload_crashed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return

        with self._lock:
            self._skills = new_map
        logger.info("skill.reload_ok", count=len(new_map))

    def _load_single(self, skill_md: Path, *, scope: str) -> Optional[SkillMeta]:
        try:
            text = skill_md.read_text(encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            logger.warning("skill.read_failed", path=str(skill_md), error=str(exc))
            return None
        try:
            fm, _body = _split_frontmatter(text)
        except yaml.YAMLError as exc:
            logger.warning(
                "skill.invalid_frontmatter",
                path=str(skill_md),
                error=str(exc),
            )
            return None
        if fm is None:
            logger.warning("skill.invalid_frontmatter", path=str(skill_md), error="missing")
            return None
        missing = [f for f in _REQUIRED_FIELDS if not fm.get(f)]
        if missing:
            logger.warning(
                "skill.invalid_frontmatter",
                path=str(skill_md),
                error=f"missing required fields: {missing}",
            )
            return None
        known = set(_REQUIRED_FIELDS) | {"task_types", "requires_script"}
        extra = {k: v for k, v in fm.items() if k not in known}
        task_types = fm.get("task_types") or []
        if not isinstance(task_types, list):
            task_types = []
        return SkillMeta(
            name=str(fm["name"]),
            description=str(fm["description"]),
            version=str(fm["version"]),
            author=str(fm["author"]),
            scope=scope,
            path=str(skill_md),
            task_types=[str(t) for t in task_types],
            requires_script=bool(fm.get("requires_script", False)),
            meta=extra,
        )

    # ------------------------------------------------------------------
    # Query surface
    # ------------------------------------------------------------------
    def list_skills(self) -> list[dict[str, Any]]:
        """IPC-friendly dict list — task 15.9."""
        with self._lock:
            metas = list(self._skills.values())
        metas.sort(key=lambda m: (m.scope, m.name))
        return [m.to_dict() for m in metas]

    def list_metas(self) -> list[SkillMeta]:
        """Sorted :class:`SkillMeta` objects (internal/assembler use)."""
        with self._lock:
            metas = list(self._skills.values())
        metas.sort(key=lambda m: (m.scope, m.name))
        return metas

    def all(self) -> list[SkillMeta]:
        """Compat alias — SkillComponent calls ``.all()`` when there's no
        ``.select()``. We provide both; component prefers select()."""
        return self.list_metas()

    def get(self, name: str) -> Optional[SkillMeta]:
        with self._lock:
            return self._skills.get(name)

    def select(
        self, task_type: str, prefer: Optional[list[str]] = None
    ) -> list[SkillMeta]:
        """Duck-type contract expected by SkillComponent.

        Selection rules (simpler than the full policy proposal — we
        don't touch policy YAML for S10):

          1. If ``prefer`` contains ``"skill:NAME"`` entries → those
             named skills (if loaded) come out first.
          2. Then skills whose ``task_types`` frontmatter list contains
             ``task_type`` get added (dedup preserved).
          3. Unknown task type + no name-based prefer → empty list so
             the assembler doesn't leak random skills into chat turns.
        """
        prefer = list(prefer or [])
        with self._lock:
            snapshot = dict(self._skills)

        ordered: list[SkillMeta] = []
        seen: set[str] = set()

        # 1. Explicit skill:name preferences (P4-S11 will wire these
        #    through policy YAML; for now they can arrive via
        #    prefer=[...]).
        for p in prefer:
            if not isinstance(p, str) or not p.startswith("skill:"):
                continue
            name = p.split(":", 1)[1].strip()
            meta = snapshot.get(name)
            if meta is not None and meta.name not in seen:
                ordered.append(meta)
                seen.add(meta.name)

        # 2. task_types match.
        for meta in snapshot.values():
            if meta.name in seen:
                continue
            if task_type and task_type in meta.task_types:
                ordered.append(meta)
                seen.add(meta.name)

        return ordered

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------
    async def execute(
        self, name: str, args: Optional[list[str]] = None
    ) -> str:
        """Render a skill's body with ``${args[N]}`` substitution.

        Returns the body text. Caller wraps as a ``user`` message.
        Unknown name → raises :class:`KeyError`; the ``skill_invoke``
        tool handler converts that into an error payload.
        """
        meta = self.get(name)
        if meta is None:
            raise KeyError(name)
        if meta.requires_script:
            return await self.invoke_script(name, args)
        # Re-read from disk so hot edits reflect immediately even when
        # the watchdog hasn't fired yet (reduces surprise for the user
        # while iterating on SKILL.md bodies).
        try:
            text = Path(meta.path).read_text(encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            logger.warning("skill.read_failed", name=name, error=str(exc))
            raise
        try:
            _fm, body = _split_frontmatter(text)
        except yaml.YAMLError:
            # Cached meta is good even if the current disk copy has bad
            # frontmatter — fall back to the cached path body.
            body = text
        return _substitute_args(body.strip("\n"), list(args or []))

    async def invoke_script(
        self, name: str, args: Optional[list[str]] = None
    ) -> str:
        """Run the skill's ``script.py`` in a sandboxed subprocess.

        See §15.6: the script runs with ``__builtins__`` pruned to a
        small allowlist, ``sys.stdin`` closed, and a wall-clock timeout.
        On timeout we ``.kill()`` the process and return an error JSON.
        """
        meta = self.get(name)
        if meta is None:
            raise KeyError(name)
        script_path = Path(meta.path).parent / "script.py"
        if not script_path.is_file():
            return '{"error": "skill_script_missing", "name": "' + name + '"}'

        try:
            script_body = script_path.read_text(encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            logger.warning("skill.script_read_failed", name=name, error=str(exc))
            return '{"error": "skill_script_read_failed"}'

        # Restricted preamble — the subprocess sees a filtered builtins
        # table before user code runs. Anything not in _ALLOWED is
        # blanked out.
        preamble = (
            "import sys\n"
            "try:\n"
            "    sys.stdin.close()\n"
            "except Exception:\n"
            "    pass\n"
            "_ALLOWED = {\n"
            "    'abs','all','any','bool','dict','divmod','enumerate','filter','float',\n"
            "    'format','frozenset','hash','hex','id','int','isinstance','issubclass',\n"
            "    'iter','len','list','map','max','min','next','oct','ord','pow','print',\n"
            "    'range','repr','reversed','round','set','slice','sorted','str','sum',\n"
            "    'tuple','type','zip','True','False','None'\n"
            "}\n"
            "try:\n"
            "    import builtins as _b\n"
            "    for _name in list(vars(_b).keys()):\n"
            "        if _name.startswith('__') and _name.endswith('__'):\n"
            "            continue\n"
            "        if _name not in _ALLOWED:\n"
            "            try:\n"
            "                delattr(_b, _name)\n"
            "            except Exception:\n"
            "                pass\n"
            "except Exception:\n"
            "    pass\n"
        )
        full_source = preamble + "\n" + script_body

        # Build the subprocess. Windows: no POSIX signals needed.
        # ``asyncio.wait_for`` + ``process.kill()`` handles timeouts
        # uniformly.
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-I",  # isolated mode — ignore env + user site-packages
            "-c",
            full_source,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self._script_timeout_s
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except Exception:
                pass
            return (
                '{"error": "skill_script_timeout", "name": "' + name + '"}'
            )

        if proc.returncode != 0:
            err = stderr.decode("utf-8", errors="replace").strip().replace("\\", "\\\\").replace('"', '\\"')
            return (
                '{"error": "skill_script_failed", "stderr": "' + err + '"}'
            )
        return stdout.decode("utf-8", errors="replace")

    # ------------------------------------------------------------------
    # ToolRegistry integration (task 15.3 surface)
    # ------------------------------------------------------------------
    def _register_skill_invoke_tool(self) -> None:
        """Expose ``skill_invoke(name, args=[])`` if a registry was injected.

        Handler is synchronous per ``ToolHandler`` contract; internally
        we bridge through ``asyncio.run`` when no loop is available.
        """
        if self._tool_registry is None or self._tool_registered:
            return
        reg = self._tool_registry

        schema = {
            "name": "skill_invoke",
            "description": "Invoke a loaded skill by name; returns the rendered user-role body.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Skill name (SKILL.md frontmatter)."},
                    "args": {
                        "type": "array",
                        "items": {"type": "string"},
                        "default": [],
                        "description": "Positional args substituted as ${args[N]} inside the body.",
                    },
                },
                "required": ["name"],
            },
        }

        loader = self

        def _handler(args: dict[str, Any], task_id: str = "") -> str:
            import json as _json

            name = args.get("name")
            if not isinstance(name, str) or not name:
                return _json.dumps(
                    {"error": "skill_invoke requires 'name' (string)", "retriable": False}
                )
            raw_args = args.get("args") or []
            if not isinstance(raw_args, list):
                raw_args = [str(raw_args)]
            str_args = [str(a) for a in raw_args]
            try:
                # Bridge async → sync. Fresh loop per call — dispatch
                # runs on the ToolRegistry's dispatch thread which has
                # no running loop.
                body = asyncio.run(loader.execute(name, str_args))
            except KeyError:
                return _json.dumps({"error": f"unknown skill: {name}", "retriable": False})
            except Exception as exc:  # noqa: BLE001
                return _json.dumps({"error": f"{type(exc).__name__}: {exc}", "retriable": True})
            return _json.dumps({"role": "user", "content": body}, ensure_ascii=False)

        try:
            reg.register("skill_invoke", "skill", schema, _handler)
        except Exception as exc:  # noqa: BLE001
            logger.warning("skill.tool_register_failed", error=str(exc))
            return
        self._tool_registered = True

    # ------------------------------------------------------------------
    # Watchdog
    # ------------------------------------------------------------------
    def _install_watchdog(self) -> None:
        user_dir = self._dirs[-1]  # user dir is the last entry
        try:
            user_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning("skill.user_dir_mkdir_failed", error=str(exc))
            return
        try:
            from watchdog.events import FileSystemEventHandler
            from watchdog.observers import Observer
        except Exception as exc:  # noqa: BLE001
            logger.warning("skill.watchdog_unavailable", error=str(exc))
            return

        loader = self

        class _Handler(FileSystemEventHandler):  # type: ignore[misc]
            def on_any_event(self, event: Any) -> None:  # noqa: D401
                # All events bounce through the debounce timer — we
                # never call ``reload()`` inline from the observer
                # thread to avoid blocking it on slow I/O.
                loader._schedule_debounced_reload()

        observer = Observer()
        observer.schedule(_Handler(), str(user_dir), recursive=True)
        observer.daemon = True
        try:
            observer.start()
        except Exception as exc:  # noqa: BLE001
            logger.warning("skill.observer_start_failed", error=str(exc))
            return
        self._observer = observer

    def _schedule_debounced_reload(self) -> None:
        """Coalesce bursts of events into one reload.

        Each call cancels the prior pending timer; the actual reload
        only runs ``debounce_s`` seconds after the LAST event.
        """
        with self._lock:
            if self._debounce_timer is not None:
                try:
                    self._debounce_timer.cancel()
                except Exception:
                    pass
            t = threading.Timer(self._debounce_s, self._on_debounce_fire)
            t.daemon = True
            self._debounce_timer = t
            t.start()

    def _on_debounce_fire(self) -> None:
        try:
            self.reload()
        except Exception as exc:  # noqa: BLE001 — keep prior cache
            logger.warning(
                "skill.debounce_reload_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )

    # Test hook: trigger the debounce path directly without waiting
    # for a real filesystem event.
    def _fire_event_for_test(self) -> None:
        self._schedule_debounced_reload()

    # ------------------------------------------------------------------
    # Introspection helpers
    # ------------------------------------------------------------------
    @property
    def skill_dirs(self) -> list[Path]:
        return list(self._dirs)


__all__ = [
    "SkillLoader",
    "SkillMeta",
]
