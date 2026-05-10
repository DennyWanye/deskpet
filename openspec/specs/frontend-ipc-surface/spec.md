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

