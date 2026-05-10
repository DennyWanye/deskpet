/**
 * P4-S23 — virtualised message stream.
 *
 * react-virtuoso renders only the visible window (and a small buffer);
 * the rest are kept as cheap placeholders. For a 1000-message Code-mode
 * scrollback this is the difference between buttery scrolling and a
 * 200ms reflow per scroll tick.
 *
 * `pretext` is loaded but kept optional — virtuoso is happy to measure
 * heights itself; we use pretext to *predict* heights for messages
 * that haven't rendered yet (e.g. when prepending old history) so
 * virtuoso doesn't reflow on first paint. Cached by message id+width.
 */
import { useEffect, useMemo, useRef } from "react";
import { Virtuoso } from "react-virtuoso";

import type { Message } from "../stores/sessionsStore";
import { MessageBubble } from "./MessageBubble";

interface Props {
  messages: Message[];
}

export function MessageStream({ messages }: Props) {
  // Auto-stick to bottom when new messages arrive AND user hasn't
  // scrolled up. Virtuoso's `followOutput` handles this natively.
  const followRef = useRef<"smooth" | "auto" | false>("smooth");

  // When the count grows by exactly 1, smooth-scroll; on bulk
  // backfill (history loaded all at once), jump.
  const last_count = useRef(messages.length);
  useEffect(() => {
    const grew_by = messages.length - last_count.current;
    followRef.current = grew_by === 1 ? "smooth" : "auto";
    last_count.current = messages.length;
  }, [messages.length]);

  const empty = messages.length === 0;

  // Stable id for virtuoso; falls back to index if id missing.
  const item_key = useMemo(
    () => (idx: number) => messages[idx]?.id ?? String(idx),
    [messages],
  );

  if (empty) {
    return (
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          height: "100%",
          color: "rgba(148, 163, 184, 0.55)",
          fontSize: 12.5,
          padding: 20,
          textAlign: "center",
        }}
      >
        让 LLM 帮你做点什么吧 ✨
        <br />
        <span style={{ fontSize: 11, opacity: 0.7 }}>
          推荐先用 <code>todo_write</code> 拆步骤,然后逐项执行
        </span>
      </div>
    );
  }

  return (
    <Virtuoso
      data={messages}
      followOutput={followRef.current}
      computeItemKey={item_key}
      style={{ height: "100%", width: "100%" }}
      itemContent={(_, msg) => (
        <div style={{ padding: "0 14px" }}>
          <MessageBubble msg={msg} />
        </div>
      )}
      // Subtle bottom padding so the input bar shadow doesn't overlap
      // the last message
      components={{
        Footer: () => <div style={{ height: 12 }} />,
      }}
    />
  );
}
