# DeskPet — Current State

> **Purpose:** Minimal "rehydration" document for any new Claude session. Read
> this first before touching anything. Last updated at the close of each sprint
> or at major inflection points.

**Last updated:** 2026-04-21 (P2-2-F1 ship — Whisper 短音频 pad + hotwords; 等手测回归)
**Current version:** `v0.2.0` (first public beta; next `v0.2.x` will use rotated pubkey)
**Active branch:** `master` (P2-2 + P2-2-F1 已 push 到 origin)
**Active tag:** `v0.2.0` at commit `718d70a`; `p2-2-verified` at `f91e264`

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
| 2 — Polish & distribute | **P2-0** | ✅ complete | S1–S7 all shipped; v0.2.0 public; HANDOFF finalized 2026-04-15 |
| 2 — Polish & distribute | **P2-1** | ✅ complete (local) | S1 ✅ OpenAI-compat provider; S2 ✅ HybridRouter; S3 ✅ API key + SettingsPanel; S6 ✅ TTFT metrics + `/metrics`; S7 ✅ Fallback E2E via MockTransport; S8 ✅ BillingLedger + BudgetHook + Asia/Shanghai rollover; **S4/S5 cut 2026-04-15** (PersonaRegistry deferred to Phase 3). All merged to local `master`; push + tag pending user call. |
| 2 — Polish & distribute | **P2-2** | ✅ complete | M1 ✅ VAD barge-in + always-on mic (`ea75f6e`); M2 ✅ PCM 流式播放 + RMS lip-sync (`2eeacca`/`f770305`/`1d0b548`); M3 ✅ VoiceConfig + dynamic VAD threshold + per-frame barge-in re-evaluation (`c47ba9d`/`4abe1ee`/`431bcf0`/`6f7b82a`)；真机手测通过 (`f91e264`)，tag `p2-2-verified`；256/256 pytest 全绿。 |
| 2 — Polish & distribute | **P2-2-F1** | ✅ merged, pending手测 | Whisper 短音频准确率 follow-up: [asr].hotwords 偏置 + 短音频（<3s）前后 pad 300ms 静音。267/267 pytest 全绿。需要用户真机复测"讲个笑话"等短句命中率。`scripts/perf/asr_accuracy.py` 做离线字符级 WER 对比（样本目录 .gitignore，每人录自己的）。handoff: `p2-2-f1-whisper-short-audio.md` |
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
| S8 | `handoffs/p2s8-key-rotation.md` | Updater signing key rotated (passphrase + new pubkey) |

## Completed P2-1 slices

| Slice | Status | Theme |
|-------|--------|-------|
| S1 | ✅ merged | OpenAICompatibleProvider replaces OllamaLLM; unit + integration tests |
| S2 | ✅ merged | HybridRouter (local_first + circuit breaker) wraps local + optional cloud provider; config split `[llm]` → `[llm]` + `[llm.local]` + optional `[llm.cloud]`; 19 router tests + 3 config tests |
| S3 | ✅ merged | API key via OS Credential Manager (keyring crate) + Tauri commands + backend `DESKPET_CLOUD_API_KEY` env handoff; `SettingsPanel` with cloud profile / strategy / daily-budget sections; WS `provider_test_connection` handler |
| S6 | ✅ merged | Prometheus `llm_ttft_seconds` Histogram; `/metrics` endpoint with secret-or-dev-mode auth; TTFT instrumentation in `HybridRouter.chat_stream`; `scripts/ttft_cloud.py` smoke; `BudgetHook` type skeleton (allow_all default) |
| S7 | ✅ merged | Fallback E2E pytest harness using `MockTransport` (no real cloud hits) with `max_iters` guard against hanging tests |
| S8 | ✅ merged | `BillingLedger` (aiosqlite, `Asia/Shanghai` daily rollover, configurable tz); `budget_status` WS handler; `budget_exceeded` toast UI; `BudgetHook` implementation denying cloud when over budget; local route always free; `budget_reason` propagated via `LLMUnavailableError` (race-free) |

## Real Test (UI E2E, 2026-04-15 post-merge)

6/6 manual scenarios via Claude Preview MCP + live backend in
`DESKPET_DEV_MODE=1`:
1. Live2D render + `connected` indicator.
2. `SettingsPanel` structure + `percent_used` renders as `0.0%`
   (validates Bug-1 fix: backend was returning 0..1 fraction, UI
   contract says 0..100).
3. Empty apiKey → "测试连接" shows guard hint.
4. Garbage apiKey → "失败: health check failed (bad key, wrong URL,
   or unreachable)" — validates Bug-2 fix (`provider_test_connection`
   was returning `{ok:false}` without an `error` field, so UI rendered
   "失败: unknown").
5. Chat input → local LLM (Gemma) streaming reply confirmed in both
   DOM and App fiber state.
6. Fiber-level injection of `chat_response.budget_exceeded=true` →
   red fixed toast banner renders at top-right (z-index 2000),
   bg `rgb(185,28,28)`, text `"今日云端预算已用尽，已降级到本地模型。
   （daily_budget_exceeded:X/Y）"`. Minor UX: toast briefly overlaps
   FPS/connected badges — acceptable for an alert.

Both bugs were invisible to pytest (type assertions are soft
comments) and invisible to tsc (types said 0..100 but backend wrote
0..1). Both were caught by Real Test only. See
`feedback_real_test.md` + `feedback_cross_layer_contract.md`.

## Pending follow-ups (not blocking P2-1)

1. **v0.2.0 → v0.2.x self-update smoke test** — the next `v0.2.x`
   release will be the first signed by the rotated key
   (`5F623E5CDBAA4C5A`). Clients on v0.2.0 have the **old** pubkey
   (`609610CD2AB388D1`) baked in, so their self-update will
   deliberately fail; they need a one-time manual reinstall. Confirm
   this expected failure on a v0.2.0 box, then confirm that a machine
   with v0.2.1 installed manually can self-update to v0.2.2 cleanly.
   See `p2s8-key-rotation.md` + `p2s7-release-v0.2.0.md` §
   "Post-push verification plan".
2. **Release-notes template** — workflow currently publishes with
   GitHub-generated notes. Should cross-link the relevant CHANGELOG
   section.
3. **First `v0.2.x` after rotation** — add a CHANGELOG note explaining
   why v0.2.0 users must manually reinstall this one release
   (pubkey rotation by design; see `p2s8-key-rotation.md`).

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

**For pushing P2-1 to origin** (short, user-gated):
> "本地有 35 个 commit（P2-1 S3/S6/S7/S8）还没 push。请先让我 review
> `git log origin/master..HEAD`，确认无误后再 `git push origin master`。
> 不要带 `--force`，如果被 non-fast-forward 拒绝就停下让我来。"

**For v0.2.1 打点验证新密钥** (short, ~15 min — good first move after
P2-1 push lands, since P2-1 gives v0.2.1 real content):
> "请帮我在 `master` 上 bump 到 v0.2.1、写一段 CHANGELOG 说明 pubkey
> 已轮换 (v0.2.0 用户需手动重装一次) + 新增 P2-1 云端 LLM 切换 /
> SettingsPanel / BillingLedger 等功能，打 tag 推上去观察 CI 能否用新
> 密钥成功签名。参考 `docs/superpowers/handoffs/p2s8-key-rotation.md`
> § Follow-ups。"

**For P2-1 → P2-2 brainstorming** (full session):
> "P2-1 收官了。请用 superpowers 的 brainstorming skill 引导我讨论
> P2-2 Sprint 的范围。先读 `docs/superpowers/plans/2026-04-14-phase2-v6-roadmap.md`
> 和这份 STATE.md 里的 P2-1 完成清单。"
