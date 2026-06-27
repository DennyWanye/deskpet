# P2-0-S3 MemoryPanel 多会话 UI — HANDOFF

**Date**: 2026-04-15
**Sprint**: V6 Phase 2 · Sprint P2-0 · Slice 3
**Status**: ✅ Complete
**Target version**: feeds into `v0.2.0-phase2-beta1`

## What shipped

Added a "本会话 / 全部会话" scope tab to the existing MemoryPanel so
users can inspect every session's turns in one view. Backend required
zero changes — `memory_list` / `memory_clear` / `memory_export` all
already accept `scope: "session" | "all"`.

## Commits (master)

| SHA | Subject |
|---|---|
| `09508fe` | feat(memory): add scope tab to MemoryPanel (本会话 / 全部会话) |

## Frontend changes (`tauri-app/src/components/MemoryPanel.tsx`)

- New `scope` state (`"session" | "all"`); `refresh()` branches on it
  to build the right `memory_list` payload.
- Tab row with `role="tablist"` and `aria-selected` between the header
  and the action buttons. `data-testid="memory-scope-session"` /
  `memory-scope-all`.
- Turn rows in scope=all show a short session tag (last 10 chars of
  `session_id`) so cross-session provenance is visible without
  overwhelming the list. Full id lives on `data-turn-session` + tooltip.
- Header label swaps between `记忆管理 · <sessionId>` and
  `记忆管理 · 全部会话`.

## E2E coverage

New `scripts/e2e/e2e_memory_all_sessions.py`:

1. Seed session A (2 chats → 4 turns) + session B (1 chat → 2 turns)
2. Assert `scope=session` on each returns **only** that session's turns
3. Assert `scope=all` sees turns from **both** sessions
4. Assert `scope=session` clear on B leaves A intact
5. Assert `scope=all` clear wipes everything

Against local dev backend: **all 4 assertions PASS**.
Existing `e2e_memory_panel.py` still PASSes (no regression).

## Gates

- ✅ `npx tsc --noEmit` clean
- ✅ `e2e_memory_all_sessions.py` ALL PASS
- ✅ `e2e_memory_panel.py` ALL PASS (regression check)

## Follow-ups

- None blocking. Could add a session grouping/folding UI in scope=all
  mode for a nicer cross-session read, but the flat list with per-row
  session tags is enough for the Phase 1 memory-management threat model.
- The scope tab doesn't yet plumb into E2E via the CDP UI driver
  (`scripts/e2e/drive_via_cdp.py`). If/when Phase 2 adds a
  `step_memory_ui_tab_switch` browser-level test, the `data-testid`s
  above are already in place.

## Spec / plan

- Roadmap entry: `docs/superpowers/plans/2026-04-14-phase2-v6-roadmap.md`
  §3.1 slice P2-0-S3
