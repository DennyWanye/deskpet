# Changelog

All notable changes to DeskPet are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.6.0-phase4-rc2] — 2026-04-25

**Phase 4 full-stack integration on top of rc1.** Every P4 component is now
live in the running backend, not just registered.

### Added

- **S14 ContextAssembler in chat handler** — every turn now runs
  `assembler.assemble()` BEFORE `chat_stream`, builds an OpenAI-shape
  messages list (frozen_system + memory_block + skill_prelude + history +
  user) and feeds it to the agent. Decisions auto-stamped with timestamp +
  session_id; ContextTracePanel renders a real timeline.
- **S15 Embedder + L3 Retriever** — `Embedder` wired with mock fallback (no
  cold-start cost without BGE-M3 weights). `SessionDB` at `<data>/state.db`
  as canonical L2. `VectorWorker` drains writes into vec0. `Retriever` ships
  RRF fusion (vec / fts / recency / salience) into MemoryManager as L3.
- **S15 dual-write memory adapter** — legacy `memory_store` writes are
  mirrored to `SessionDB` so L3 search has content to retrieve, without
  breaking the existing `agent_engine` contract.
- **S15 MCPManager bootstrap** — lifespan now reads `config.raw["mcp"]` and
  brings up enabled servers via `create_and_start_from_config`. Empty/no
  config = no-op; failures isolated per server.
- **S15 classifier embedder protocol** — `Embedder.embed()` adapter
  unifies the classifier's `embed(texts) -> list[list[float]]` shape with
  the retriever's canonical `encode(texts) -> ndarray`. No more
  `'Embedder' object has no attribute 'embed'` warnings on every turn.
- **S15 full-stack bench** — `scripts/bench_phase4_full_stack.py`:
  - Cold-start (mock embedder): **98ms** (SLO <5s) ✅
  - Per-turn assemble p95: **48ms** (SLO <370ms) ✅
- **AssemblyDecisions front-end aliases** — `latency_ms`, `token_breakdown`,
  `reason`, `timestamp`, `session_id` emitted alongside canonical fields.

### Tests

- 628 passing in deskpet regression (+10 from rc1's 618).
- 4/4 S14 assembler-hook tests.
- 6/6 S15 full-stack tests.
- Frontend `tsc --noEmit` clean; no UI churn (S11 already declared the
  trace fields).

### Open follow-ups (future slice, not blocking rc2)

- Real BGE-M3 cold-start measurement (need user to run download_bge_m3.py)
- Native Tauri E2E smoke (Preview MCP renders 0×0 viewport)
- OpenSpec archive

## [0.6.0-phase4-rc1] — 2026-04-24

**Phase 4 Poseidon agent harness + long-term memory — rc1 (components complete).**

Ships the full P4 stack of components plus IPC surface. Backend session
integration (wiring `ContextAssembler` / `MemoryManager` / `SkillLoader` /
`MCPManager` into `main.py`'s per-turn flow) is scoped to a follow-up S13
sprint — rc1 exposes each component via the control-WS but they operate
as standalone services that the UI can exercise today via `p4_ipc.py`.

### Added

- **Three-layer memory (L1 / L2 / L3)**
  - L1 `FileMemory` (MEMORY.md / USER.md) with salience eviction + atomic
    write. 50KB/20KB caps. Frozen-snapshot pattern to keep prompt cache hot.
  - L2 `SessionDB` schema v9 migration — adds `embedding BLOB`, `salience`,
    `decay_last_touch`, `user_emotion`, `audio_file_path` columns plus
    `messages_vec` virtual table using `sqlite-vec` (cosine, 1024-dim).
  - L3 `Retriever` — hybrid RRF fusion of FTS5 / vector / recency / salience.
  - `MemoryManager` facade: parallel per-layer recall with graceful degradation
    (one layer failing never cancels the others).
- **Embedding pipeline**
  - `BGE-M3 INT8` embedder (`deskpet.memory.embedder`) with mock fallback.
  - `VectorWorker` batches writes on a 1s interval; backfills historical turns.
- **ContextAssembler + Context Compressor**
  - 6-component registry with per-task `AssemblyPolicy`, `BudgetAllocator`
    sizing sections off `context_window × budget_ratio`.
  - `ContextCompressor` rolling-summary when transcript ≥ 0.75 × window
    (keep first_n=3 + last_n=6). 29/29 tests.
  - `TaskClassifier` (rule → embed → LLM fallback) surfaces `classifier_path`
    on each decision for the trace UI.
- **MCP client (P4-S9)**
  - `MCPManager` with `AsyncExitStack` lifecycle, `stdio_client` / `sse_client`
    / `streamablehttp_client` transports.
  - Exponential-backoff reconnect, fast-fail <50ms on dead sessions, namespace
    tools as `mcp_{server}_{tool}`. 13/13 tests.
- **Skill system (P4-S10)**
  - `SkillLoader` with YAML frontmatter, 1s watchdog debounce, `${args[N]}`
    substitution, sandboxed `python -I` subprocess.
  - Ships three built-in skills: `recall-yesterday`, `summarize-day`,
    `weather-report`. 16/16 tests.
- **Front-end panels (P4-S11)**
  - `MemoryPanel` — four tabs: 对话 / L1 档案 / 向量搜索 / 技能.
  - `ContextTracePanel` — decision timeline, classifier_path + latency +
    total_tokens, CSS-only stacked token-breakdown bar, >=90% budget warn.
- **Control-WS IPC surface (P4-S11)**
  - `backend/p4_ipc.py` — 5 handlers: `skills_list`, `decisions_list`,
    `memory_search`, `memory_l1_list`, `memory_l1_delete`. All degrade
    gracefully when their service isn't registered yet. 22/22 tests.
- **Phase-4 bench** — `scripts/bench_phase4.py` validates SLO:
  - `FileMemory.read_snapshot` p95 0.22ms (SLO 10ms).
  - `MemoryManager.recall(L1+L2)` p95 1.70ms (SLO 30ms).
  - `SkillLoader.list_skills` p95 <1ms (SLO 5ms).

### Deferred to S13 integration sprint

- Wiring P4 services into `main.py` session flow (currently each component
  works standalone; `p4_ipc.py` returns empty + `reason` until wired).
- Full-stack SLO validation (ContextAssembler p95, first-byte p50, prompt
  cache hit rate) — requires live LLM + integrated pipeline.
- Cold-start ≤ 90s gate (unchanged from P3; BGE-M3 still lazy-loaded).
- Native Tauri E2E smoke (Preview MCP only serves the Vite dev server —
  no native window rendering in CI).

### Tests

- 612 passing in deskpet regression (1 timing-flaky in isolation passes).
- 22/22 P4 IPC handlers.
- Frontend: `tsc --noEmit` clean, `vite build` clean.

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
