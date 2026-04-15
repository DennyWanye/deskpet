# DeskPet — Current State

> **Purpose:** Minimal "rehydration" document for any new Claude session. Read
> this first before touching anything. Last updated at the close of each sprint
> or at major inflection points.

**Last updated:** 2026-04-15 (end of Sprint P2-0)
**Current version:** `v0.2.0` (first public beta)
**Active branch:** `master`
**Active tag:** `v0.2.0` at commit `718d70a`

---

## Just shipped

- **v0.2.0 public beta** — GitHub Release published, 5 assets uploaded, all
  signatures verified.
  <https://github.com/DennyWanye/deskpet/releases/tag/v0.2.0>
- **CI release pipeline** — `.github/workflows/release.yml` is production-ready
  after 3 debug iterations. Key guardrails in place:
  - `$LASTEXITCODE` check + `$PSNativeCommandUseErrorActionPreference = $true`
    prevent PowerShell silent-success on native command failures.
  - "Verify bundle artifacts exist" step dumps the full bundle tree + names
    missing files on failure (turns silent failures into loud ones).
  - `bundle.createUpdaterArtifacts: true` in `tauri.conf.json` required for
    `.sig` emission — this was the blocker that killed CI runs #1 and #2.
- **Changelog** — `CHANGELOG.md` covers every P2-0 slice. Keep a Changelog
  format, SemVer.

## Phase / Sprint progress

| Phase | Sprint | Status | Notes |
|-------|--------|--------|-------|
| 1 — MVP loop | — | ✅ complete | v0.1.0 internal milestone |
| 2 — Polish & distribute | **P2-0** | ✅ complete | S1–S7 all shipped; v0.2.0 public |
| 2 — Polish & distribute | **P2-1** | ⏳ not started | 6 decision points pending (see roadmap §3.2) |
| 3 — Backend auto-launch | — | ⏳ future | Blocker follow-up: bundle Python backend |
| 4 — v1.0 GA | — | ⏳ future | Once P2/P3 land |

## Completed P2-0 slices (quick index)

| Slice | Handoff | Theme |
|-------|---------|-------|
| S1 | `handoffs/p2s1-icon-branding.md` | Icon set + favicon |
| S2 | `handoffs/p2s2-updater.md` | Updater plugin + Ed25519 signing |
| S3 | `handoffs/p2s3-memory-multi-session.md` | MemoryPanel `全部会话` tab |
| S4 | `handoffs/p2s4-perf-scripts.md` | `cold_boot.py` + `rss_sampler.py` |
| S5 | `handoffs/p2s5-vn-dialog-nit.md` | DialogBar empty placeholder + mic idle fix |
| S6 | `handoffs/p2s6-chat-history-a11y.md` | Focus trap + Escape close |
| S7 | `handoffs/p2s7-release-v0.2.0.md` | v0.2.0 tag + CI release |

## Pending follow-ups (not blocking P2-1)

1. **v0.2.0 self-update smoke test** — run on a Windows box with v0.1.0
   installed, confirm updater flow end-to-end.
   See `p2s7-release-v0.2.0.md` § "Post-push verification plan".
2. **Rotate signing key with passphrase** — current key has empty passphrase.
   Generate new keypair, upload both `TAURI_SIGNING_PRIVATE_KEY` and
   `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` secrets, update `tauri.conf.json`
   pubkey, ship v0.2.1. See `p2s7-release-v0.2.0.md` § "Follow-ups".
3. **Release-notes template** — workflow currently publishes with
   GitHub-generated notes. Should cross-link the relevant CHANGELOG section.

## Key files to read before any work

Pick the 2–3 that match your task; don't read everything.

- **Roadmap**: `docs/superpowers/plans/2026-04-14-phase2-v6-roadmap.md`
  - §3.2 covers Sprint P2-1 decision points.
- **Architecture overview**: `CLAUDE.md` (project-level instructions).
- **Release pipeline**: `.github/workflows/release.yml` + `scripts/release.ps1`
  + `tauri-app/src-tauri/tauri.conf.json` (bundle/updater config).
- **Perf gates**: `docs/PERFORMANCE.md` (5 scripts: 3 manual + cold_boot +
  rss_sampler).
- **Handoff of interest**: `docs/superpowers/handoffs/p2s{N}-*.md` for the
  specific slice you're touching.

## Environment gotchas (bit us this sprint)

- **PowerShell + native commands**: `$ErrorActionPreference = 'Stop'` does NOT
  trip on non-zero exit codes from native binaries. Always add
  `$PSNativeCommandUseErrorActionPreference = $true` (PS 7.3+) AND an explicit
  `if ($LASTEXITCODE -ne 0) { throw }` guard.
- **npm arg passthrough**: `npm run X -- --flag` passes one `--`; a second `--`
  gets interpreted by the downstream CLI as POSIX end-of-options. Use exactly
  one.
- **Tauri updater artifacts**: `bundle.createUpdaterArtifacts: true` in
  `tauri.conf.json` is REQUIRED for `.sig` emission. Without it, builds
  silently skip signing even with `TAURI_SIGNING_PRIVATE_KEY` in env.
- **Windows Python Popen**: `_winapi.CreateProcess` does NOT resolve relative
  exe paths against CWD. Use `Path.resolve()` before passing to `subprocess`.
- **Windows Python JSON**: `open(path)` on Windows uses GBK by default.
  Always pass `encoding='utf-8'` for UTF-8 JSON files.
- **Tauri dev orphan processes**: stopping the dev server on Windows can leave
  orphan `deskpet.exe` + Vite processes. `taskkill /f /im deskpet.exe` before
  restart. (See `MEMORY.md`.)

## Suggested next-session opening prompts

**For the smoke test** (quick, ~10 min session):
> "请指导我执行 v0.2.0 自更新冒烟测试。参考
> `docs/superpowers/handoffs/p2s7-release-v0.2.0.md` 的 Post-push
> verification plan。"

**For P2-1 brainstorming** (full session):
> "请用 superpowers 的 brainstorming skill 引导我讨论 P2-1 Sprint 的 6 个
> 决策点。先读 `docs/superpowers/plans/2026-04-14-phase2-v6-roadmap.md` §3.2
> 和 `docs/superpowers/STATE.md`。逐个决策点抛 1-2 个发散问题，让我回答后
> 收敛成 spec，再写 plan。"

**For signing key rotation** (side-task, ~30 min):
> "请执行签名密钥轮换：生成带口令的新 Ed25519 密钥对、更新
> `tauri.conf.json` pubkey、指导我上传 GitHub secrets、发 v0.2.1 打点
> 验证新密钥。参考 `docs/superpowers/handoffs/p2s7-release-v0.2.0.md`
> § Follow-ups。"
