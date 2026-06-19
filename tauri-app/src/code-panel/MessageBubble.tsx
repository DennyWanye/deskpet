// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * P4-S23 — message bubble + tool-call card renderer.
 *
 * Three flavours:
 *   1. user / assistant text  → markdown via react-markdown
 *   2. tool_call               → collapsible card (tool name, args)
 *   3. tool_result             → expandable result, syntax-highlighted
 *   4. error                   → red banner
 */
import { useMemo, useState, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import { invoke } from "@tauri-apps/api/core";

const LOCAL_FILE_EXTENSIONS =
  /\.(pptx?|pptm|xlsx?|docx?|pdf|png|jpe?g|gif|md|txt|csv)$/i;

const isLocalFileLink = (href?: string) => {
  if (!href) return false;
  if (/^https?:\/\//i.test(href)) return false;

  const normalizedHref = href.replace(/\\/g, "/");
  const hrefWithoutQuery = normalizedHref.split(/[?#]/)[0];

  return (
    /^file:\/\//i.test(href) ||
    /^[a-zA-Z]:[\\/]/.test(href) ||
    LOCAL_FILE_EXTENSIONS.test(hrefWithoutQuery)
  );
};

const localFilePathFromHref = (href: string) => {
  const withoutScheme = href.replace(/^file:\/\//i, "");
  const withoutLeadingWindowsSlash = withoutScheme.replace(
    /^\/([a-zA-Z]:[\\/])/,
    "$1",
  );

  try {
    return decodeURIComponent(withoutLeadingWindowsSlash);
  } catch {
    return withoutLeadingWindowsSlash;
  }
};

const openLocalFileLink = (href: string) => {
  invoke("artifact_open", { path: localFilePathFromHref(href) }).catch(
    (error) => {
      console.warn("Failed to open local markdown link", error);
    },
  );
};

declare global {
  interface Window {
    __deskpetLocalMarkdownLinkHandlerInstalled?: boolean;
  }
}

if (typeof window !== "undefined" && !window.__deskpetLocalMarkdownLinkHandlerInstalled) {
  window.__deskpetLocalMarkdownLinkHandlerInstalled = true;

  document.addEventListener("click", (event) => {
    if (!(event.target instanceof Element)) return;

    const anchor = event.target.closest<HTMLAnchorElement>("a[href]");
    const href = anchor?.getAttribute("href");
    if (!href || !isLocalFileLink(href)) return;

    event.preventDefault();
    openLocalFileLink(href);
  });

  document.addEventListener("mouseover", (event) => {
    if (!(event.target instanceof Element)) return;

    const anchor = event.target.closest<HTMLAnchorElement>("a[href]");
    const href = anchor?.getAttribute("href");
    if (!anchor || !href || !isLocalFileLink(href) || anchor.title) return;

    anchor.title = "用系统默认程序打开";
  });
}


import type { Message } from "../stores/sessionsStore";
import { useSessionsStore } from "../stores/sessionsStore";
import { CodeBlock, InlineCode } from "./CodeBlock";
import { ArtifactCard, extractArtifactsFromResult } from "./ArtifactCard";
import { codePanelWS } from "./ws";

interface Props {
  msg: Message;
}

// 判断 markdown 链接 href 是否指向本地文件（而非 http(s)/mailto 等网络链接）。
// 命中：Windows 盘符路径 (C:\... / C:/...)、UNC (\\server\...)、file:// 协议、POSIX 绝对路径 (/...)。
function isLocalFilePath(href: string): boolean {
  if (!href) return false;
  const h = href.trim();
  if (/^[a-zA-Z]:[\\/]/.test(h)) return true; // C:\... or C:/...
  if (h.startsWith("\\\\")) return true; // UNC \\server\share
  if (/^file:\/\//i.test(h)) return true; // file:// 协议
  if (h.startsWith("/")) return true; // POSIX 绝对路径
  return false;
}

// 把 href 规整成 artifact_open 可用的本地路径（剥掉 file:// 前缀）。
function toLocalPath(href: string): string {
  const h = href.trim();
  if (/^file:\/\//i.test(h)) {
    try {
      // file:///C:/x.pptx → C:/x.pptx ; file://server/share → //server/share
      return decodeURIComponent(h.replace(/^file:\/\//i, "").replace(/^\/([a-zA-Z]:)/, "$1"));
    } catch {
      return h.replace(/^file:\/\//i, "");
    }
  }
  return h;
}

export function MessageBubble({ msg }: Props) {
  switch (msg.role) {
    case "user":
      return <UserBubble text={msg.text ?? ""} />;
    case "assistant":
      // P5-S2: render with <think>...</think> stripped → collapsed
      // reasoning bubble + the rest as normal markdown. Some thinking-
      // mode models (deepseek-v4-pro) leak chain-of-thought into the
      // visible content stream as `<think>...</think>` instead of the
      // reasoning_content field. Without this split, the user sees the
      // raw think tag and (worse) a hanging streaming cursor when the
      // tag is unclosed.
      return <AssistantBubbleWithThink text={msg.text ?? ""} streaming={false} />;
    case "assistant_delta":
      // P4-S25 A1: streaming partial bubble. Same render as final but
      // with a soft pulsing cursor — UNLESS the only content so far is
      // a still-open <think>, in which case we let AssistantBubbleWithThink
      // keep the reasoning bubble streaming (no main-bubble cursor).
      return <AssistantBubbleWithThink text={msg.text ?? ""} streaming={true} />;
    case "reasoning_delta":
      // P4-S25 A1: thinking-mode chain-of-thought, rendered faded so
      // it's clear it's not the final answer.
      return <ReasoningBubble text={msg.text ?? ""} />;
    case "plan":
      return (
        <PlanCard
          rationale={msg.plan_rationale ?? ""}
          steps={msg.plan_steps ?? []}
          awaiting={!!msg.plan_awaiting_confirm}
          msgId={msg.id}
          planSid={msg.plan_sid}
        />
      );
    case "skill_candidate":
      return (
        <SkillCandidateCard
          candidateId={msg.skill_candidate_id ?? 0}
          name={msg.skill_candidate_name ?? "(未命名技能)"}
          description={msg.skill_candidate_description ?? ""}
          steps={msg.skill_candidate_steps ?? []}
          awaiting={!!msg.skill_candidate_awaiting}
          accepted={msg.skill_candidate_accepted}
          skillSid={msg.skill_candidate_sid}
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
    case "slash_result":
      // FEAT-A2: /help /goal /prefs /skill 结果（ws.ts 已格式化成纯文本）。
      return <SlashResultBubble text={msg.text ?? ""} />;
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

// P5-S2: split a content string on `<think>...</think>` boundaries.
// Handles both well-formed (closed) and unclosed (streaming or
// premature-end_turn) think tags. Returns a flat list of segments
// in original order; consumer renders normal segments through markdown
// and think segments as collapsed reasoning bubbles.
//
// Examples:
//   "hi <think>foo</think> bye"
//     → [{kind:"normal","hi "},{kind:"think","foo",closed:true},{kind:"normal"," bye"}]
//   "<think>still going..."
//     → [{kind:"think","still going...",closed:false}]
//   "plain text"
//     → [{kind:"normal","plain text"}]
type ThinkSegment =
  | { kind: "normal"; text: string }
  | { kind: "think"; text: string; closed: boolean };

export function splitThinkBlocks(text: string): ThinkSegment[] {
  if (!text) return [];
  const out: ThinkSegment[] = [];
  let i = 0;
  while (i < text.length) {
    const open = text.indexOf("<think>", i);
    if (open === -1) {
      const tail = text.slice(i);
      if (tail) out.push({ kind: "normal", text: tail });
      break;
    }
    if (open > i) {
      out.push({ kind: "normal", text: text.slice(i, open) });
    }
    const close = text.indexOf("</think>", open + 7);
    if (close === -1) {
      // Unclosed — everything after <think> is a still-streaming reasoning block.
      out.push({ kind: "think", text: text.slice(open + 7), closed: false });
      break;
    }
    out.push({ kind: "think", text: text.slice(open + 7, close), closed: true });
    i = close + "</think>".length;
  }
  return out;
}

function AssistantBubbleWithThink({
  text,
  streaming,
}: {
  text: string;
  streaming: boolean;
}) {
  const segments = splitThinkBlocks(text);
  const hasNormal = segments.some((s) => s.kind === "normal" && s.text.trim());
  const hasOpenThink = segments.some(
    (s) => s.kind === "think" && !s.closed,
  );
  // Cursor decision: only show in the main bubble if we have visible
  // content actively streaming. If the only thing streaming right now
  // is an open <think> tag, the cursor lives in that reasoning bubble
  // — not the main one. Prevents the "blank bubble + ghost cursor"
  // ambiguity that made earlier turns look stuck.
  const showMainCursor = streaming && hasNormal && !hasOpenThink;

  return (
    <>
      {segments.map((seg, idx) => {
        if (seg.kind === "think") {
          // Open think during streaming → animate; closed/finalized → static.
          const suffix = !seg.closed && streaming ? " ▍" : "";
          return (
            <ReasoningBubble key={idx} text={seg.text + suffix} />
          );
        }
        return (
          <AssistantBubble
            key={idx}
            text={seg.text + (showMainCursor && idx === segments.length - 1 ? " ▍" : "")}
          />
        );
      })}
    </>
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
            a: ({ href, children }: any) => {
              const url = typeof href === "string" ? href : "";
              if (isLocalFilePath(url)) {
                // 本地文件链接（如 LLM 写的 [打开 PPT](C:\...\xxx.pptx)）：
                // webview 里 href 打不开 → 改走 Tauri artifact_open 用系统默认应用打开。
                const localPath = toLocalPath(url);
                return (
                  <a
                    href={url}
                    onClick={(e) => {
                      e.preventDefault();
                      void invoke("artifact_open", { path: localPath }).catch(
                        (err) => console.error("[artifact_open] failed", err),
                      );
                    }}
                    style={{ color: "#67e8f9", cursor: "pointer" }}
                    title={localPath}
                  >
                    {children}
                  </a>
                );
              }
              return (
                <a href={url} target="_blank" rel="noreferrer noopener"
                   style={{ color: "#67e8f9" }}>{children}</a>
              );
            },
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

// P5-S2 Phase 5.1: 把后端 os_tools 错误返回值（统一 schema）拆成
//   { body, hint, examples }
// 让 ToolResultCard 可以对 hint 做高亮渲染。后端 schema:
//   { ok: false, error: "...", hint: "...", examples: [...] }
// 没有 hint 字段或不是 JSON 时退化为 { body: 原文, hint: null, examples: null }。
//
// 这里挂在 export 是为了 splitToolError.test.ts 能直接 import 测纯函数。
export interface SplitToolErrorResult {
  body: string;
  hint: string | null;
  examples: unknown[] | null;
}

export function splitToolError(raw: string): SplitToolErrorResult {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return { body: raw, hint: null, examples: null };
  }
  if (!parsed || typeof parsed !== "object") {
    return { body: JSON.stringify(parsed, null, 2), hint: null, examples: null };
  }
  const obj = parsed as Record<string, unknown>;
  // hint 必须是非空 string；空白也算空
  let hint: string | null = null;
  if (typeof obj.hint === "string" && obj.hint.trim().length > 0) {
    hint = obj.hint;
  }
  let examples: unknown[] | null = null;
  if (Array.isArray(obj.examples)) {
    examples = obj.examples;
  }
  // last-mile envelope 包装后，工具自身的 {ok:false, hint, examples} 被套进
  // envelope.result（JSON 字符串）→ 顶层取不到 hint，金黄修复建议卡不触发。
  // 兜底：当顶层无 hint 时，解包 envelope.result 再取 hint/examples（与
  // ArtifactCard.extractArtifactsFromResult 的嵌套兜底同一模式）。
  if (hint === null && typeof obj.result === "string") {
    try {
      const inner = JSON.parse(obj.result) as Record<string, unknown>;
      if (inner && typeof inner === "object") {
        if (typeof inner.hint === "string" && inner.hint.trim().length > 0) {
          hint = inner.hint;
        }
        if (examples === null && Array.isArray(inner.examples)) {
          examples = inner.examples;
        }
      }
    } catch {
      /* envelope.result 不是 JSON → 忽略，保持 hint=null */
    }
  }
  return {
    body: JSON.stringify(parsed, null, 2),
    hint,
    examples,
  };
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
  // WI-T1.4 last-mile: 优先走 ArtifactCard 分发（PRD §3 D2）。
  // 仅在 ok=true 且 result 含 artifacts[] 时启用 — 失败仍走旧路径
  // 显示错误细节。`artifacts` 字段不在 → 字节级回落旧渲染（TG-5 T5-5）。
  const artifacts = useMemo(
    () => (ok ? extractArtifactsFromResult(result) : []),
    [ok, result],
  );
  if (ok && artifacts.length > 0) {
    return (
      <ToolCard
        header={
          <>
            <span style={{ color: "#86efac" }}>✓ ok</span>
            <span style={{ marginLeft: 6, color: "#94a3b8" }}>← {name}</span>
            <span style={{ marginLeft: 8, color: "#64748b", fontSize: 11 }}>
              {artifacts.length} 个产物
            </span>
          </>
        }
        open={true}
        onToggle={() => { /* artifact 卡片本身可折叠 */ }}
      >
        <div data-testid="artifact-card-list" style={{ padding: "4px 8px" }}>
          {artifacts.map((a, i) => (
            <ArtifactCard key={i} artifact={a} toolName={name} />
          ))}
        </div>
      </ToolCard>
    );
  }
  // P5-S2 Phase 5.1: 走 splitToolError 提取 hint；保留原 pretty-print fallback。
  const { body: display, hint } = splitToolError(result);
  const lineCount = display.split("\n").length;
  const [open, setOpen] = useState(lineCount <= 30);
  const status = ok ? "✓ ok" : "✗ failed";
  const statusColor = ok ? "#86efac" : "#fca5a5";
  // 有 hint = 后端给了"修这个错误"的可读建议，整张卡加金黄色描边吸引注意。
  const hasHint = !!hint;
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
          {hasHint && (
            <span style={{ marginLeft: 8, color: "#f59e0b", fontSize: 11 }}>
              💡 有修复建议
            </span>
          )}
        </>
      }
      open={open}
      onToggle={() => setOpen((v) => !v)}
      borderColor={hasHint ? "#f59e0b" : undefined}
    >
      {hasHint && (
        <div
          data-bp-selectable=""
          data-testid="tool-result-hint"
          style={{
            padding: "6px 12px",
            background: "rgba(245, 158, 11, 0.12)",
            color: "#fcd34d",
            fontSize: 12,
            borderTop: "1px solid #f59e0b",
            borderLeft: "1px solid #f59e0b",
            borderRight: "1px solid #f59e0b",
            lineHeight: 1.5,
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
          }}
        >
          💡 {hint}
        </div>
      )}
      <pre data-bp-selectable="" style={preStyle}>
        {display}
      </pre>
    </ToolCard>
  );
}

function PlanCard({
  rationale,
  steps,
  awaiting = false,
  msgId,
  planSid,
}: {
  rationale: string;
  steps: { title: string; detail: string }[];
  // superpowers 决策2 plan-confirm 硬门
  awaiting?: boolean;
  msgId?: string;
  planSid?: string;
}) {
  const resolve_plan = useSessionsStore((s) => s.resolve_plan);
  const decide = (decision: "go" | "cancel") => {
    if (planSid) {
      codePanelWS.send({
        type: "plan_confirm",
        payload: { session_id: planSid, decision },
      });
      resolve_plan(planSid, msgId);
    }
  };
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
      {awaiting && (
        <div
          data-testid="plan-confirm-bar"
          style={{ display: "flex", gap: 8, marginTop: 10 }}
        >
          <button
            type="button"
            data-testid="plan-confirm-go"
            onClick={() => decide("go")}
            style={{
              flex: 1,
              background: "#2563eb",
              color: "#fff",
              border: "none",
              borderRadius: 6,
              padding: "6px 12px",
              fontSize: 12.5,
              fontWeight: 600,
              cursor: "pointer",
            }}
          >
            ▶ 执行
          </button>
          <button
            type="button"
            data-testid="plan-confirm-cancel"
            onClick={() => decide("cancel")}
            style={{
              background: "rgba(148, 163, 184, 0.2)",
              color: "#e2e8f0",
              border: "1px solid rgba(148, 163, 184, 0.3)",
              borderRadius: 6,
              padding: "6px 14px",
              fontSize: 12.5,
              cursor: "pointer",
            }}
          >
            取消
          </button>
        </div>
      )}
    </div>
  );
}

// FP-5 WI-4.3c — 技能自创确认卡。
// 同构镜像 PlanCard（上方）的「后端 propose → 卡片 awaiting → 用户点按钮 →
// WS 回传 → resolve 清 awaiting」闭环：
//   • PlanCard 收 chat_v2_plan，回传 plan_confirm{session_id,decision}
//   • SkillCandidateCard 收 skill_candidate_proposed，回传
//     skill_candidate_confirm{candidate_id,accept}
// 后端收到 accept=true → 落盘 SKILL.md + reload；accept=false → 丢弃候选。
function SkillCandidateCard({
  candidateId,
  name,
  description,
  steps,
  awaiting = false,
  accepted,
  skillSid,
}: {
  candidateId: number;
  name: string;
  description: string;
  steps: string[];
  awaiting?: boolean;
  // resolve 后的最终决定（true=已保存, false=已忽略, undefined=尚未决定）
  accepted?: boolean;
  skillSid?: string;
}) {
  const resolve_skill_candidate = useSessionsStore(
    (s) => s.resolve_skill_candidate,
  );
  const decide = (accept: boolean) => {
    // 镜像 PlanCard.decide：先 WS 回传，再本地 resolve 清 awaiting（防重复点击）。
    codePanelWS.send({
      type: "skill_candidate_confirm",
      payload: { candidate_id: candidateId, accept },
    });
    if (skillSid) {
      resolve_skill_candidate(skillSid, candidateId, accept);
    }
  };
  return (
    <div
      data-bp-selectable=""
      data-testid="skill-candidate-card"
      style={{
        margin: "8px 0",
        padding: "10px 14px",
        // 用紫/青绿调与 plan 卡（蓝）区分，但沿用同样的半透明描边 token。
        background: "rgba(16, 185, 129, 0.10)",
        color: "#d1fae5",
        border: "1px solid rgba(16, 185, 129, 0.45)",
        borderRadius: 8,
        fontSize: 12.5,
      }}
    >
      <div style={{ fontWeight: 600, marginBottom: 6, color: "#6ee7b7" }}>
        ✨ 新技能 · {name}
      </div>
      {description && (
        <div
          style={{
            fontSize: 11.5,
            color: "#94a3b8",
            marginBottom: 8,
            fontStyle: "italic",
          }}
        >
          {description}
        </div>
      )}
      {steps.length > 0 && (
        <ol style={{ margin: 0, paddingLeft: 22, lineHeight: 1.55 }}>
          {steps.map((s, i) => (
            <li key={i} style={{ marginBottom: 3, color: "#e2e8f0" }}>
              {s}
            </li>
          ))}
        </ol>
      )}
      {awaiting ? (
        <div
          data-testid="skill-candidate-bar"
          style={{ display: "flex", gap: 8, marginTop: 10 }}
        >
          <button
            type="button"
            data-testid="skill-candidate-accept"
            onClick={() => decide(true)}
            style={{
              flex: 1,
              background: "#10b981",
              color: "#fff",
              border: "none",
              borderRadius: 6,
              padding: "6px 12px",
              fontSize: 12.5,
              fontWeight: 600,
              cursor: "pointer",
            }}
          >
            ✓ 保存技能
          </button>
          <button
            type="button"
            data-testid="skill-candidate-ignore"
            onClick={() => decide(false)}
            style={{
              background: "rgba(148, 163, 184, 0.2)",
              color: "#e2e8f0",
              border: "1px solid rgba(148, 163, 184, 0.3)",
              borderRadius: 6,
              padding: "6px 14px",
              fontSize: 12.5,
              cursor: "pointer",
            }}
          >
            忽略
          </button>
        </div>
      ) : (
        // 已决定：按钮消失，显示结果文案（防重复点击 + 给用户反馈）。
        <div
          data-testid="skill-candidate-result"
          style={{
            marginTop: 10,
            fontSize: 12,
            fontWeight: 600,
            color: accepted ? "#6ee7b7" : "#94a3b8",
          }}
        >
          {accepted ? "✓ 已保存为技能" : "已忽略"}
        </div>
      )}
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

function SlashResultBubble({ text }: { text: string }) {
  return (
    <div style={{ display: "flex", margin: "8px 0" }}>
      <div
        data-bp-selectable=""
        data-testid="slash-result-bubble"
        style={{
          maxWidth: "92%",
          background: "rgba(15, 23, 42, 0.85)",
          color: "#cbd5e1",
          padding: "8px 12px",
          borderRadius: "12px 12px 12px 2px",
          fontSize: 12.5,
          lineHeight: 1.55,
          border: "1px solid rgba(99, 102, 241, 0.35)",
          whiteSpace: "pre-wrap",
          wordBreak: "break-word",
          fontFamily:
            'ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace',
        }}
      >
        {text}
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
  borderColor,
}: {
  header: ReactNode;
  open: boolean;
  onToggle: () => void;
  children: ReactNode;
  /** P5-S2 Phase 5.1: optional emphasis border (e.g. golden when hint present). */
  borderColor?: string;
}) {
  const accent = borderColor ?? "rgba(148, 163, 184, 0.22)";
  return (
    <div
      style={{
        margin: "8px 0",
        // 整张卡加同色 outline，让 hint 高亮在折叠/展开两种状态下都能被看见
        ...(borderColor ? { outline: `1px solid ${borderColor}`, outlineOffset: 0, borderRadius: 6 } : {}),
      }}
    >
      <button
        type="button"
        onClick={onToggle}
        style={{
          width: "100%",
          textAlign: "left",
          padding: "5px 10px",
          background: "rgba(30, 41, 59, 0.6)",
          border: `1px solid ${accent}`,
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
