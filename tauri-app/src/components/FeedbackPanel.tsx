// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * WI-02 (beta-100) — in-app feedback panel.
 *
 * Lets a beta user describe a problem and one-click package a redacted
 * diagnostic zip (crash reports + recent logs + anonymous metrics).
 * The "打包诊断" button stays disabled until the note is ≥ 10 chars so
 * we don't collect contentless reports.
 *
 * Privacy: the bundle is built Rust-side and never contains the API
 * key (see `src-tauri/src/diagnostics.rs`). The panel just shows the
 * resulting path + the feedback channel.
 *
 * Presentation + local state only — `onBuildBundle` is injected so the
 * panel is unit-testable without Tauri.
 */
import { memo, useState, useCallback } from "react";

import { Icon } from "./Icon";

export interface FeedbackBundleResult {
  zip_path: string;
  size_bytes: number;
  collected: Record<string, string>;
}

export interface FeedbackPanelProps {
  /** Build the diagnostic zip for the given note. */
  onBuildBundle: (note: string) => Promise<FeedbackBundleResult>;
  /** Close the panel. */
  onClose: () => void;
  /** Where users should send the zip — shown verbatim. */
  feedbackChannel?: string;
}

/** Minimum note length before packaging is allowed. */
export const MIN_NOTE_CHARS = 10;

/**
 * Pure: is the feedback note long enough to allow packaging?
 * Trimmed length must be ≥ :data:`MIN_NOTE_CHARS`. Exported for
 * unit-testing without a DOM.
 */
export function isFeedbackNoteValid(note: string): boolean {
  return note.trim().length >= MIN_NOTE_CHARS;
}

type BuildState = "idle" | "building" | "done" | "error";

function FeedbackPanelImpl({
  onBuildBundle,
  onClose,
  feedbackChannel = "内测交流群文件 / GitHub Issues",
}: FeedbackPanelProps) {
  const [note, setNote] = useState("");
  const [state, setState] = useState<BuildState>("idle");
  const [result, setResult] = useState<FeedbackBundleResult | null>(null);
  const [error, setError] = useState("");

  const noteOk = isFeedbackNoteValid(note);

  const handleBuild = useCallback(async () => {
    if (!noteOk) return;
    setState("building");
    setError("");
    try {
      const r = await onBuildBundle(note.trim());
      setResult(r);
      setState("done");
    } catch (e) {
      setError(String(e));
      setState("error");
    }
  }, [noteOk, note, onBuildBundle]);

  return (
    <div data-testid="feedback-panel" style={overlayStyle}>
      <div style={cardStyle} role="dialog" aria-label="反馈问题">
        <div style={headerStyle}>
          <span style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <span
              style={{
                display: "inline-flex",
                alignItems: "center",
                justifyContent: "center",
                width: 32,
                height: 32,
                borderRadius: 10,
                background: "linear-gradient(180deg,#fef2f2,#fee2e2)",
                color: "#e11d48",
              }}
            >
              <Icon name="bug" size={18} />
            </span>
            <h2 style={titleStyle}>反馈问题</h2>
          </span>
          <button
            data-testid="feedback-close-btn"
            style={closeBtnStyle}
            onClick={onClose}
            aria-label="关闭"
          >
            <Icon name="close" size={16} />
          </button>
        </div>

        <p style={hintStyle}>
          描述你遇到的问题，DeskPet 会打包诊断信息（崩溃记录 + 最近日志 +
          匿名使用计数）。<strong>诊断包不含你的 API 密钥</strong>。
        </p>

        <textarea
          data-testid="feedback-note"
          style={textareaStyle}
          value={note}
          placeholder="例如：点击设置保存后桌宠没反应，重启也没用…（至少 10 个字）"
          rows={5}
          onChange={(e) => setNote(e.target.value)}
        />
        <div style={counterStyle}>
          {note.trim().length} 字
          {!noteOk && note.length > 0 && (
            <span style={{ color: "#dc2626" }}>　（再写一点，至少 10 字）</span>
          )}
        </div>

        <button
          data-testid="feedback-build-btn"
          style={{
            ...buildBtnStyle,
            opacity: noteOk && state !== "building" ? 1 : 0.5,
            cursor: noteOk && state !== "building" ? "pointer" : "not-allowed",
          }}
          disabled={!noteOk || state === "building"}
          onClick={handleBuild}
        >
          {state === "building" ? "打包中…" : "一键打包诊断"}
        </button>

        {state === "done" && result && (
          <div data-testid="feedback-result" style={resultStyle}>
            <p
              style={{
                margin: "0 0 6px",
                fontWeight: 600,
                color: "#16a34a",
                display: "flex",
                alignItems: "center",
                gap: 6,
              }}
            >
              <Icon name="check" size={15} />
              诊断包已生成（{(result.size_bytes / 1024).toFixed(0)} KB）
            </p>
            <code style={pathStyle}>{result.zip_path}</code>
            <p style={{ margin: "8px 0 0", fontSize: 12.5, color: "#475569" }}>
              文件已在资源管理器中高亮显示。请把它发送到：
              <strong> {feedbackChannel}</strong>
            </p>
          </div>
        )}

        {state === "error" && (
          <p data-testid="feedback-error" style={errStyle}>
            ✗ 打包失败：{error}
            <br />
            你也可以手动到安装目录的 <code>crash_reports/</code> 取文件。
          </p>
        )}
      </div>
    </div>
  );
}

export const FeedbackPanel = memo(FeedbackPanelImpl);

// --------------------------- styles ----------------------------------

const overlayStyle: React.CSSProperties = {
  position: "fixed",
  inset: 0,
  background: "rgba(15, 23, 42, 0.55)",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  zIndex: 9998,
  backdropFilter: "blur(4px)",
};

const cardStyle: React.CSSProperties = {
  width: 440,
  maxWidth: "92vw",
  background: "#ffffff",
  borderRadius: 16,
  padding: "20px 24px 22px",
  boxShadow: "0 24px 60px rgba(0,0,0,0.32)",
  fontFamily: "Microsoft YaHei UI, sans-serif",
  color: "#0f172a",
};

const headerStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
};

const titleStyle: React.CSSProperties = {
  fontSize: 18,
  fontWeight: 700,
  margin: 0,
};

const closeBtnStyle: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  width: 30,
  height: 30,
  background: "#f1f5f9",
  border: "1px solid #e2e8f0",
  borderRadius: 9,
  color: "#64748b",
  cursor: "pointer",
};

const hintStyle: React.CSSProperties = {
  fontSize: 13,
  lineHeight: 1.6,
  color: "#475569",
  margin: "10px 0 12px",
};

const textareaStyle: React.CSSProperties = {
  width: "100%",
  boxSizing: "border-box",
  padding: "11px 13px",
  fontSize: 13,
  lineHeight: 1.6,
  border: "1px solid #e2e8f0",
  borderRadius: 11,
  background: "#f8fafc",
  outline: "none",
  resize: "vertical",
  fontFamily: "inherit",
  color: "#0f172a",
};

const counterStyle: React.CSSProperties = {
  fontSize: 11.5,
  color: "#94a3b8",
  margin: "4px 2px 12px",
};

const buildBtnStyle: React.CSSProperties = {
  width: "100%",
  padding: "11px 0",
  fontSize: 14,
  fontWeight: 700,
  background: "linear-gradient(180deg, #3b82f6 0%, #2563eb 100%)",
  color: "#fff",
  border: "1px solid rgba(37,99,235,0.5)",
  borderRadius: 11,
  boxShadow: "0 6px 18px rgba(37,99,235,0.32)",
};

const resultStyle: React.CSSProperties = {
  marginTop: 14,
  padding: "12px 14px",
  background: "#f0fdf4",
  border: "1px solid #bbf7d0",
  borderRadius: 8,
};

const pathStyle: React.CSSProperties = {
  display: "block",
  fontSize: 11.5,
  background: "#e2e8f0",
  padding: "6px 8px",
  borderRadius: 6,
  wordBreak: "break-all",
  color: "#334155",
};

const errStyle: React.CSSProperties = {
  marginTop: 12,
  fontSize: 12.5,
  color: "#dc2626",
  lineHeight: 1.6,
};
