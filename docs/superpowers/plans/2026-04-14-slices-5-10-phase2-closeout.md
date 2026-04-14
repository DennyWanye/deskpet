# Phase 2 Closeout — Slices S5–S10 HANDOFF

**Date**: 2026-04-14
**Scope**: Close out V5 plan residuals R5, R9, R10, R11, R12, R13, R14, R15, R16, R18, R19, R20.
**Mode**: Autonomous slice-based execution (codingsys).

---

## Summary

Seven commits on `master`, all under codingsys quality gates (pytest + tsc
+ manual smoke where applicable). Backend test count went from 92 → 122
passing (+30); frontend tsc stayed green across all slices.

```
5477a9e test(backend): E2E integration tests via real FastAPI app (S10)
c64c3fb feat(backend): CosyVoice 2 TTS provider with edge-tts fallback (S9)
c05462b feat(backend): 4-tier VRAM classifier + clipboard/reminder tools (S8)
120b9e3 feat(observability): versioned SQLite migrations + crash reporters (S7)
133994d feat(security): redaction filter + tool confirmation + frontend secret fetch (S6)
2781672 feat(frontend): Live2D emotion/action binding + interrupt UI (S5)
```

## Slice-by-slice

### S5 — Frontend Live2D binding + interrupt UI (R5, R18, R19)

Live2DCanvas became a `forwardRef` handle exposing `setExpression(name)` and
`playMotion(group, index, priority=FORCE)`. App.tsx subscribes to the agent's
emotion/action stream and drives the live model; Escape key + stop button send
`interrupt` over the control WS.

### S6 — Security triad (R12, R13, R14)

- **Shared-secret fetched from Rust via Tauri invoke** instead of hardcoded.
- **RedactingMemoryStore** decorator wraps SqliteConversationMemory — regex PII/secret patterns
  (Anthropic key ordered before generic API_KEY for specificity) sanitize content on write.
- **Tool confirmation gate** — `ToolSpec.requires_confirmation` + `confirm` callback
  with `_deny_all` fail-closed default; fronting a dialog is an explicit opt-in.

### S7 — Persistence + crash reports (R15, R16)

- `memory/migrations/001_initial.sql` + `memory/migrator.py` — discovers `*.sql`,
  tracks `schema_migrations`, idempotent.
- `observability/crash_reports.py` — chained `sys.excepthook` +
  `asyncio.set_exception_handler`, writes `crash_reports/py-<ts>.log`.
  `tauri-app/src-tauri/src/crash_reports.rs` — chained `panic::set_hook` writing
  `rust-<ts>.log`.

### S8 — VRAM 4-tier classifier + low-risk tools (R9, R10)

- `observability/vram.py` — `HardwareTier` dataclass + `classify_tier(vram_gb)`:
  flagship ≥35GB / standard ≥25GB / economy ≥15GB / minimal. Startup banner logs
  detected tier + recommended llm/asr/tts so dispatch is traceable.
- `tools/clipboard.py` — Win32 CF_UNICODETEXT via ctypes, tkinter fallback, lazy
  imports, exception-swallowing.
- `tools/reminder.py` — in-memory thread-safe store + HH:MM-prefixed listing tool.

### S9 — CosyVoice 2 local TTS provider (R11)

- `providers/cosyvoice_tts.py` — tries `cosyvoice.CosyVoice2` with weights under
  `backend/assets/cosyvoice2/`; on any failure (no package, no weights, no GPU,
  init error) falls back transparently to `EdgeTTSProvider` with the configured
  voice. `.active_backend` exposes which one won.
- Config-driven: `[tts] provider = "cosyvoice2"` to activate; default stays
  `edge-tts` so Phase 1 deploys are untouched.

### S10 — E2E integration tests (R20)

`tests/test_e2e_integration.py` boots the real `main.app` with in-process fakes
replacing LLM/ASR/TTS. 8 tests cover: full chat roundtrip, unknown msg error,
audio-WS auth gate, cross-channel interrupt dispatch (with and without active
pipeline), RedactingMemoryStore runtime behaviour, fail-closed tool gate,
connection tracking lifecycle.

## Gate results

| Gate | Result |
|------|--------|
| `backend/ pytest`  | **122 passed, 1 skipped** (was 92) |
| `tauri-app/ tsc`   | **exit 0** |
| Working tree       | clean |

## V5 plan requirements — status

| ID  | Requirement                                   | Status |
|-----|------------------------------------------------|--------|
| R5  | Live2D emotion/action frontend binding        | ✅ S5  |
| R9  | 4-tier VRAM dispatch                          | ✅ S8  |
| R10 | Clipboard + reminder tools                    | ✅ S8  |
| R11 | CosyVoice 2 local TTS                         | ✅ S9 (falls back to edge-tts if env incomplete) |
| R12 | WS shared-secret token, fetched by frontend   | ✅ S6  |
| R13 | PII / secret redaction before memory write    | ✅ S6  |
| R14 | Destructive-tool confirmation                 | ✅ S6  |
| R15 | Versioned SQLite migrations                   | ✅ S7  |
| R16 | Python + Rust crash reporters                 | ✅ S7  |
| R18 | Interrupt button + Escape shortcut            | ✅ S5  |
| R19 | Live2D action trigger                         | ✅ S5  |
| R20 | Automated E2E coverage                        | ✅ S10 |

## Known gaps / follow-ups

1. **CosyVoice 2 real-weight path** is behind `pragma: no cover` — validates via
   manual `scripts/tts_smoke.py` once a dev env has the package + GPU. The
   fallback path is fully tested.
2. **Streaming granularity for CosyVoice 2** is coarse: local path currently
   collects the full waveform then yields one chunk. Finer streaming (~80ms
   token-level) is Phase 3.
3. **Zero-shot voice cloning** for CosyVoice 2 needs a `prompt.wav` +
   `prompt.txt` under `model_dir/asset/`; current default uses the built-in
   SFT voice (`中文女`).
4. **tests/test_e2e_pipeline.py** remains as a manual live-backend smoke
   script. `test_e2e_integration.py` is the automated equivalent; the manual
   one is useful when you want to hear actual audio with a running Ollama.
5. The audio WS hermetic path is partially tested — the `_pipelines` dispatch
   and auth gate are covered, but a full VAD→ASR→LLM→TTS run still needs the
   manual script because silero-vad / faster-whisper / edge-tts are heavy.

## How to resume

```bash
# Verify green
cd backend && .venv/Scripts/python -m pytest
cd tauri-app && npx tsc --noEmit -p .

# Activate CosyVoice 2 (when env ready)
#  1. pip install cosyvoice torchaudio (inside backend/.venv)
#  2. edit config.toml: [tts] provider = "cosyvoice2"
#  3. restart uvicorn
# Startup log shows either "cosyvoice2_loaded" or "cosyvoice2_fallback_active".
```
