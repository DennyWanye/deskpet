// SPDX-License-Identifier: BUSL-1.1

import { useState, type CSSProperties } from "react";
import ReactMarkdown from "react-markdown";

import type { PPTOutlineDecision, PPTOutlineHistoryItem } from "../types/skillPlatform";
import { useSessionsStore } from "../stores/sessionsStore";
import { codePanelWS } from "./ws";

export interface PPTOutlineCardProps {
  outlineId: string;
  topic: string;
  outlineMd: string;
  sourcesCount: number;
  noResearch: boolean;
  history: PPTOutlineHistoryItem[];
  awaiting?: boolean;
  sessionId?: string;
}

export function PPTOutlineCard({
  outlineId,
  topic,
  outlineMd,
  sourcesCount,
  noResearch,
  history,
  awaiting = false,
  sessionId,
}: PPTOutlineCardProps) {
  const [editing, setEditing] = useState(false);
  const [feedback, setFeedback] = useState("");
  const [historyOpen, setHistoryOpen] = useState(false);
  const [decided, setDecided] = useState(!awaiting);
  const resolve_ppt_outline = useSessionsStore((s) => s.resolve_ppt_outline);

  const effectiveAwaiting = awaiting && !decided;
  const canSubmitFeedback = feedback.trim().length > 0;

  const sendDecision = (payload: PPTOutlineDecision["payload"]) => {
    codePanelWS.send({
      type: "ppt_outline_decision",
      payload,
    });
    setDecided(true);
    if (sessionId) {
      resolve_ppt_outline(sessionId, outlineId);
    }
  };

  const decide = (action: PPTOutlineDecision["payload"]["action"]) => {
    sendDecision({ outline_id: outlineId, action });
  };

  const submitModify = () => {
    const trimmed = feedback.trim();
    if (!trimmed) return;
    sendDecision({
      outline_id: outlineId,
      action: "modify",
      feedback: trimmed,
    });
  };

  const reuse = (reuseId: string) => {
    sendDecision({
      outline_id: outlineId,
      action: "reuse",
      reuse_id: reuseId,
    });
  };

  return (
    <div data-testid="ppt-outline-card" style={cardStyle}>
      <div style={titleStyle}>PPT 大纲确认 · {topic || "未命名主题"}</div>
      <div style={metaStyle}>📚 {sourcesCount} 个调研来源</div>
      {noResearch && (
        <div style={warningStyle}>⚠️ 本次未取得调研来源，基于通用知识</div>
      )}

      <div style={outlineStyle}>
        <ReactMarkdown>{outlineMd || "（暂无大纲内容）"}</ReactMarkdown>
      </div>

      {history.length > 0 && (
        <div style={historyWrapStyle}>
          <button
            type="button"
            onClick={() => setHistoryOpen((v) => !v)}
            style={secondaryButtonStyle}
          >
            📜 历史大纲
          </button>
          {historyOpen && (
            <div data-testid="ppt-outline-history" style={historyListStyle}>
              {history.map((item) => (
                <button
                  key={item.outline_id}
                  type="button"
                  disabled={!effectiveAwaiting}
                  onClick={() => reuse(item.outline_id)}
                  style={historyItemStyle}
                >
                  <span style={{ fontWeight: 600 }}>{item.topic || "未命名大纲"}</span>
                  <span style={{ color: "#94a3b8", fontSize: 11 }}>
                    {formatTime(item.created_at)} · {item.sources_count} 个来源
                  </span>
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      {effectiveAwaiting ? (
        <>
          {editing && (
            <div style={editWrapStyle}>
              <label htmlFor={`ppt-outline-feedback-${outlineId}`} style={labelStyle}>
                说说想改哪里
              </label>
              <textarea
                id={`ppt-outline-feedback-${outlineId}`}
                aria-label="说说想改哪里"
                value={feedback}
                onChange={(event) => setFeedback(event.currentTarget.value)}
                style={textareaStyle}
                rows={4}
              />
              <button
                type="button"
                disabled={!canSubmitFeedback}
                onClick={submitModify}
                style={{
                  ...primaryButtonStyle,
                  opacity: canSubmitFeedback ? 1 : 0.5,
                  cursor: canSubmitFeedback ? "pointer" : "not-allowed",
                }}
              >
                提交修改
              </button>
            </div>
          )}
          <div style={buttonBarStyle}>
            <button type="button" onClick={() => decide("accept")} style={primaryButtonStyle}>
              ✅ 确认生成
            </button>
            <button type="button" onClick={() => setEditing(true)} style={secondaryButtonStyle}>
              ✏️ 修改
            </button>
            <button type="button" onClick={() => decide("cancel")} style={dangerButtonStyle}>
              ✖ 取消
            </button>
          </div>
        </>
      ) : (
        <div data-testid="ppt-outline-resolved" style={resolvedStyle}>
          已提交决定
        </div>
      )}
    </div>
  );
}

function formatTime(value: string) {
  if (!value) return "未知时间";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

const cardStyle: CSSProperties = {
  margin: "8px 0",
  padding: "12px 14px",
  background: "rgba(15, 118, 110, 0.14)",
  color: "#dffcf6",
  border: "1px solid rgba(45, 212, 191, 0.45)",
  borderRadius: 8,
  fontSize: 12.5,
};

const titleStyle: CSSProperties = {
  fontWeight: 700,
  color: "#5eead4",
  marginBottom: 6,
};

const metaStyle: CSSProperties = {
  color: "#bae6fd",
  marginBottom: 6,
};

const warningStyle: CSSProperties = {
  color: "#fde68a",
  marginBottom: 8,
};

const outlineStyle: CSSProperties = {
  maxHeight: "40vh",
  overflow: "auto",
  whiteSpace: "normal",
  background: "rgba(2, 6, 23, 0.35)",
  border: "1px solid rgba(148, 163, 184, 0.24)",
  borderRadius: 6,
  padding: "8px 10px",
  lineHeight: 1.55,
};

const buttonBarStyle: CSSProperties = {
  display: "flex",
  flexWrap: "wrap",
  gap: 8,
  marginTop: 10,
};

const primaryButtonStyle: CSSProperties = {
  background: "#0d9488",
  color: "#ffffff",
  border: "none",
  borderRadius: 6,
  padding: "6px 12px",
  fontSize: 12.5,
  fontWeight: 700,
  cursor: "pointer",
};

const secondaryButtonStyle: CSSProperties = {
  background: "rgba(148, 163, 184, 0.18)",
  color: "#e2e8f0",
  border: "1px solid rgba(148, 163, 184, 0.35)",
  borderRadius: 6,
  padding: "6px 12px",
  fontSize: 12.5,
  cursor: "pointer",
};

const dangerButtonStyle: CSSProperties = {
  ...secondaryButtonStyle,
  color: "#fecaca",
  border: "1px solid rgba(248, 113, 113, 0.42)",
};

const editWrapStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 6,
  marginTop: 10,
};

const labelStyle: CSSProperties = {
  color: "#cbd5e1",
  fontSize: 12,
  fontWeight: 600,
};

const textareaStyle: CSSProperties = {
  width: "100%",
  boxSizing: "border-box",
  resize: "vertical",
  minHeight: 86,
  borderRadius: 6,
  border: "1px solid rgba(148, 163, 184, 0.35)",
  background: "rgba(15, 23, 42, 0.86)",
  color: "#e5e7eb",
  padding: "8px 10px",
  fontSize: 12.5,
  lineHeight: 1.5,
};

const historyWrapStyle: CSSProperties = {
  marginTop: 10,
};

const historyListStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 6,
  marginTop: 8,
};

const historyItemStyle: CSSProperties = {
  ...secondaryButtonStyle,
  display: "flex",
  flexDirection: "column",
  alignItems: "flex-start",
  gap: 3,
  textAlign: "left",
  width: "100%",
};

const resolvedStyle: CSSProperties = {
  marginTop: 10,
  color: "#94a3b8",
  fontSize: 12,
  fontWeight: 600,
};

export default PPTOutlineCard;
