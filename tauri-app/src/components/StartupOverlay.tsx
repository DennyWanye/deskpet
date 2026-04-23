/**
 * P3-S8 — Startup splash + error overlay.
 *
 * Shows a centered spinner with "正在启动语音服务…" while the Rust
 * supervisor is still spawning the Python backend. On failure, swaps
 * in an error card with 重试 / 打开日志目录 / 退出 buttons.
 *
 * Why not `tauri-plugin-dialog::blocking_show`? The blocking dialog
 * freezes the Tauri event loop, which — because our window uses
 * `transparent:true` + `decorations:false` — can leave the user
 * looking at a black rectangle before the dialog paints. An in-DOM
 * overlay renders immediately inside the WebView2 layer.
 */
import { memo } from "react";

export type BootState = "starting" | "ready" | "failed";

export interface StartupOverlayProps {
  state: BootState;
  errorMessage: string | null;
  onRetry: () => void;
  onOpenLogDir: () => void;
  onExit: () => void;
}

function StartupOverlayImpl({
  state,
  errorMessage,
  onRetry,
  onOpenLogDir,
  onExit,
}: StartupOverlayProps) {
  if (state === "ready") return null;

  const isError = state === "failed";

  return (
    <div data-testid="startup-overlay" style={overlayStyle}>
      <div style={cardStyle} role={isError ? "alertdialog" : "status"}>
        {!isError && (
          <>
            <div style={spinnerStyle} aria-hidden />
            <div style={titleStyle}>正在启动语音服务…</div>
            <div style={hintStyle}>
              首次启动需要 20–60 秒加载模型，请耐心等待。
            </div>
          </>
        )}
        {isError && (
          <>
            <div style={{ ...titleStyle, color: "#b91c1c" }}>启动失败</div>
            <pre style={errorTextStyle}>
              {errorMessage ?? "未知错误"}
            </pre>
            <div style={btnRowStyle}>
              <button
                type="button"
                data-testid="startup-retry"
                onClick={onRetry}
                style={{ ...btnStyle, background: "#2563eb", color: "white" }}
              >
                重试
              </button>
              <button
                type="button"
                data-testid="startup-open-log"
                onClick={onOpenLogDir}
                style={btnStyle}
              >
                打开日志目录
              </button>
              <button
                type="button"
                data-testid="startup-exit"
                onClick={onExit}
                style={btnStyle}
              >
                退出
              </button>
            </div>
          </>
        )}
      </div>
      <style>{`
        @keyframes deskpet-splash-spin {
          to { transform: rotate(360deg); }
        }
      `}</style>
    </div>
  );
}

export const StartupOverlay = memo(StartupOverlayImpl);

const overlayStyle: React.CSSProperties = {
  position: "fixed",
  inset: 0,
  background: "rgba(0,0,0,0.72)",
  display: "grid",
  placeItems: "center",
  zIndex: 5000,
  padding: 12,
};

const cardStyle: React.CSSProperties = {
  background: "white",
  borderRadius: 10,
  padding: "18px 20px",
  maxWidth: "min(92vw, 440px)",
  display: "grid",
  gap: 10,
  boxShadow: "0 10px 30px rgba(0,0,0,0.35)",
  color: "#111",
  fontSize: 13,
};

const spinnerStyle: React.CSSProperties = {
  width: 28,
  height: 28,
  borderRadius: "50%",
  border: "3px solid #e5e7eb",
  borderTopColor: "#2563eb",
  animation: "deskpet-splash-spin 0.9s linear infinite",
  margin: "0 auto",
};

const titleStyle: React.CSSProperties = {
  fontSize: 14,
  fontWeight: 600,
  textAlign: "center",
};

const hintStyle: React.CSSProperties = {
  fontSize: 12,
  color: "#6b7280",
  textAlign: "center",
};

const errorTextStyle: React.CSSProperties = {
  fontSize: 12,
  background: "#f9fafb",
  padding: "8px 10px",
  borderRadius: 6,
  whiteSpace: "pre-wrap",
  wordBreak: "break-word",
  margin: 0,
  maxHeight: 180,
  overflowY: "auto",
  fontFamily: "inherit",
  color: "#1f2937",
};

const btnRowStyle: React.CSSProperties = {
  display: "flex",
  gap: 8,
  justifyContent: "flex-end",
  flexWrap: "wrap",
};

const btnStyle: React.CSSProperties = {
  padding: "6px 12px",
  borderRadius: 4,
  border: "1px solid #d1d5db",
  background: "white",
  fontSize: 12,
  cursor: "pointer",
};
