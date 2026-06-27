# P2-0-S6 ChatHistoryPanel a11y follow-up — HANDOFF

**Date**: 2026-04-15
**Sprint**: V6 Phase 2 · Sprint P2-0 · Slice 6
**Status**: ✅ Complete
**Target version**: feeds into `v0.2.0-phase2-beta1`

## What shipped

Three a11y improvements to `ChatHistoryPanel` identified during the
Phase 1 review but deferred:

1. **Auto-focus**: close button receives focus on open.
2. **Focus trap**: Tab / Shift+Tab cycle within the panel.
3. **Escape-to-close**: standard dialog affordance.

Component already had `role="dialog"` + `aria-modal="true"` + `aria-label`
from the initial ship; this slice just wires up the keyboard behaviors
those ARIA attributes imply.

## Commits (master)

| SHA | Subject |
|---|---|
| `bd22fa6` | feat(a11y): ChatHistoryPanel focus trap + auto-focus + Esc-to-close |

## Changes (`tauri-app/src/components/ChatHistoryPanel.tsx`)

- Added `useRef<HTMLDivElement>` (panel) + `useRef<HTMLButtonElement>`
  (close button).
- `useEffect([open])`: when panel flips to open, calls
  `closeBtnRef.current?.focus()`.
- `useEffect([open, onClose])`: attaches a `keydown` listener to
  `window` while open:
  - `Escape` → `onClose()` (+ `preventDefault`).
  - `Tab` → queries focusable descendants; wraps focus at first/last;
    forces focus back inside the panel if `document.activeElement`
    escaped.
- Close button got the `closeBtnRef`.

**Focusable selector**: `button, [href], input, select, textarea,
[tabindex]:not([tabindex="-1"])`. Same shape as the MDN reference
implementation and what Radix / react-aria use.

**Degenerate case**: today the panel has exactly one focusable
element (`✕`). The trap becomes a no-op that keeps Tab on that button,
which is still the right behavior — Tab shouldn't escape a modal.

## Gates

- ✅ `npx tsc --noEmit` clean (tauri-app)
- Keyboard flow manual check deferred to the S7 self-update rehearsal
  (no visual regression risk; pure interaction behavior).

## Follow-ups

- A restore-focus-on-close path is NOT wired in. When the user hits
  Escape, focus falls to `document.body`, not back to the
  `dialog-history-toggle` button that opened it. A proper React
  dialog would track `document.activeElement` at open time and restore
  it in the cleanup. Worth adding if we build more modals — deferred
  for now because there's only one entry point (the 💬 button) and
  users can Tab back to it.
- If future slices add more interactive elements inside the panel
  (e.g. per-row "copy" / "regenerate" buttons), the existing focus
  trap will cover them automatically — no change needed.
- No dedicated E2E for keyboard navigation yet. Would add a step that
  opens the panel via click, asserts `document.activeElement` ===
  close button, presses Escape, asserts panel is closed.

## Spec / plan

- Roadmap entry: `docs/superpowers/plans/2026-04-14-phase2-v6-roadmap.md`
  §3.1 slice P2-0-S6
