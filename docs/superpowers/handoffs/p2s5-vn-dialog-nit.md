# P2-0-S5 VN Dialog Bar NIT 清理 — HANDOFF

**Date**: 2026-04-15
**Sprint**: V6 Phase 2 · Sprint P2-0 · Slice 5
**Status**: ✅ Complete
**Target version**: feeds into `v0.2.0-phase2-beta1`

## What shipped

Three small NITs carried over from the VN-底栏 rearchitecture review:

1. **Dead `messagesEndRef` removed** from `App.tsx`.
2. **Empty-state placeholder** added to `DialogBar.tsx`.
3. **Mid-step `ensure_mic_idle`** added to `step_dialog_bar` in the
   CDP E2E driver.

## Commits (master)

| SHA | Subject |
|---|---|
| `4d68e4f` | chore(vn): P2-0-S5 VN dialog bar NIT cleanup |

## Details

### App.tsx — drop dead ref + no-op effect

Before: a `messagesEndRef` + a hidden `<div ref=...>` + a `useEffect`
that only contained a comment. Left over from the pre-VN architecture
where the chat column auto-scrolled to the ref. Under the new VN 底栏
the ref and the effect do nothing — removed both.

The `useRef` import stays; it's still used by `liveRef`.

### DialogBar.tsx — empty-state placeholder

Before: `latestAssistant === null` rendered as an empty string. Cold
first-run UX looked broken ("为什么底栏没东西？").

After: shows
`按住下方按钮说话，或输入消息开始聊天…` in muted italic (opacity 0.5,
`font-style: italic`). A new `data-empty="true" | "false"` attribute on
the text node gives future E2E a stable hook without having to string-
compare the placeholder text.

### drive_via_cdp.py — mid-step `ensure_mic_idle`

`step_dialog_bar` sends three chat messages in sequence. `ensure_mic_idle`
was only called once at the top, so if anything between sends flipped
the mic to recording (rare but observed during VAD misfires), follow-up
sends queued behind the audio pipeline and blew the 60s timeout.

Now called before each of the three sends.

## Gates

- ✅ `npx tsc --noEmit` clean (tauri-app)
- Visual check: DialogBar with `latestAssistant={null}` renders the
  placeholder in muted italic; with a real string renders full-opacity
  non-italic. (Confirmed via code inspection; live run deferred to
  the S7 self-update rehearsal.)
- E2E `step_dialog_bar` regression not re-run under this branch since
  the changes are additive (extra idle checks before existing sends).

## Follow-ups

- None. This was a cleanup slice.

## Spec / plan

- Roadmap entry: `docs/superpowers/plans/2026-04-14-phase2-v6-roadmap.md`
  §3.1 slice P2-0-S5
- Original VN dialog-bar plan: `docs/superpowers/plans/2026-04-14-vn-dialog-bar.md`
