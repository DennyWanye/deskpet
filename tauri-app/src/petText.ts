// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * petText — companion pet-window display filter.
 *
 * The pet character window must show ONLY clean natural-language text.
 * It must NOT show the user-unintelligible "trace" lines that App.tsx
 * pushes as role:"assistant" for the message stream:
 *
 *   - `🔧 调用 <tool>(...)`      tool-call command (App.tsx tool_use_event/request)
 *   - `✅ <tool> 结果` / `❌ ...`  tool result        (App.tsx tool_use_event/result)
 *   - `⚠ <msg>`                  error               (App.tsx chat_v2_error)
 *   - `<think>...</think>` blocks deepseek reasoning leak (inside chat_v2_final)
 *
 * ALL of the above MUST still be visible in the "信息回顾" history panel
 * (ChatHistoryPanel) — so this filter is applied ONLY at the pet-bubble /
 * pet-stream derivation, never to the history feed. Pure & presentational;
 * no backend change.
 *
 * The trace prefixes are exact string literals this codebase emits
 * itself (App.tsx tool_use_event / chat_v2_error handlers), not fragile
 * heuristics — keep them in sync if those emit sites change.
 */
import { splitThinkBlocks } from "./code-panel/MessageBubble";

/** A line this app emitted as a non-conversational trace, not pet-visible. */
export function isTraceLine(text: string): boolean {
  if (!text) return false;
  const t = text.trimStart();
  if (t.startsWith("🔧 调用 ")) return true; // tool-call command
  if (t.startsWith("⚠ ")) return true; // error banner
  // tool-result line: "✅ <tool> 结果" / "❌ <tool> 结果"
  if (/^[✅❌]\s.+\s结果\s*$/.test(t)) return true;
  return false;
}

/** Strip `<think>...</think>` (closed or still-streaming) → clean text. */
export function stripThink(text: string): string {
  if (!text) return "";
  return splitThinkBlocks(text)
    .filter((s) => s.kind === "normal")
    .map((s) => s.text)
    .join("")
    .trim();
}

/**
 * Return the pet-visible form of an assistant message, or null when the
 * message is a trace line or has no natural-language content (e.g. a
 * think-only chunk). null → caller should skip it for pet display.
 * History/review keeps the raw text regardless.
 */
export function forPet(text: string | null | undefined): string | null {
  if (!text) return null;
  if (isTraceLine(text)) return null;
  const clean = stripThink(text);
  return clean.length > 0 ? clean : null;
}
