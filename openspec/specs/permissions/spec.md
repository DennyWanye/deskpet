# permissions Specification

## Purpose
TBD - created by archiving change p4-s21-msi-polish-pack. Update Purpose after archive.
## Requirements
### Requirement: PermissionGate MUST support an auto_mode short-circuit
The system SHALL expose an `auto_mode: bool` flag on `PermissionGate`.
When `auto_mode == True`, every `check()` call SHALL return
`PermissionDecision(allow=True, source="auto-mode")` immediately,
bypassing cache, deny patterns, and the responder. This is opt-in via
Settings UI; default is False. Toggling MUST be possible at runtime
through a control-channel IPC message.

#### Scenario: Auto-mode allows shell when responder absent
- **GIVEN** `gate.auto_mode = True` and no responder is wired
- **WHEN** caller checks `("shell", {"command": "echo"}, "sess")`
- **THEN** decision is `allow=True`, `source="auto-mode"`

#### Scenario: Auto-mode beats deny patterns intentionally
- **GIVEN** `gate.config.shell_deny_patterns = ["rm -rf"]` and `gate.auto_mode = True`
- **WHEN** caller checks `("shell", {"command": "rm -rf /etc"}, "sess")`
- **THEN** decision is `allow=True` (auto-mode is explicit user opt-in)

#### Scenario: IPC message flips the flag
- **WHEN** control WS receives `{type: "permission_auto_mode_set", payload: {enabled: true}}`
- **THEN** `permission_gate_v2.auto_mode == True`
- **AND** the WS replies with `{type: "permission_auto_mode_response", payload: {enabled: true}}`

### Requirement: Voice-context permission requests MUST trigger TTS narration
When `current_source == "voice"` and the gate is about to fire a
PermissionPopup, the system SHALL ALSO invoke the wired TTS engine
to speak a Chinese cue line ("我需要确认才能执行 X，请点击屏幕上的允许按钮"
or similar). TTS runs in a fire-and-forget task so the popup is not
blocked by audio synthesis. TTS failures SHALL NOT block the popup.

#### Scenario: Voice-context popup speaks the cue
- **GIVEN** `gate.current_source = "voice"`, TTS engine attached, responder ready
- **WHEN** caller checks a non-default-allow category (e.g. `shell`)
- **THEN** `tts.synthesize(...)` is called with a string containing "请点击"
- **AND** the popup IPC fires in parallel

#### Scenario: Text-context popup is silent
- **GIVEN** `gate.current_source = None` or `"text"`, TTS attached
- **WHEN** caller checks `shell`
- **THEN** TTS is NOT invoked
- **AND** popup behaves as before

