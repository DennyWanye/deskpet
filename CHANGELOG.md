# Changelog

All notable changes to DeskPet are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] — 2026-04-15

First public beta. V6 Phase 2 Sprint P2-0 wraps up.

### Added

- **Updater pipeline**: signed auto-update via Tauri 2 updater plugin
  backed by GitHub Releases. `.github/workflows/release.yml` runs on
  `v*.*.*` tags, builds the Windows installer + `.sig`, and publishes
  `latest.json` so existing installs can self-update.
  (P2-0-S2, commits `fc3e2ee` / `b35b1e7`)
- **MemoryPanel multi-session view**: new `本会话 / 全部会话` scope tab
  lets users inspect every session's persisted turns without juggling
  `session_id`s. Backend already supported `scope=all`; this slice is
  UI only. (P2-0-S3, commit `09508fe`)
- **Performance scripts**: two new gates under `scripts/perf/`:
  - `cold_boot.py` — automates the V5 `<30s` cold-boot gate by racing
    the backend's `SHARED_SECRET` stdout line against `/health`.
  - `rss_sampler.py` — psutil-backed RSS sampler with peak + growth-rate
    gates; replaces "open Task Manager and squint".
  (P2-0-S4, commit `16fedb2`)
- **DialogBar empty-state placeholder**: on first run the VN bar now
  shows `按住下方按钮说话，或输入消息开始聊天…` in muted italic
  instead of rendering blank. (P2-0-S5, commit `4d68e4f`)
- **ChatHistoryPanel keyboard a11y**: close button auto-focuses on open,
  Tab/Shift+Tab trap focus within the dialog, Escape closes.
  (P2-0-S6, commit `bd22fa6`)
- **Icon branding**: full Windows + macOS + Linux + favicon icon set
  regenerated from an SVG source via
  `scripts/branding/rebuild-icons.ps1`. (P2-0-S1)

### Changed

- `docs/PERFORMANCE.md` now documents five perf gates (adds cold_boot
  + rss_sampler). The previous "what these don't do" caveats for
  cold-boot and frontend RSS are gone — both are automated.
- `docs/RELEASE.md` rewritten around the GitHub Releases CI flow; the
  "roll this manually" path is still there but is now the fallback.

### Fixed

- Dead `messagesEndRef` + no-op `useEffect` in `App.tsx` removed.
  (P2-0-S5)
- `scripts/e2e/drive_via_cdp.py::step_dialog_bar` now calls
  `ensure_mic_idle` before every follow-up send, not just once.
  Prevents spurious 60s chat timeouts when a prior reply accidentally
  armed the mic. (P2-0-S5)

### Security

- Updater signing key rotated before any release was signed — an
  earlier terminal session leaked the encrypted-but-empty-passphrase
  private key into chat. Safe because no tagged release existed yet.
  New pubkey is live in `tauri.conf.json`. (commit `b35b1e7`)
- **Follow-up before wider distribution**: rotate the signing key
  again with a non-empty passphrase and upload
  `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` alongside the private key. See
  `docs/RELEASE.md` § "Rotating the signing key".

### Known issues / follow-ups

- Backend is still assumed to be launched manually
  (`python backend/main.py`). The Tauri app does not yet spawn the
  backend itself; Phase 3 work.
- `cold_boot.py` measures warm-disk boots today (~1.7s on a dev box).
  For true cold-from-reboot numbers, flush filesystem cache between
  runs.
- `ChatHistoryPanel` does not restore focus to the opening button on
  close. Only one entry point exists so this degrades gracefully; if
  more modals ship, add a shared focus-restore hook.

## [0.1.0] — 2026-03

Unreleased internal milestone. Phase 1 complete: voice pipeline, VN
dialog bar, per-session SQLite memory, MemoryPanel (single-session).
No public artifacts.
