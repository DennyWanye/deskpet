/**
 * P4-S20 Wave 1c — PermissionPopup
 *
 * Modal that overlays the chat surface whenever the backend sends a
 * `permission_request` IPC. Three buttons:
 *
 *   - Yes (one time)              → decision="allow"
 *   - Yes, always for this session → decision="allow_session"
 *   - No                          → decision="deny"
 *
 * ESC also denies (per spec). The popup blocks pointer events on the
 * rest of the UI via a backdrop.
 *
 * Spec: openspec/changes/deskpet-skill-platform/specs/permission-gate/spec.md
 */
import React, { useEffect } from "react";

import type {
  PermissionRequest,
  PermissionCategory,
} from "../types/skillPlatform";

type Decision = "allow" | "allow_session" | "deny";

interface Props {
  request: PermissionRequest["payload"] | null;
  onResolve: (decision: Decision) => void;
}

const CATEGORY_STYLE: Record<
  PermissionCategory,
  { color: string; label: string; danger: boolean }
> = {
  read_file: { color: "#6b7280", label: "读文件", danger: false },
  read_file_sensitive: {
    color: "#dc2626",
    label: "读敏感文件",
    danger: true,
  },
  write_file: { color: "#d97706", label: "写文件", danger: false },
  desktop_write: {
    color: "#d97706",
    label: "写桌面",
    danger: false,
  },
  shell: { color: "#dc2626", label: "执行命令", danger: true },
  network: { color: "#d97706", label: "网络请求", danger: false },
  mcp_call: { color: "#d97706", label: "MCP 调用", danger: false },
  skill_install: {
    color: "#dc2626",
    label: "安装技能",
    danger: true,
  },
};

export const PermissionPopup: React.FC<Props> = ({ request, onResolve }) => {
  useEffect(() => {
    if (!request) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onResolve("deny");
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [request, onResolve]);

  if (!request) return null;
  const style =
    CATEGORY_STYLE[request.category as PermissionCategory] ??
    CATEGORY_STYLE.write_file;

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.45)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 9999,
      }}
      role="dialog"
      aria-modal="true"
      aria-labelledby="permission-popup-title"
    >
      <div
        style={{
          background: "white",
          borderRadius: 12,
          padding: 20,
          width: 480,
          maxWidth: "92vw",
          boxShadow: "0 20px 60px rgba(0,0,0,0.25)",
          borderTop: `4px solid ${style.color}`,
        }}
      >
        <h3
          id="permission-popup-title"
          style={{
            margin: 0,
            fontSize: 16,
            color: style.color,
            fontWeight: 600,
          }}
        >
          {style.danger ? "⚠ " : ""}权限请求 · {style.label}
        </h3>
        <p style={{ marginTop: 12, fontSize: 14, color: "#1f2937" }}>
          {request.summary}
        </p>
        {Object.keys(request.params || {}).length > 0 && (
          <details
            style={{
              marginTop: 8,
              fontSize: 12,
              color: "#6b7280",
              maxHeight: 160,
              overflow: "auto",
            }}
          >
            <summary>查看详细参数</summary>
            <pre
              style={{
                background: "#f3f4f6",
                padding: 8,
                borderRadius: 6,
                fontSize: 11,
                whiteSpace: "pre-wrap",
                wordBreak: "break-all",
              }}
            >
              {JSON.stringify(request.params, null, 2)}
            </pre>
          </details>
        )}
        <div
          style={{
            marginTop: 16,
            display: "flex",
            gap: 8,
            justifyContent: "flex-end",
            flexWrap: "wrap",
          }}
        >
          <button
            type="button"
            onClick={() => onResolve("deny")}
            style={btnStyle("#e5e7eb", "#1f2937")}
          >
            拒绝 (Esc)
          </button>
          <button
            type="button"
            onClick={() => onResolve("allow_session")}
            style={btnStyle("#bfdbfe", "#1e3a8a")}
          >
            本会话始终允许
          </button>
          <button
            type="button"
            onClick={() => onResolve("allow")}
            style={btnStyle(style.color, "white")}
          >
            允许一次
          </button>
        </div>
      </div>
    </div>
  );
};

function btnStyle(bg: string, fg: string): React.CSSProperties {
  return {
    background: bg,
    color: fg,
    border: "none",
    borderRadius: 6,
    padding: "8px 14px",
    fontSize: 14,
    fontWeight: 500,
    cursor: "pointer",
  };
}

export default PermissionPopup;
