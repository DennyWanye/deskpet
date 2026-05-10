# Design — P4-S22 Code Mode

## Overall flow

```
   chat msg ──► main.py:1450 chat handler ──► AgentLoop
                       │                          │
                       ▼                          ▼
              code_mode_state              tool_registry_v2
              (per session_id)             + 5 new code tools
              ├─ enabled: bool             - Glob
              ├─ project_root: Path        - Grep
              └─ session_id: str           - TodoWrite
                       │                   - WebSearch
                       ▼                   - Agent (subagent)
              max_iterations:
                8 if !enabled
                50 if enabled
```

## State (Python side)

```python
# backend/deskpet/code_mode/state.py — new module
@dataclass
class CodeModeState:
    enabled: bool = False
    project_root: Path | None = None
    session_id: str = "default"          # base session before code mode
    code_session_id: str | None = None   # derived: "code-<sha[:8]>"

# Singleton on service_context (per-process, not per-session)
service_context.register("code_mode", CodeModeManager())
```

`CodeModeManager` is a thin wrapper that:
- Holds a `dict[base_session_id, CodeModeState]`
- Exposes `enter(session_id, project_path) -> code_session_id`
- Exposes `exit(session_id)`
- Exposes `is_enabled(session_id) -> bool`, `current_root(session_id) -> Path | None`
- Computes `code_session_id = "code-" + sha1(str(project_root.resolve()))[:8]`

## Project root resolution

```python
# backend/deskpet/code_mode/project_root.py
def resolve_project_root(user_choice: str | None, llm_suggested_name: str) -> Path:
    if user_choice:
        p = Path(user_choice).expanduser().resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p

    # No user choice — auto-create
    base = paths.user_data_dir() / "projects"   # %AppData%\deskpet\projects
    safe_name = sanitize(llm_suggested_name) or "untitled"
    p = base / safe_name
    p.mkdir(parents=True, exist_ok=True)
    # Seed a README so file ops have something to anchor on
    readme = p / "README.md"
    if not readme.exists():
        readme.write_text(f"# {safe_name}\n\nCreated by DeskPet Code mode.\n", encoding="utf-8")
    return p
```

`sanitize` strips OS-illegal chars + collapses whitespace, caps at 60
chars. Returns `""` if input is unsafe → caller falls back to "untitled".

## AgentLoop — pluggable max_iterations

```python
# backend/agent/agent_loop.py — current
class AgentLoop:
    def __init__(self, llm_registry, tool_registry, max_iterations: int = 8): ...
```

Already pluggable. The chat handler picks the right value per session:

```python
# main.py — _run_chat
mgr = service_context.get("code_mode")
in_code = mgr and mgr.is_enabled(_sid)
_max_iter = 50 if in_code else 8
_agent = _AgentLoop(
    llm_registry=_shim,
    tool_registry=deskpet_tool_registry_v2,
    max_iterations=_max_iter,
)
```

## System prompt — code mode template

```python
# backend/deskpet/agent/assembler/components/persona.py — extended
_CODE_MODE_PERSONA_TEMPLATE = (
    "你是 DeskPet 的 Code 模式助手 —— 一个工程助手。\n"
    "- 当前你跑在底层模型 **{model}** 上；项目根目录: {project_root}\n"
    "- 优先使用工具完成任务: Read, Write, Edit, Glob, Grep, Bash, TodoWrite,\n"
    "  WebSearch, Agent (subagent)。\n"
    "- 复杂任务先用 TodoWrite 拆步骤,然后逐项完成。\n"
    "- 写文件之前用 Read 看现状,Edit 用精确的 old_string/new_string,\n"
    "  避免错误覆盖。\n"
    "- 跑 Bash 之前预估副作用; 长任务里 max 50 轮工具调用,够用即可停。\n"
    "- 完成后用 TodoWrite 标 done 让用户知道。\n"
)
```

PersonaComponent reads `service_context.get("code_mode")` to pick the
right template at assembly time.

## New tools — minimal viable contracts

### Glob
```python
class GlobTool:
    name = "glob"
    description = "Find files by glob pattern (e.g. **/*.py)"
    parameters = {
        "pattern": {"type": "string", "description": "glob, e.g. **/*.py"},
        "path": {"type": "string", "description": "search root (default: project_root)", "optional": True},
    }
    async def __call__(self, *, pattern: str, path: str | None = None) -> str:
        root = Path(path) if path else _current_project_root()
        matches = sorted(root.rglob(pattern), key=lambda p: -p.stat().st_mtime)
        return "\n".join(str(p) for p in matches[:200])  # cap
```

### Grep
```python
class GrepTool:
    name = "grep"
    parameters = {
        "pattern": {"type": "string"},
        "path": {"type": "string", "optional": True},
        "glob": {"type": "string", "description": "filter files by glob", "optional": True},
        "output_mode": {"type": "string", "enum": ["content", "files_with_matches", "count"], "default": "files_with_matches"},
        "context": {"type": "integer", "default": 0},
        "case_insensitive": {"type": "boolean", "default": False},
    }
```

Implementation: walk files matching `glob` under `path`, regex search
each line, collect by output_mode. Cap output at 250 lines / 100 files.

### TodoWrite
SessionDB v11 migration:
```sql
CREATE TABLE IF NOT EXISTS code_todos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    content TEXT NOT NULL,
    active_form TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending','in_progress','completed')),
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at REAL DEFAULT (julianday('now')),
    updated_at REAL DEFAULT (julianday('now'))
);
CREATE INDEX IF NOT EXISTS idx_code_todos_session ON code_todos(session_id, sort_order);
PRAGMA user_version = 11;
```

Tool replaces the entire todo list each call (idempotent):
```python
async def todo_write(items: list[dict]) -> str:
    # items: [{content, activeForm, status}, ...]
    sid = _current_code_session_id()
    await sdb.replace_todos(sid, items)
    # Push to control WS so frontend updates
    await _broadcast({"type": "code_todo_update", "payload": {"items": items}})
    return f"todos updated: {len(items)} items"
```

### WebSearch
```python
class WebSearchTool:
    async def __call__(self, *, query: str, max_results: int = 5) -> str:
        # DuckDuckGo HTML — no API key, scrape h2 + snippet
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post("https://html.duckduckgo.com/html/", data={"q": query})
        # parse with selectolax or regex; cap at max_results
        return formatted_string
```

### Agent (subagent)
```python
class AgentSubagentTool:
    """Spawn a nested AgentLoop on a specialized prompt."""
    async def __call__(self, *, description: str, prompt: str) -> str:
        sub_msgs = [
            {"role": "system", "content": "You are a focused subagent. " + description},
            {"role": "user", "content": prompt},
        ]
        sub_loop = AgentLoop(
            llm_registry=self._shim,
            tool_registry=self._tools,  # subset: read-only tools
            max_iterations=15,
        )
        out_text = ""
        async for ev in sub_loop.run(sub_msgs, session_id=f"{self._parent_sid}.sub"):
            if isinstance(ev, FinalEvent):
                out_text = ev.content or ""
                break
        return out_text
```

Subagent gets a **read-only** tool subset by default (Read, Glob, Grep,
WebSearch). Caller can pass `tools=["read", "bash"]` to override but
defaults are safe.

## Auto-detect "wants to start a project"

```python
# backend/deskpet/code_mode/intent_detector.py
_TRIGGER_KEYWORDS = [
    "做一个项目", "搞一个项目", "建个项目", "scaffold", "build me",
    "create a project", "新项目", "做个 app", "生成一个", "做一个",
]

def maybe_suggest_code_mode(text: str) -> bool:
    t = text.lower()
    return any(kw in t for kw in _TRIGGER_KEYWORDS)
```

Called inside chat handler **before** dispatching to AgentLoop. If true
AND code mode not already enabled, send a `code_mode_suggest` control msg
to the frontend; the user's actual query still runs as a normal chat.

## Frontend wiring (terse)

- Toolbar: `🔧` ToggleChip, calls `invoke('open_directory_dialog')` then
  WS `code_mode_enter` with the path. On WS reply `code_mode_state`,
  flip a React state.
- New `CodeModeBanner` rendered conditionally above DialogBar.
- `TodoListPanel` opens off the banner; subscribes to
  `code_todo_update` messages.
- `code_mode_suggest` from backend triggers a yellow `<Banner>` with
  Yes/Dismiss; Yes runs the same enter flow but with no path → backend
  auto-creates `<safename>/`.

## Test strategy

### Unit (~30 tests, runs in pytest)
- `test_p4s22_glob.py` — pattern matching, root override, cap, no matches
- `test_p4s22_grep.py` — three output modes, glob filter, context lines, regex flags
- `test_p4s22_todo_write.py` — replace semantics, schema v11 migration applies, IPC broadcast called
- `test_p4s22_web_search.py` — mock httpx response, snippet parsing, max_results cap, network failure → empty result, no exception
- `test_p4s22_agent_subagent.py` — mocked sub LLM emits FinalEvent with text, default tool subset is read-only, parent's session_id has `.sub` appended
- `test_p4s22_code_mode_state.py` — enter/exit toggle, project_root persistence, code_session_id stable hash
- `test_p4s22_intent_detector.py` — triggers / non-triggers
- `test_p4s22_project_root.py` — sanitize, readme seeding, fallback paths

### Integration (1 test, mock LLM)
`test_p4s22_code_mode_integration.py` — end to end:
1. `code_mode_enter` with empty tmp dir
2. Mock LLM scripted to emit:
   - turn 1: TodoWrite "[plan, write file, test]"
   - turn 2: tool_call Write file.py "print('hi')"
   - turn 3: tool_call Bash "python file.py"
   - turn 4: TodoWrite (mark done) + final message
3. Assert: file exists, bash output captured, final FinalEvent reached

### Manual e2e (script, but I run it)
`backend/scripts/e2e_code_mode_smoke.py` — connects to live backend, sends:
1. `code_mode_enter` (with `~/temp/deskpet-test-proj`)
2. chat: "在这个目录里生成一个简单的 todo CLI Python 脚本"
3. wait for `code_todo_update` events
4. wait for FinalEvent
5. assert file `todo.py` exists in project root, runnable

## Risks & mitigations

| Risk | Mitigation |
|------|-----------|
| 50-iteration loop blows token budget | Bumped daily_budget_cny to 100 in code mode (config.py temp override) |
| WebSearch DDG HTML format changes | Defensive parser + try/except + falls back to empty result string |
| Subagent recursion (sub-sub-sub-...) | `tool_registry` for subagent excludes `agent` tool |
| TodoWrite race when multiple tool calls in flight | Tool runs synchronously per turn; only one writer per AgentLoop iteration |
| Project root traversal escape (e.g. `path="../../etc/passwd"` to Read) | Tools resolve `path` relative to project_root; reject if resolved path is outside root via `Path.is_relative_to(project_root)` |
