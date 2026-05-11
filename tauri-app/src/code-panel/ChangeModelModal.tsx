/**
 * multi-provider-management Phase 5 — "改 model" modal.
 *
 * Minimal modal triggered from a session card's provider dropdown
 * sub-menu. User types a model id string (free-form, like
 * "claude-4.7" or "gpt-5.4-turbo"); on confirm we emit
 * `code_session_set_model { session_id, model }`.
 *
 * Empty string is permitted and means "clear preferred_model" — handled
 * by `buildSetModelMessage` which normalises to `null`.
 */
import { useState, useEffect, useRef } from "react";

import { codePanelWS } from "./ws";
import { buildSetModelMessage } from "./SessionGridView";

export interface ChangeModelModalProps {
  session_id: string;
  /** Current preferred_model value (null/undefined → empty input). */
  current_model: string | null | undefined;
  onClose: () => void;
}

export function ChangeModelModal({
  session_id,
  current_model,
  onClose,
}: ChangeModelModalProps) {
  const [text, set_text] = useState<string>(current_model ?? "");
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const submit = () => {
    codePanelWS.send(buildSetModelMessage(session_id, text));
    onClose();
  };

  const clear_and_submit = () => {
    codePanelWS.send(buildSetModelMessage(session_id, null));
    onClose();
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      e.preventDefault();
      submit();
    } else if (e.key === "Escape") {
      e.preventDefault();
      onClose();
    }
  };

  return (
    <div
      style={overlayStyle}
      onClick={onClose}
      role="dialog"
      aria-label="改 model"
    >
      <div
        style={dialogStyle}
        onClick={(e) => e.stopPropagation()}
      >
        <header style={headerStyle}>改 model</header>
        <div style={bodyStyle}>
          <label style={labelStyle} htmlFor="change-model-input">
            preferred_model（留空 = 跟随 provider 默认）
          </label>
          <input
            id="change-model-input"
            ref={inputRef}
            type="text"
            value={text}
            placeholder="例如 claude-4.7 / gpt-5.4-turbo"
            onChange={(e) => set_text(e.target.value)}
            onKeyDown={onKeyDown}
            style={inputStyle}
          />
        </div>
        <footer style={footerStyle}>
          <button type="button" onClick={onClose} style={btnSecondary}>
            取消
          </button>
          <button type="button" onClick={clear_and_submit} style={btnSecondary}>
            清空
          </button>
          <button type="button" onClick={submit} style={btnPrimary}>
            保存
          </button>
        </footer>
      </div>
    </div>
  );
}

const overlayStyle: React.CSSProperties = {
  position: "fixed",
  inset: 0,
  background: "rgba(0, 0, 0, 0.55)",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  zIndex: 1000,
};

const dialogStyle: React.CSSProperties = {
  background: "#1e2330",
  color: "#e2e8f0",
  borderRadius: 8,
  padding: 18,
  width: 360,
  boxShadow: "0 10px 40px rgba(0,0,0,0.5)",
  border: "1px solid rgba(148,163,184,0.18)",
};

const headerStyle: React.CSSProperties = {
  fontSize: 14,
  fontWeight: 700,
  marginBottom: 12,
};

const bodyStyle: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 6,
  marginBottom: 14,
};

const labelStyle: React.CSSProperties = {
  fontSize: 11,
  color: "#94a3b8",
};

const inputStyle: React.CSSProperties = {
  background: "rgba(30, 35, 48, 0.85)",
  color: "#e2e8f0",
  border: "1px solid rgba(148, 163, 184, 0.30)",
  borderRadius: 6,
  padding: "6px 9px",
  fontSize: 12.5,
  outline: "none",
};

const footerStyle: React.CSSProperties = {
  display: "flex",
  justifyContent: "flex-end",
  gap: 8,
};

const btnPrimary: React.CSSProperties = {
  background: "#2563eb",
  color: "#fff",
  border: "none",
  borderRadius: 5,
  padding: "5px 12px",
  fontSize: 12,
  cursor: "pointer",
  fontWeight: 600,
};

const btnSecondary: React.CSSProperties = {
  background: "rgba(148, 163, 184, 0.18)",
  color: "#e2e8f0",
  border: "1px solid rgba(148, 163, 184, 0.30)",
  borderRadius: 5,
  padding: "5px 12px",
  fontSize: 12,
  cursor: "pointer",
};
