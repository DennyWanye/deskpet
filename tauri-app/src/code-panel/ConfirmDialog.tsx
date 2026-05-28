// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * P4-S24 followup — minimal modal confirm dialog.
 *
 * No external deps; styled to match the dark code-panel chrome. Used
 * for destructive actions like "delete project session" so users
 * can't lose state to a slipped click.
 *
 * Usage:
 *   const [confirm, setConfirm] = useState<null | { ... }>(null);
 *   ...
 *   {confirm && <ConfirmDialog {...confirm} onCancel={() => setConfirm(null)} />}
 */
import type React from "react";

export interface ConfirmDialogProps {
  title: string;
  message: React.ReactNode;
  /** Visible label on the destructive action button (defaults to "确认"). */
  confirm_label?: string;
  /** Visible label on the cancel button (defaults to "取消"). */
  cancel_label?: string;
  /**
   * "danger" → red confirm button (use for delete/destroy).
   * "primary" → blue (use for non-destructive flows).
   */
  variant?: "danger" | "primary";
  onConfirm: () => void;
  onCancel: () => void;
}

export function ConfirmDialog({
  title,
  message,
  confirm_label = "确认",
  cancel_label = "取消",
  variant = "danger",
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const confirmBg = variant === "danger" ? "#dc2626" : "#2563eb";
  const confirmHover = variant === "danger" ? "#b91c1c" : "#1d4ed8";
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="confirm-dialog-title"
      onClick={(e) => {
        // Click on backdrop = cancel. Stop on dialog body so inner
        // clicks don't bubble.
        if (e.target === e.currentTarget) onCancel();
      }}
      onKeyDown={(e) => {
        if (e.key === "Escape") onCancel();
        if (e.key === "Enter") onConfirm();
      }}
      tabIndex={-1}
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0, 0, 0, 0.55)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 1000,
        backdropFilter: "blur(4px)",
      }}
    >
      <div
        style={{
          minWidth: 320,
          maxWidth: 480,
          background: "#0f1218",
          color: "#e2e8f0",
          border: "1px solid rgba(148, 163, 184, 0.30)",
          borderRadius: 10,
          padding: "18px 20px 16px",
          boxShadow: "0 10px 40px rgba(0, 0, 0, 0.5)",
          fontFamily:
            '-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif',
        }}
      >
        <h3
          id="confirm-dialog-title"
          style={{ fontSize: 14, margin: "0 0 10px", fontWeight: 600 }}
        >
          {title}
        </h3>
        <div
          style={{
            fontSize: 12.5,
            color: "#cbd5e1",
            lineHeight: 1.55,
            marginBottom: 16,
          }}
        >
          {message}
        </div>
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
          <button
            type="button"
            onClick={onCancel}
            style={{
              background: "rgba(148, 163, 184, 0.18)",
              color: "#e2e8f0",
              border: "1px solid rgba(148, 163, 184, 0.30)",
              borderRadius: 5,
              padding: "5px 14px",
              fontSize: 12,
              cursor: "pointer",
            }}
          >
            {cancel_label}
          </button>
          <button
            type="button"
            autoFocus
            onClick={onConfirm}
            style={{
              background: confirmBg,
              color: "#fff",
              border: "none",
              borderRadius: 5,
              padding: "5px 14px",
              fontSize: 12,
              fontWeight: 600,
              cursor: "pointer",
            }}
            onMouseEnter={(e) => {
              (e.currentTarget as HTMLButtonElement).style.background =
                confirmHover;
            }}
            onMouseLeave={(e) => {
              (e.currentTarget as HTMLButtonElement).style.background =
                confirmBg;
            }}
          >
            {confirm_label}
          </button>
        </div>
      </div>
    </div>
  );
}
