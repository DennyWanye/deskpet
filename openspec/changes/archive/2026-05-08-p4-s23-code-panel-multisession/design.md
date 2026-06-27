# Design — P4-S23 Code Panel + Multi-session

## Architecture

```
                Backend (Python, port 8100)
            ┌──────────────────────────────────┐
            │ CodeModeManager  (per-base-sid)  │
            │  ├ session "default"  (companion)│
            │  ├ session "code-eed22c67" (proj A)│
            │  └ session "code-1a2b3c4d" (proj B)│
            ├──────────────────────────────────┤
            │ AgentLoop coroutines (one/sid)   │
            │  asyncio tasks running in parallel│
            ├──────────────────────────────────┤
            │ control WS  (per-sid map)        │
            │ chat WS routes by payload.sid    │
            └──────────────────────────────────┘
                       │ WS              ▲ WS
       ┌───────────────┘                 └───────────┐
       │                                              │
  ┌────▼─────┐                                  ┌────▼────────────┐
  │ Pet Win  │                                  │ Code Panel Win  │
  │ (main)   │                                  │ (1024×720, 2nd) │
  │          │  shared single backend           │                 │
  │ Live2D   │                                  │ - Sessions side │
  │ Toolbar  │                                  │ - Message stream│
  │ DialogBar│  ←──── 💬 toggle visibility ──── │ - Input bar     │
  └──────────┘                                  └─────────────────┘
```

## Backend changes

### 1. Per-message session_id routing (chat handler)

Current `main.py:1488` chat handler:
```python
elif msg_type in ("chat", "chat_v2"):
    text = raw.get("payload", {}).get("text", "")
    # ... session_id = ws-level "default"
```

becomes:
```python
elif msg_type in ("chat", "chat_v2"):
    payload = raw.get("payload", {}) or {}
    text = payload.get("text", "")
    sid = payload.get("session_id") or session_id  # WS default fallback
```

Every event sent back from `_run_chat` MUST stamp `payload.session_id = _sid`
so the frontend can route. Audit: chat_response / chat_v2_final /
chat_v2_error / tool_call / tool_result all need this stamp.

### 2. CodeModeManager already supports multi-session

P4-S22's `CodeModeManager` keeps a `dict[base_session_id, CodeModeState]`.
The single-active assumption was *frontend* convention, not backend
limitation. We just expose it.

### 3. New IPC: code_sessions_list

```python
# main.py — new control msg handler
elif msg_type == "code_sessions_list":
    cmm = service_context.get("code_mode")
    items = []
    if cmm is not None:
        sdb = service_context.get("session_db")
        for base_sid, st in cmm.all_sessions().items():
            todo_count = 0
            if sdb and st.code_session_id:
                todo_count = len(await sdb.get_code_todos(st.code_session_id))
            items.append({
                "base_session_id": base_sid,
                "code_session_id": st.code_session_id,
                "project_root": str(st.project_root) if st.project_root else None,
                "project_name": st.project_name,
                "todo_count": todo_count,
                "enabled": st.enabled,
            })
    await ws.send_json({"type": "code_sessions_list_response",
                        "payload": {"items": items}})
```

### 4. Concurrency: AgentLoop per session

Today: chat handler awaits AgentLoop inline. Multiple chats can already
run in parallel (each is its own asyncio task). Verify there's no shared
mutable state (tool_registry session_context dict — yes, already per-sid).

Add a `_chat_inflight: dict[str, asyncio.Task]` for visibility, and on
`chat_v2` arrival cancel any prior task for the same sid (prevents stale
tool calls if user retries quickly).

### 5. Backend bug fixes (carryover from P4-S22)

- `providers/openai_compatible.py::chat_with_tools`: wrap httpx errors
  → LLMProviderError (already coded in working tree)
- `tools/code_tools/todo_write_tool.py`: broadcaster invocation already
  exists — main.py just needs to actually pass the broadcaster (already
  coded in working tree)
- `deskpet/mcp/manager.py::_expand_path`: %APPDATA%/deskpet → portable
  user_data_dir (already coded in working tree)

These all ride along in the same PyInstaller rebuild that ships the
multi-session changes.

## Rust / Tauri changes

### 1. Second webview window

`tauri.conf.json` add:
```json
"windows": [
  { ...existing main pet window... },
  {
    "label": "code-panel",
    "title": "DeskPet · Code Mode",
    "width": 1024,
    "height": 720,
    "resizable": true,
    "decorations": true,
    "transparent": false,
    "visible": false,
    "url": "index.html#/code-panel"
  }
]
```

### 2. New IPC commands

```rust
// commands.rs
#[tauri::command]
pub fn open_code_panel(app: AppHandle) -> Result<(), String> {
    if let Some(w) = app.get_webview_window("code-panel") {
        w.show().map_err(|e| e.to_string())?;
        w.set_focus().map_err(|e| e.to_string())?;
    }
    Ok(())
}

#[tauri::command]
pub fn close_code_panel(app: AppHandle) -> Result<(), String> {
    if let Some(w) = app.get_webview_window("code-panel") {
        w.hide().map_err(|e| e.to_string())?;
    }
    Ok(())
}
```

`capabilities/default.json` add `core:webview:allow-show` /
`allow-set-focus` / `allow-hide` and apply to both windows
(`"windows": ["main", "code-panel"]`).

### 3. Auto-open on Code mode entry

App.tsx's existing `code_mode_enter` IPC handler — in addition to
sending the WS message, also call `invoke("open_code_panel")`. On
`code_mode_exit` call `close_code_panel`.

## Frontend changes

### Routing

`main.tsx` (or `App.tsx` top-level) checks `window.location.hash`:
- `#/code-panel` → mount `<CodePanelRoot />`
- otherwise → mount `<App />` (existing pet shell)

This way both windows ship the same JS bundle but render different
trees. Saves ~80 KB of duplicate ship cost.

### Sessions store (zustand)

```ts
// stores/sessionsStore.ts
type SessionStatus = "idle" | "thinking" | "running" | "permission" | "error";

interface SessionState {
  base_session_id: string;
  project_root: string | null;
  project_name: string;
  messages: Message[];
  todos: Todo[];
  token_usage: { prompt: number; completion: number };
  status: SessionStatus;
  last_activity: number;
  inflight: boolean;  // chat in flight
}

interface SessionsStore {
  active_sid: string;        // currently-focused session in panel
  sessions: Map<string, SessionState>;
  set_active(sid: string): void;
  upsert(sid: string, patch: Partial<SessionState>): void;
  push_message(sid: string, msg: Message): void;
  upsert_todos(sid: string, todos: Todo[]): void;
}
```

WS event router (in CodePanelRoot's connect effect):
```ts
function routeEvent(msg: IncomingMessage) {
  const sid = (msg.payload as any)?.session_id || "default";
  switch (msg.type) {
    case "code_todo_update":
      store.upsert_todos(sid, msg.payload.items); break;
    case "chat_response":
      store.push_message(sid, { role: "assistant", text: msg.payload.text }); break;
    case "tool_call":
      store.push_message(sid, { role: "tool_call", ...msg.payload }); break;
    case "tool_result":
      store.push_message(sid, { role: "tool_result", ...msg.payload }); break;
    // ...
  }
}
```

### Components

```
CodePanelRoot
├── Header (project path, status, close)
├── SessionSidebar
│   ├── ActiveSessionsList (zustand subscribe sessions)
│   ├── TodosList (zustand subscribe active session todos)
│   └── TokenUsagePanel
└── MessageStream (zustand subscribe active session messages)
    └── react-virtuoso wrapping per-message <MessageBubble />
        ├── <UserBubble />
        ├── <AssistantBubble />
        │   └── <ReactMarkdown> (with CodeBlock override)
        │       └── <CodeBlock> (react-syntax-highlighter Prism light)
        └── <ToolCallCard /> (collapsible)
└── InputBar (send / model / chat_v2 toggle)
```

`MessageStream` height precompute uses `pretext`:
```ts
import { prepare, layout } from "@chenglou/pretext";

const measureHeight = (text: string, width: number) => {
  const prepared = prepare(text, "14px Inter");
  return layout(prepared, width, 22).height;
};
```
Cached by message hash so re-renders don't re-measure.

### Multi-project dashboard

`SessionGridView` — overlay rendered when user clicks "项目" in
sidebar header:

```
┌── Active Code Sessions  [+ 新项目] ──────────────────────────┐
│ ┌──────────┐ ┌──────────┐ ┌──────────┐                       │
│ │project A │ │project B │ │project C │                       │
│ │📋 3/7    │ │📋 0/0    │ │📋 5/12   │                       │
│ │⏳ thinking│ │idle      │ │🔥 running│                       │
│ │last AI:  │ │last AI:  │ │last AI:  │                       │
│ │"写完了…"  │ │"你想…"   │ │"测试通过…"│                       │
│ └──────────┘ └──────────┘ └──────────┘                       │
└──────────────────────────────────────────────────────────────┘
```

Tile click → `store.set_active(sid)` → main panel switches.

## Concurrency limiter

```ts
class ConcurrencyLimiter {
  private inflight = 0;
  private queue: (() => void)[] = [];
  constructor(private max: number) {}
  async run<T>(fn: () => Promise<T>): Promise<T> {
    if (this.inflight >= this.max) {
      await new Promise<void>(r => this.queue.push(r));
    }
    this.inflight++;
    try { return await fn(); } finally {
      this.inflight--;
      this.queue.shift()?.();
    }
  }
}
```

Wraps `sendChatV2` calls. Default `max=2`. Status pill on the panel
shows "queued: N" when active.

## Testing strategy

### Backend pytest
- `test_p4s23_chat_handler_session_routing.py` — chat handler honors
  `payload.session_id`, falls back to ws session_id, isolation
  between two parallel chats.
- `test_p4s23_code_sessions_list.py` — IPC returns all enabled
  sessions, todo_count populated, disabled sessions excluded.
- Existing 900+ tests must stay green.

### Frontend
- `tsc -b` clean
- Manual e2e through computer-use:
  1. Launch deskpet
  2. Pet visible
  3. Click 🔧 in toolbar → folder picker → select project A
  4. Code panel opens (second window)
  5. Type "create hello.py" in panel → permission → allow
  6. File appears on disk
  7. Click "+ 新项目" in sidebar → pick project B
  8. Switch back to A's tile → see A's history preserved
  9. Send a message in B → A's tile shows old data (isolation)
  10. Close panel via 💬 → reopen → state restored
  11. Exit code mode (退出) → panel closes, pet resumes

### Visual regression
Take screenshots at each step above; cross-check that:
- Pet keeps running parallel to panel
- Panel layout matches the wireframe (sidebar / messages / input)
- Code blocks have visible syntax colors
- Tool call cards collapse/expand
- AppData remains untouched (portable userdata only)

## Implementation order

1. **Phase 0 — venv + dev backend ready** (allows hot iteration)
   1.1 Rebuild .venv
   1.2 pip install -r requirements.txt
   1.3 verify dev backend starts via Tauri spawn

2. **Phase A — Code panel** (frontend-heavy)
   2.1 Tauri second window + open/close commands
   2.2 React routing #/code-panel
   2.3 Zustand store + WS event router
   2.4 CodeChatPanel layout + components
   2.5 react-markdown + react-syntax-highlighter
   2.6 react-virtuoso + pretext height measurement

3. **Phase B — Multi-session**
   3.1 Backend chat handler accepts payload.session_id
   3.2 All broadcast events stamped with sid
   3.3 New code_sessions_list IPC
   3.4 SessionGridView + tile selection
   3.5 Concurrency limiter

4. **Phase C — Polish**
   4.1 @filename autocomplete
   4.2 Tool card collapse / copy
   4.3 Token usage progress
   4.4 Session persistence

5. **Bug fixes folded in** (already coded, just needs build)

6. **Build + visual e2e**
   6.1 backend pytest 全跑
   6.2 PyInstaller rebuild
   6.3 Tauri MSI rebuild
   6.4 Computer-use visual smoke

7. **Archive** the OpenSpec change.
