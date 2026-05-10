# frontend-ipc-surface Specification

## Purpose
TBD - created by archiving change p4-s21-msi-polish-pack. Update Purpose after archive.
## Requirements
### Requirement: Frontend MUST update LLM config via Rust IPC, not direct fetch
The system SHALL expose a Rust `update_cloud_config(update)` Tauri
command that proxies the POST to the backend's `/config/cloud`
endpoint, attaching the SHARED_SECRET server-side. The frontend's
`bindings/config.ts::updateCloudConfig` SHALL call this command via
`invoke()` instead of `fetch("http://127.0.0.1:8100/config/cloud")`.
Reason: in release builds the webview origin is `https://tauri.localhost`,
and browsers block `https→http` fetches as mixed content — Settings
"Save" silently failed with `TypeError: Failed to fetch`.

#### Scenario: Settings save in release build succeeds
- **GIVEN** user is running the production MSI install
- **WHEN** user changes LLM model + base_url + clicks Save
- **THEN** `invoke("update_cloud_config", {update: ...})` is called
- **AND** Rust proxies the request to backend with X-Shared-Secret
- **AND** backend returns ok; frontend shows success state

#### Scenario: Frontend never sees the SHARED_SECRET
- **WHEN** the IPC bridge handles the request
- **THEN** the secret is read from `BackendProcess::shared_secret_clone()` server-side
- **AND** the frontend's `updateCloudConfig` ignores its `secret` parameter

### Requirement: Tauri MUST expose an app_exit IPC command for the Quit button
The system SHALL expose `app_exit()` as a Tauri command. Calling it
SHALL invoke `BackendProcess::kill_child()` and `app.exit(0)` in that
order, ensuring the supervisor releases port 8100 before the process
terminates. The Toolbar `⏻` Quit button SHALL invoke this command.

#### Scenario: Toolbar Quit closes everything cleanly
- **WHEN** user clicks the `⏻` button in the Toolbar
- **THEN** `invoke("app_exit")` runs
- **AND** the deskpet-backend.exe child process exits within 1 second
- **AND** port 8100 is released

### Requirement: Tauri MUST register a system tray with Show/Hide/Quit
The system SHALL register a system tray icon at startup with three
menu items: 显示桌宠 (show), 隐藏桌宠 (hide), 退出 DeskPet (quit).
Show shows + focuses the main window; Hide hides it without exiting;
Quit invokes the same kill_child + app.exit flow as the Toolbar Quit.

#### Scenario: Tray icon appears alongside the pet
- **WHEN** DeskPet starts
- **THEN** a tray icon labeled "DeskPet" is visible in the notification area
- **AND** right-clicking it reveals the three menu items

### Requirement: Backend spawn MUST suppress the orphan console window
The system SHALL spawn the bundled backend exe with the Windows
`CREATE_NO_WINDOW` flag (0x08000000). Without this flag, Windows
allocates a new console window for the console-subsystem PyInstaller
exe and shows it next to the pet — closing it kills the backend.

#### Scenario: No console window appears at startup
- **WHEN** DeskPet starts in production / release mode
- **THEN** no `cmd.exe`-styled console window opens alongside the pet
- **AND** SHARED_SECRET is still read from the piped stdout

### Requirement: supervisor_alert outbound event

The control WebSocket SHALL support a new outbound event type `supervisor_alert` with payload schema:
```
{
  session_id: string,
  alert_id: string,            // unique per alert; used for click-back correlation
  severity: "green" | "yellow" | "red",
  action: "nudge" | "ask_user", // wait does not broadcast; cancel coerces to ask_user
  diagnosis: string,            // <=200 chars
  user_message: string,         // <=120 chars; pet bubble text
  suggested_buttons: string[]   // 0..2 button labels
}
```
This event SHALL be broadcast to ALL connected control WebSockets (both pet main window and code-panel window) using the same fan-out pattern as `code_todo_update`.

#### Scenario: Both windows receive the alert

- **GIVEN** pet main and code-panel WebSockets are both connected
- **WHEN** the backend broadcasts `supervisor_alert`
- **THEN** both WebSockets receive identical payload

#### Scenario: Disconnected WS does not block other sends

- **GIVEN** the code-panel WebSocket has died but is still in the connections dict
- **WHEN** broadcasting `supervisor_alert`
- **THEN** the failed send is logged and skipped
- **AND** the pet main WebSocket still receives the event

### Requirement: supervisor_user_choice inbound message

The control WebSocket SHALL accept an inbound message of type `supervisor_user_choice` with payload schema:
```
{
  session_id: string,
  alert_id: string,
  button_index: 0 | 1,
  button_text: string
}
```
The backend SHALL log the choice with sid + button_text and SHALL persist it to the `supervisor_hints` table (extending the row that originated the alert, OR inserting a new row of action `user_choice`). Currently the choice does not change main-agent behavior beyond logging — future iterations may map specific button choices to nudge or cancel actions.

#### Scenario: User clicks "让它继续"

- **WHEN** frontend sends `{type:"supervisor_user_choice", payload:{session_id:"X", alert_id:"a1", button_index:0, button_text:"让它继续"}}`
- **THEN** backend logs `supervisor_user_choice` with sid="X" and button_text="让它继续"
- **AND** persists the choice to supervisor_hints

### Requirement: supervisor_toggle inbound message

The control WebSocket SHALL accept an inbound message of type `supervisor_toggle` with payload `{enabled: boolean}`. The backend SHALL update the `[supervisor].enabled` config key, persist to `config.toml` (or runtime overlay), and adjust the supervisor state at runtime: starting the watchdog if newly enabled, cancelling the watchdog task and clearing in-memory queues if newly disabled.

#### Scenario: Enable supervisor at runtime

- **GIVEN** supervisor is currently disabled
- **WHEN** frontend sends `{type:"supervisor_toggle", payload:{enabled:true}}`
- **THEN** backend writes `[supervisor].enabled = true`
- **AND** starts the watchdog asyncio.Task (with normal 30s grace before first scan)
- **AND** acks via a control message `{type:"supervisor_toggle_ack", payload:{enabled:true}}`

#### Scenario: Disable cleans up state

- **GIVEN** supervisor is currently enabled with active state
- **WHEN** frontend sends `{type:"supervisor_toggle", payload:{enabled:false}}`
- **THEN** backend cancels the watchdog task
- **AND** clears nudge_queue for all sids
- **AND** clears in-memory session_activity (durable supervisor_hints rows are kept for audit)
- **AND** sends `supervisor_toggle_ack` with `enabled:false`

### Requirement: pet_focus_session_clicked outbound event (frontend-internal)

When the user clicks the pet supervisor bubble background (not a button), the pet window SHALL invoke the existing `open_code_panel` Tauri command AND post a window message `pet_focus_session_clicked` with payload `{session_id}` so the code-panel window can switch its active session view to that sid. This event is local frontend (window-to-window via Tauri events or BroadcastChannel) — it does NOT go through the WebSocket.

#### Scenario: Clicking bubble opens panel and switches session

- **GIVEN** pet bubble is showing for sid `code-a1b2c3d4`
- **WHEN** the user clicks the bubble background
- **THEN** Tauri `open_code_panel` IPC is invoked (panel becomes visible)
- **AND** the code-panel window receives `pet_focus_session_clicked` event
- **AND** SessionGridView/SessionSidebar in the panel switches active session to `code-a1b2c3d4`

