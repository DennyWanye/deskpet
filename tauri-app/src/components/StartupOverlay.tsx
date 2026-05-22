/**
 * P3-S8 — Startup splash + error overlay（高级感重设计）.
 *
 * 加载态：居中的深色玻璃卡片 + 双环旋转指示器 + 文案。
 * 失败态：换成错误卡片，配 alert 图标 + 重试 / 打开日志目录 / 退出。
 *
 * Why not `tauri-plugin-dialog::blocking_show`? The blocking dialog
 * freezes the Tauri event loop, which — because our window uses
 * `transparent:true` + `decorations:false` — can leave the user
 * looking at a black rectangle before the dialog paints. An in-DOM
 * overlay renders immediately inside the WebView2 layer.
 */
import { memo } from "react";

import { Icon } from "./Icon";
import { dark, darkButton } from "../theme/components";

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
            <div style={spinnerWrapStyle} aria-hidden>
              <div style={spinnerRingStyle} />
              <div style={spinnerCoreStyle}>
                <Icon name="sparkle" size={20} />
              </div>
            </div>
            <div style={titleStyle}>正在启动语音服务</div>
            <div style={hintStyle}>
              首次启动需要 20–60 秒加载模型，请耐心等待
            </div>
          </>
        )}
        {isError && (
          <>
            <div style={errIconWrapStyle} aria-hidden>
              <Icon name="alert" size={22} />
            </div>
            <div style={titleStyle}>启动失败</div>
            <pre style={errorTextStyle}>{errorMessage ?? "未知错误"}</pre>
            <div style={btnRowStyle}>
              <button
                type="button"
                data-testid="startup-retry"
                onClick={onRetry}
                style={darkButton("primary", "md")}
              >
                <Icon name="refresh" size={14} />
                重试
              </button>
              <button
                type="button"
                data-testid="startup-open-log"
                onClick={onOpenLogDir}
                style={darkButton("neutral", "md")}
              >
                <Icon name="folder" size={14} />
                打开日志目录
              </button>
              <button
                type="button"
                data-testid="startup-exit"
                onClick={onExit}
                style={darkButton("neutral", "md")}
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
        @keyframes deskpet-splash-pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.55; }
        }
      `}</style>
    </div>
  );
}

export const StartupOverlay = memo(StartupOverlayImpl);

const overlayStyle: React.CSSProperties = {
  position: "fixed",
  inset: 0,
  background:
    "radial-gradient(ellipse at center, rgba(10,12,20,0.78) 0%, rgba(4,5,9,0.90) 100%)",
  backdropFilter: "blur(8px)",
  WebkitBackdropFilter: "blur(8px)",
  display: "grid",
  placeItems: "center",
  zIndex: 5000,
  padding: 16,
};

const cardStyle: React.CSSProperties = {
  background: dark.bg,
  border: `1px solid ${dark.border}`,
  borderRadius: 18,
  padding: "26px 24px",
  maxWidth: "min(92vw, 320px)",
  width: "100%",
  display: "grid",
  gap: 12,
  boxShadow:
    "0 24px 60px rgba(0,0,0,0.55), inset 0 1px 0 rgba(255,255,255,0.07)",
  color: dark.text,
  fontFamily:
    '"Inter","PingFang SC","Microsoft YaHei UI",sans-serif',
  fontSize: 13,
  animation: "bp-pop-in 280ms cubic-bezier(0.16,1,0.3,1)",
};

const spinnerWrapStyle: React.CSSProperties = {
  position: "relative",
  width: 52,
  height: 52,
  margin: "2px auto 4px",
};

const spinnerRingStyle: React.CSSProperties = {
  position: "absolute",
  inset: 0,
  borderRadius: "50%",
  border: "3px solid rgba(255,255,255,0.07)",
  borderTopColor: "#4f93ff",
  borderRightColor: "#60a5fa",
  animation: "deskpet-splash-spin 0.95s cubic-bezier(0.5,0.1,0.5,0.9) infinite",
};

const spinnerCoreStyle: React.CSSProperties = {
  position: "absolute",
  inset: 9,
  borderRadius: "50%",
  display: "grid",
  placeItems: "center",
  background: "rgba(79,147,255,0.14)",
  color: "#7fb0ff",
  animation: "deskpet-splash-pulse 1.8s ease-in-out infinite",
};

const errIconWrapStyle: React.CSSProperties = {
  width: 48,
  height: 48,
  margin: "2px auto 2px",
  borderRadius: "50%",
  display: "grid",
  placeItems: "center",
  background: "rgba(239,68,68,0.14)",
  color: "#f87171",
  border: "1px solid rgba(239,68,68,0.30)",
};

const titleStyle: React.CSSProperties = {
  fontSize: 15,
  fontWeight: 600,
  textAlign: "center",
  letterSpacing: 0.2,
};

const hintStyle: React.CSSProperties = {
  fontSize: 12,
  color: dark.textMuted,
  textAlign: "center",
  lineHeight: 1.6,
};

const errorTextStyle: React.CSSProperties = {
  fontSize: 11.5,
  background: "rgba(0,0,0,0.30)",
  padding: "10px 12px",
  borderRadius: 10,
  border: `1px solid ${dark.hairline}`,
  whiteSpace: "pre-wrap",
  wordBreak: "break-word",
  margin: 0,
  maxHeight: 180,
  overflowY: "auto",
  fontFamily: '"JetBrains Mono","Cascadia Code",Consolas,monospace',
  color: "#fca5a5",
  lineHeight: 1.5,
};

const btnRowStyle: React.CSSProperties = {
  display: "flex",
  gap: 8,
  justifyContent: "center",
  flexWrap: "wrap",
  marginTop: 2,
};
