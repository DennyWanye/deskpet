# CONTINUE — message-panel: 2 remaining fixes (handoff)

> Fresh agent: read top-to-bottom, self-contained. Branch
> `feat/2026-05-18-session-and-e2e`. Last good commit `a0d11a4`
> (6 msg-panel fixes; 5 verified, see below). Two issues remain.

## Context (what exists & works)

The message panel is a SEPARATE always-on-top transparent Tauri window
(`message-panel`, `index.html#/message-panel`, conf in
`tauri-app/src-tauri/tauri.conf.json`) docked left of the slim 360-wide
pet window. Root: `tauri-app/src/message-panel/MessagePanelRoot.tsx`
(reuses code-panel `MessageStreamPanel` + `InputBar` + `ws.ts` +
`stores/sessionsStore`; ws.ts control session_id is hash-derived =
`message-panel-main`). Rust cmds in
`tauri-app/src-tauri/src/commands.rs`: `open/close/toggle/dock_message_panel`
+ `emit_panel_visibility` (event `message-panel-visibility` → pet hides
its DialogBar). Backend `backend/llm/resolution.py` now lets the
companion `default` session honor a per-session binding (companion was
deliberately isolated before; user wants it). Backend 1477 passed,
frontend tsc=0 vitest 154/154.

VERIFIED via computer-use: #1 toggle show/hide, #2 pet DialogBar hides
when panel open, #3 panel full MessageStreamPanel (全部/对话/⚠/🚨),
#5 placeholder, #6 model-chip→ChangeModelModal (binding logic tested).

## REMAINING ISSUE 1 — panel drag does NOT work

`MessagePanelRoot` header uses `data-tauri-drag-region`. On a frameless
transparent always-on-top window this does NOT drag (confirmed: user +
computer-use both can't move it). ⛶ maximize (`toggleMaximize()`) also
no-ops on this window type.

**Fix:** replace reliance on `data-tauri-drag-region` with an explicit
`onMouseDown` on the header that calls Tauri
`getCurrentWindow().startDragging()` (ignore non-left buttons; the
header's buttons already `stopPropagation`). In MessagePanelRoot:
```
onMouseDown={(e) => {
  if (e.button !== 0) return;
  import("@tauri-apps/api/window")
    .then(({ getCurrentWindow }) => getCurrentWindow().startDragging())
    .catch(() => {});
}}
```
Keep `data-tauri-drag-region` too (harmless). For ⛶ "放大/全屏": if
`toggleMaximize` still fails, try `getCurrentWindow().setFullscreen(true)`
toggle, or manual `setSize` to a larger size + back. Lower priority than
drag; user's explicit complaint is drag.

## REMAINING ISSUE 2 — panel send NOT consistent with main interface

ROOT CAUSE (found, not yet fixed):
- Pet MAIN send = `ControlChannel.sendChatV2` (`tauri-app/src/ws/
  ControlChannel.ts:88`): `this.send({ type:"chat_v2", payload:{ text } })`
  — **NO session_id** (backend binds it to that control connection's
  session = "default"). App.tsx `handleSend` (line ~645) also pushes the
  user msg into pet local `messages` + shows bubble.
- Panel reuses code-panel `InputBar` (`tauri-app/src/code-panel/
  InputBar.tsx`): sends `payload:{ text, session_id: active_sid }` and
  push_message/upsert on **`active_sid`** (store's active session),
  via `codePanelWS` (control sid `message-panel-main`).
- `MessagePanelRoot` renders `MessageStreamPanel` from
  `sessions["default"].messages`.
- Mismatch: if the panel store's `active_sid` !== `"default"` (it can be
  set elsewhere), InputBar pushes to the wrong session & sends a wrong
  session_id → panel shows nothing / inconsistent with main.

**Fix (make panel target "default" exactly like main):** add an optional
`sessionId?: string` prop to `InputBar`; inside, use
`const sid = sessionId ?? active_sid;` in ALL ~5 spots that currently
use `active_sid` (send payload.session_id, push_message, upsert,
stop/interrupt, status). Then `MessagePanelRoot` renders
`<InputBar placeholder="和桌宠说点什么…" sessionId="default" />`. This
guarantees the panel always sends/echoes on `"default"` — the same
session the pet main uses and the same one MessageStreamPanel renders.
(InputBar is shared with code-panel which calls `<InputBar/>` with no
prop → `sessionId` undefined → falls back to active_sid → zero
regression for code-panel.)

Verify reply actually streams back into the panel (backend streams
chat_v2_delta/final to the SENDING ws connection; panel ws =
message-panel-main → its ws.ts dispatch → store["default"] → shows). If
reply doesn't appear, check backend chat_v2 reply routing in
`backend/main.py` (search `chat_v2_delta`/`_ws.send_json`) — should send
to the receiving `_ws`, which is the panel's.

## After fixing both

1. Gate: `cd tauri-app && npx tsc --noEmit` (EXIT 0) + `npx vitest run`
   (expect 154 passed). These 2 fixes are frontend-only → **HMR, no
   Rust/conf rebuild, no restart needed** (stack is running; if not:
   kill node/python, then the PowerShell launch in §below).
2. Verify via computer-use on the running app:
   - Drag the panel header → window moves (real fix via startDragging).
   - Type in panel input + send → user msg + assistant reply appear IN
     THE PANEL, identical to sending from the pet's main input.
3. Commit to `feat/2026-05-18-session-and-e2e`. Do NOT `git add -A`
   (it grabs `tauri-app/.claude/launch.json` — exclude it).

## Stack launch (only if not running)
```
Stop-Process -Name node,python,deskpet,deskpet-backend -Force -EA SilentlyContinue
$env:DESKPET_PYTHON="G:\projects\deskpet\backend\.venv\Scripts\python.exe"
$env:DESKPET_BACKEND_DIR="G:\projects\deskpet\backend"
$env:DESKPET_DEV_ROOT="G:\projects\deskpet"
Set-Location G:\projects\deskpet\tauri-app; npm run tauri dev
```
Backend interpreter for pytest: `G:/projects/deskpet/backend/.venv/Scripts/python.exe`
(run from `backend/`, `--ignore=tests/test_deskpet_vector_worker.py`).
Landmine: work SERIAL in main tree on this branch; no parallel
worktrees (harness worktree cuts from 96-commit-stale master).
