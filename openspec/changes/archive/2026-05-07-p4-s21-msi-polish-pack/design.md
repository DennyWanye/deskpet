# Design: P4-S21 MSI Polish Pack

## Architecture changes

```
                     +--- Settings UI -- invoke -- Rust cmd
                     |                              |
  Frontend (webview) +--- Toolbar --- invoke --- update_cloud_config
                     |                |              |
                     +--- TrayIcon ---+              +-- HTTP -> backend
                                                                  |
   Voice mic ---> voice_pipeline --[NEW: AgentLoop branch]------> tool_use
                                  --[fallback]--> chat_stream    /
                                                                /
   WS chat ---> chat handler --> AgentLoop --> tool_use --------+
                                       |
                                       v
                          +-- ContextAssembler --+
                          | bundle.history (new)  |
                          | bundle.memory_block   |  (now L3 only)
                          +----------------------+
                                       |
                                       v
                                  build_messages(history=...)
```

## Frontend / Rust IPC contract (#1)

Frontend stops fetching `http://127.0.0.1:8100` directly. Instead:

```ts
// bindings/config.ts
import { invoke } from "@tauri-apps/api/core";

export async function updateCloudConfig(update: CloudConfigUpdate) {
  return invoke<CloudConfigResult>("update_cloud_config", { update });
}
// secret param removed — Rust knows it from BackendInfo state
```

```rust
// src-tauri/src/commands.rs (new file)
#[tauri::command]
pub async fn update_cloud_config(
    state: tauri::State<'_, BackendInfo>,
    update: serde_json::Value,
) -> Result<serde_json::Value, String> {
    let url = format!("http://127.0.0.1:{}/config/cloud", state.port);
    let resp = reqwest::Client::new()
        .post(&url)
        .header("X-Shared-Secret", &state.shared_secret)
        .json(&update)
        .send()
        .await.map_err(|e| e.to_string())?;
    let status = resp.status();
    let body: serde_json::Value = resp.json().await.map_err(|e| e.to_string())?;
    if !status.is_success() {
        return Err(format!("backend {}: {}", status, body));
    }
    Ok(body)
}

#[tauri::command]
pub fn app_exit(app: tauri::AppHandle) { app.exit(0); }
```

`BackendInfo` is a Rust struct already managed by `process_manager` (it tracks the
shared_secret from backend stdout). We expose it as Tauri state.

## ContextBundle history (#16)

```python
# bundle.py — added field
@dataclass
class ContextBundle:
    frozen_system: str = ""
    skill_prelude: str = ""
    memory_block: str = ""           # was L2 + L3; now L3 ONLY
    history: list[dict] = field(default_factory=list)  # NEW: L2 raw
    tool_schemas: list[dict] = field(default_factory=list)
    decisions: AssemblyDecisions = field(default_factory=AssemblyDecisions)
    cost_hint: dict[str, int] = field(default_factory=dict)
```

`MemoryComponent.gather()`:

```python
# was:
memory_block = _render_l2_l3(l2_rows, l3_hits)

# becomes:
memory_block = _render_l3_only(l3_hits)   # L3 stays as text (long-term recall)
bundle.history = [
    {"role": r["role"], "content": r["content"]}
    for r in l2_rows
    if r.get("content") and r.get("role") in ("user", "assistant")
]
```

`main.py:1529`:

```python
_msgs = _bundle.build_messages(
    user_message=_text,
    history=_bundle.history,   # was missing
)
```

**Token budget**: L2 default cap stays at 20 turns (existing). Each turn
truncated only at recall layer (rows trimmed to 200 chars in DB read);
once they're real `messages[]` items, they go in raw — LLM gets full
context. If this overshoots provider context window, MemoryComponent
shrinks `n_recent` proactively (existing logic via `cost_hint`).

## voice_pipeline tool-use route (#13)

```python
# voice_pipeline.py
class VoicePipeline:
    def __init__(self, ..., agent, *,
                 tool_registry_v2: Optional["ToolRegistry"] = None,
                 permission_gate: Optional["PermissionGate"] = None,
                 llm_provider: Optional["LLMProvider"] = None):
        ...
        self.tool_registry_v2 = tool_registry_v2
        self.permission_gate = permission_gate
        self.llm_provider = llm_provider  # raw provider for AgentLoop shim

    async def _handle_user_said(self, text):
        if self.tool_registry_v2 is not None and self.llm_provider is not None:
            await self._run_with_tools(text)
        else:
            await self._run_legacy_chat_stream(text)

    async def _run_with_tools(self, text):
        # Mirror main.py's _run_chat: assemble bundle, build messages,
        # spin AgentLoop, emit tool_call / tool_result via the pipeline's
        # event channel so the WS forwards to frontend.
        ...
```

Ground truth lives in `main.py` (`_run_chat`). voice_pipeline imports a
shared helper to avoid divergence.

## PermissionGate auto_mode (#13 cross-cut)

```python
class PermissionGate:
    def __init__(self, ...):
        ...
        self.auto_mode: bool = False    # NEW

    async def request_permission(self, request):
        if self.auto_mode:
            log.info("permission_auto_allowed", category=request.category)
            return PermissionDecision.ALLOW

        # When voice context, also send a TTS prompt so the user knows
        # to look at the popup
        if request.context.get("source") == "voice":
            tts = self._service_context.get("tts_engine")
            if tts:
                asyncio.create_task(tts.synthesize(
                    "我需要确认才能执行这个操作，请点击屏幕上的允许按钮"
                ))

        # ... existing IPC popup flow
```

Settings UI exposes a switch:

```tsx
<label>
  <input type="checkbox" checked={autoMode}
         onChange={e => onAutoModeChange(e.target.checked)} />
  自动模式（高级）：所有工具自动允许，不弹确认窗口
</label>
```

`onAutoModeChange` sends `{type: "permission_auto_mode_set", payload: {enabled}}`
to the control WS, backend updates `permission_gate_v2.auto_mode = enabled`.

## Bundle config.toml (#12)

```python
# deskpet-backend.spec
datas += [
    ("memory/migrations", "memory/migrations"),
    ("../config.toml", "."),    # NEW — ships at _MEIPASS/config.toml
]
```

```python
# config.py
def _bundle_default_config_path() -> Path | None:
    if getattr(sys, "frozen", False):
        # NEW: try _MEIPASS first
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            c = Path(meipass) / "config.toml"
            if c.is_file():
                return c
        # ... existing exe_dir multi-level fallback
    ...

def seed_user_config_if_missing() -> Path | None:
    user_target = _paths.user_data_dir() / "config.toml"
    if user_target.is_file():
        # NEW: detect legacy schema, auto-migrate
        try:
            with open(user_target, "rb") as f:
                raw = tomli.load(f)
            llm = raw.get("llm", {})
            if isinstance(llm, dict) and ("local" in llm or "cloud" in llm):
                logger.warning(
                    "legacy_llm_schema_detected, migrating to unified [llm]"
                )
                bak = user_target.with_suffix(".legacy-bak")
                shutil.copyfile(user_target, bak)
                source = _bundle_default_config_path()
                if source:
                    shutil.copyfile(source, user_target)
                    logger.info("config_migrated source=%s target=%s", source, user_target)
        except Exception as e:
            logger.warning("config_migrate_check_failed: %s", e)
        return user_target
    # ... existing first-run seed
```

## CREATE_NO_WINDOW for backend (#8)

```rust
// process_manager.rs — at top
#[cfg(windows)]
use std::os::windows::process::CommandExt;

// inside spawn_once after cmd.stdout(Stdio::piped()) ...
#[cfg(windows)]
{
    cmd.creation_flags(0x08000000); // CREATE_NO_WINDOW
}
```

That's it — Windows-only, single line; backend pipes still work because
PyInstaller spec keeps `console=True` for stdout SHARED_SECRET emission.
The flag tells Windows "don't allocate a console window for this child".

## System tray + Quit (#7)

```rust
// lib.rs — inside Builder::default().setup
use tauri::{
    menu::{Menu, MenuItem},
    tray::{TrayIconBuilder, TrayIconEvent},
};

let show = MenuItem::with_id(app, "show", "显示桌宠", true, None::<&str>)?;
let hide = MenuItem::with_id(app, "hide", "隐藏桌宠", true, None::<&str>)?;
let quit = MenuItem::with_id(app, "quit", "退出 DeskPet", true, None::<&str>)?;
let menu = Menu::with_items(app, &[&show, &hide, &quit])?;

let _tray = TrayIconBuilder::new()
    .icon(app.default_window_icon().unwrap().clone())
    .tooltip("DeskPet")
    .menu(&menu)
    .on_menu_event(|app, event| match event.id.as_ref() {
        "show" => { let _ = app.get_webview_window("main").map(|w| w.show()); }
        "hide" => { let _ = app.get_webview_window("main").map(|w| w.hide()); }
        "quit" => { app.exit(0); }
        _ => {}
    })
    .build(app)?;
```

Toolbar Quit button just calls `invoke("app_exit")`, equivalent to tray Quit.

## External cab MSI (#2)

```powershell
# build-msi.ps1 — patch
$content -replace `
    '<Media Id="1" Cabinet="app\.cab" EmbedCab="yes" />', `
    '<MediaTemplate EmbedCab="no" CompressionLevel="mszip" MaximumUncompressedMediaSize="1900" />'
```

Result: light.exe outputs `DeskPet.msi` (~10 MB) + N `*.cab` files (each
< 1.9 GB). Distribute as a folder or zip; user runs the .msi from the
folder.

## Test strategy

### Backend pytest
- `test_context_bundle_history.py` — assert MemoryComponent populates `history` and main.py's call signature handles it
- `test_permission_gate_auto_mode.py` — set auto_mode=true, all categories ALLOW
- `test_seed_user_config_legacy_migration.py` — write legacy `[llm.cloud]` → invoke seed → verify backup created + new schema in place

### Smoke (manual after rebuild)
- Install MSI to non-C: drive, verify C: free didn't drop by 5 GB
- Launch from start menu — no cmd window visible
- Open Toolbar — Quit button visible, tray icon present
- Settings → change LLM model → Save → no fetch error
- Chat: "我喜欢喝可乐" → "我喜欢喝什么?" — should answer 可乐 (history works)
- Voice: "帮我桌面生成一个 todo.txt 内容是吃饭买菜" → PermissionPopup → 允许 → file appears
- Settings → 自动模式 ON → voice request again → no popup, file appears immediately

### Build pipeline self-check
- `cd backend && python -m pytest tests/test_context_bundle_history.py tests/test_permission_gate_auto_mode.py tests/test_seed_user_config_legacy_migration.py -x`
- `python -m PyInstaller deskpet-backend.spec --noconfirm --clean --distpath dist-msi` — exit 0
- `pwsh scripts/build-msi.ps1` — produces .msi in `tauri-app/src-tauri/target/release/bundle/msi/`
- Manual install + smoke test list above
