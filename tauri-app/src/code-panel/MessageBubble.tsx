/**
 * P4-S23 — message bubble + tool-call card renderer.
 *
 * Three flavours:
 *   1. user / assistant text  → markdown via react-markdown
 *   2. tool_call               → collapsible card (tool name, args)
 *   3. tool_result             → expandable result, syntax-highlighted
 *   4. error                   → red banner
 */
import { useState, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";

import type { Message } from "../stores/sessionsStore";
import { CodeBlock, InlineCode } from "./CodeBlock";

interface Props {
  msg: Message;
}

export function MessageBubble({ msg }: Props) {
  switch (msg.role) {
    case "user":
      return <UserBubble text={msg.text ?? ""} />;
    case "assistant":
      return <AssistantBubble text={msg.text ?? ""} />;
    case "assistant_delta":
      // P4-S25 A1: streaming partial bubble. Same chrome as assistant
      // but with a soft pulsing cursor at the end so the user knows
      // text is still flowing in.
      return <AssistantBubble text={(msg.text ?? "") + " ▍"} />;
    case "reasoning_delta":
      // P4-S25 A1: thinking-mode chain-of-thought, rendered faded so
      // it's clear it's not the final answer.
      return <ReasoningBubble text={msg.text ?? ""} />;
    case "plan":
      return (
        <PlanCard
          rationale={msg.plan_rationale ?? ""}
          steps={msg.plan_steps ?? []}
        />
      );
    case "tool_call":
      return (
        <ToolCallCard
          name={msg.tool_name ?? "(unknown)"}
          args={msg.tool_args ?? {}}
        />
      );
    case "tool_result":
      return (
        <ToolResultCard
          name={msg.tool_name ?? "(unknown)"}
          ok={msg.tool_ok ?? true}
          result={msg.tool_result ?? ""}
        />
      );
    case "error":
      return <ErrorBanner text={msg.text ?? "(unknown error)"} />;
    default:
      return null;
  }
}

function UserBubble({ text }: { text: string }) {
  return (
    <div style={{ display: "flex", justifyContent: "flex-end", margin: "8px 0" }}>
      <div
        data-bp-selectable=""
        style={{
          maxWidth: "85%",
          background: "#2563eb",
          color: "#fff",
          padding: "8px 12px",
          borderRadius: "12px 12px 2px 12px",
          fontSize: 13.5,
          lineHeight: 1.55,
          whiteSpace: "pre-wrap",
          wordBreak: "break-word",
        }}
      >
        {text}
      </div>
    </div>
  );
}

function AssistantBubble({ text }: { text: string }) {
  return (
    <div style={{ display: "flex", margin: "8px 0" }}>
      <div
        data-bp-selectable=""
        style={{
          maxWidth: "92%",
          background: "rgba(30, 35, 48, 0.95)",
          color: "#e2e8f0",
          padding: "10px 14px",
          borderRadius: "12px 12px 12px 2px",
          fontSize: 13.5,
          lineHeight: 1.6,
          border: "1px solid rgba(148, 163, 184, 0.18)",
          // Markdown elements need their own spacing
          fontFamily:
            '-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif',
        }}
      >
        <ReactMarkdown
          components={{
            code: ({ inline, className, children }: any) => {
              const match = /language-(\w+)/.exec(className || "");
              if (!inline && match) {
                return (
                  <CodeBlock language={match[1]}>
                    {String(children).replace(/\n$/, "")}
                  </CodeBlock>
                );
              }
              return <InlineCode>{children}</InlineCode>;
            },
            p: ({ children }: any) => (
              <p style={{ margin: "4px 0" }}>{children}</p>
            ),
            ul: ({ children }: any) => (
              <ul style={{ margin: "6px 0", paddingLeft: 22 }}>{children}</ul>
            ),
            ol: ({ children }: any) => (
              <ol style={{ margin: "6px 0", paddingLeft: 22 }}>{children}</ol>
            ),
            a: ({ href, children }: any) => (
              <a href={href} target="_blank" rel="noreferrer noopener"
                 style={{ color: "#67e8f9" }}>{children}</a>
            ),
          }}
        >
          {text}
        </ReactMarkdown>
      </div>
    </div>
  );
}

function ToolCallCard({ name, args }: { name: string; args: Record<string, unknown> }) {
  const [open, setOpen] = useState(false);
  const arg_summary = Object.entries(args)
    .filter(([k]) => !k.startsWith("_"))   // hide injected _project_root etc.
    .map(([k, v]) => {
      const s = typeof v === "string" ? v : JSON.stringify(v);
      const trimmed = s.length > 60 ? s.slice(0, 57) + "…" : s;
      return `${k}=${trimmed}`;
    })
    .join("  ");
  return (
    <ToolCard
      header={
        <>
          <span style={{ color: "#67e8f9" }}>▶ {name}</span>
          {arg_summary && (
            <span style={{ marginLeft: 8, color: "#94a3b8", fontSize: 11 }}>
              {arg_summary}
            </span>
          )}
        </>
      }
      open={open}
      onToggle={() => setOpen((v) => !v)}
    >
      <pre
        data-bp-selectable=""
        style={preStyle}
      >
        {JSON.stringify(args, null, 2)}
      </pre>
    </ToolCard>
  );
}

function ToolResultCard({
  name,
  ok,
  result,
}: {
  name: string;
  ok: boolean;
  result: string;
}) {
  // Try to pretty-print JSON, fall back to raw.
  let display = result;
  try {
    const parsed = JSON.parse(result);
    display = JSON.stringify(parsed, null, 2);
  } catch {
    /* keep raw */
  }
  const lineCount = display.split("\n").length;
  const [open, setOpen] = useState(lineCount <= 30);
  const status = ok ? "✓ ok" : "✗ failed";
  const statusColor = ok ? "#86efac" : "#fca5a5";
  return (
    <ToolCard
      header={
        <>
          <span style={{ color: statusColor }}>{status}</span>
          <span style={{ marginLeft: 6, color: "#94a3b8" }}>← {name}</span>
          {!open && (
            <span style={{ marginLeft: 8, color: "#64748b", fontSize: 11 }}>
              {lineCount} 行 — 点击展开
            </span>
          )}
        </>
      }
      open={open}
      onToggle={() => setOpen((v) => !v)}
    >
      <pre data-bp-selectable="" style={preStyle}>
        {display}
      </pre>
    </ToolCard>
  );
}

function PlanCard({
  rationale,
  steps,
}: {
  rationale: string;
  steps: { title: string; detail: string }[];
}) {
  return (
    <div
      data-bp-selectable=""
      style={{
        margin: "8px 0",
        padding: "10px 14px",
        background: "rgba(37, 99, 235, 0.10)",
        color: "#dbeafe",
        border: "1px solid rgba(37, 99, 235, 0.45)",
        borderRadius: 8,
        fontSize: 12.5,
      }}
    >
      <div style={{ fontWeight: 600, marginBottom: 6, color: "#93c5fd" }}>
        📋 计划 ({steps.length} 步)
      </div>
      {rationale && (
        <div
          style={{
            fontSize: 11.5,
            color: "#94a3b8",
            marginBottom: 8,
            fontStyle: "italic",
          }}
        >
          {rationale}
        </div>
      )}
      <ol style={{ margin: 0, paddingLeft: 22, lineHeight: 1.55 }}>
        {steps.map((s, i) => (
          <li key={i} style={{ marginBottom: 3 }}>
            <strong style={{ color: "#e2e8f0" }}>{s.title}</strong>
            {s.detail && (
              <span style={{ color: "#94a3b8" }}> — {s.detail}</span>
            )}
          </li>
        ))}
      </ol>
    </div>
  );
}

function ReasoningBubble({ text }: { text: string }) {
  return (
    <div style={{ display: "flex", margin: "4px 0" }}>
      <div
        data-bp-selectable=""
        style={{
          maxWidth: "92%",
          background: "rgba(30, 35, 48, 0.55)",
          color: "#94a3b8",
          padding: "6px 12px",
          borderRadius: "12px 12px 12px 2px",
          fontSize: 12,
          lineHeight: 1.5,
          fontStyle: "italic",
          border: "1px dashed rgba(148, 163, 184, 0.25)",
          whiteSpace: "pre-wrap",
          wordBreak: "break-word",
        }}
      >
        💭 {text}
      </div>
    </div>
  );
}

function ErrorBanner({ text }: { text: string }) {
  return (
    <div
      data-bp-selectable=""
      style={{
        margin: "8px 0",
        padding: "8px 12px",
        borderRadius: 6,
        background: "rgba(220, 38, 38, 0.18)",
        color: "#fca5a5",
        border: "1px solid rgba(220, 38, 38, 0.45)",
        fontSize: 12.5,
      }}
    >
      ⚠ {text}
    </div>
  );
}

// ---- shared tool-card chrome ------------------------------------------

function ToolCard({
  header,
  open,
  onToggle,
  children,
}: {
  header: ReactNode;
  open: boolean;
  onToggle: () => void;
  children: ReactNode;
}) {
  return (
    <div style={{ margin: "8px 0" }}>
      <button
        type="button"
        onClick={onToggle}
        style={{
          width: "100%",
          textAlign: "left",
          padding: "5px 10px",
          background: "rgba(30, 41, 59, 0.6)",
          border: "1px solid rgba(148, 163, 184, 0.22)",
          color: "#e2e8f0",
          fontSize: 12,
          fontFamily:
            'ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace',
          borderRadius: open ? "6px 6px 0 0" : 6,
          cursor: "pointer",
        }}
      >
        {header}
      </button>
      {open && children}
    </div>
  );
}

const preStyle: React.CSSProperties = {
  margin: 0,
  padding: "8px 12px",
  background: "#0d1117",
  color: "#c9d1d9",
  fontSize: 11.5,
  fontFamily:
    'ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace',
  border: "1px solid rgba(148, 163, 184, 0.22)",
  borderTop: "none",
  borderRadius: "0 0 6px 6px",
  whiteSpace: "pre-wrap",
  wordBreak: "break-word",
  maxHeight: 320,
  overflowY: "auto",
};
