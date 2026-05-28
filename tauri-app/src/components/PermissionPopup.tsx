// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * P4-S20 Wave 1c — PermissionPopup (UI-revamp)
 *
 * 权限请求模态框。三个按钮：拒绝 / 本会话始终允许 / 允许一次。ESC 也是拒绝。
 *
 * 重构要点：
 * - 全部样式走 design tokens (theme/components)
 * - 按类别提供图标 + 配色，敏感类红、写入黄、读取灰
 * - 入场动画 (bp-pop-in)
 * - hover/focus 状态完整
 * - 主按钮回车确认（聚焦在"允许一次"）
 *
 * 规格：openspec/specs/permission-gate/spec.md
 */
import React, { useEffect, useRef } from "react";

import type {
  PermissionRequest,
  PermissionCategory,
} from "../types/skillPlatform";
import {
  backdropStyle,
  bannerStyle,
  buttonStyle,
  surfaceLight,
} from "../theme/components";
import { tokens } from "../theme/tokens";

type Decision = "allow" | "allow_session" | "deny";

interface Props {
  request: PermissionRequest["payload"] | null;
  onResolve: (decision: Decision) => void;
}

interface CategoryMeta {
  label: string;
  icon: string;
  level: "info" | "warning" | "error";
  hint: string;
}

const CATEGORY_META: Record<PermissionCategory, CategoryMeta> = {
  read_file: {
    label: "读取文件",
    icon: "📖",
    level: "info",
    hint: "读取你电脑上的一个文件。",
  },
  read_file_sensitive: {
    label: "读取敏感文件",
    icon: "🔒",
    level: "error",
    hint: "尝试读取看起来包含密钥/凭证的文件，请确认是你的本意。",
  },
  write_file: {
    label: "写入文件",
    icon: "✏️",
    level: "warning",
    hint: "在指定路径创建或修改文件。",
  },
  desktop_write: {
    label: "写入桌面",
    icon: "🗂️",
    level: "warning",
    hint: "在你的桌面创建一个文件。",
  },
  shell: {
    label: "执行命令",
    icon: "⚡",
    level: "error",
    hint: "运行 shell 命令 — 请仔细看清楚命令内容再决定。",
  },
  network: {
    label: "网络请求",
    icon: "🌐",
    level: "warning",
    hint: "向某个 URL 发起 HTTP 请求。",
  },
  mcp_call: {
    label: "MCP 调用",
    icon: "🔌",
    level: "warning",
    hint: "调用某个 MCP server 提供的工具。",
  },
  skill_install: {
    label: "安装技能",
    icon: "📦",
    level: "error",
    hint: "从 GitHub 安装第三方技能 — 请确认来源可信。",
  },
};

const DEFAULT_META: CategoryMeta = {
  label: "操作请求",
  icon: "⚙",
  level: "warning",
  hint: "请确认这次操作。",
};

export const PermissionPopup: React.FC<Props> = ({ request, onResolve }) => {
  const allowOnceRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    if (!request) return;
    // 自动聚焦"允许一次"，回车确认
    allowOnceRef.current?.focus();
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
  const meta =
    CATEGORY_META[request.category as PermissionCategory] ?? DEFAULT_META;

  // accent border color by level
  const accent =
    meta.level === "error"
      ? tokens.color.danger.bg
      : meta.level === "warning"
      ? tokens.color.warning.bg
      : tokens.color.info.bg;

  return (
    <div
      style={{ ...backdropStyle, zIndex: 9999 }}
      role="dialog"
      aria-modal="true"
      aria-labelledby="permission-popup-title"
    >
      <div
        style={{
          ...surfaceLight,
          width: 460,
          maxWidth: "92vw",
          maxHeight: "92vh",
          overflow: "hidden",
          display: "flex",
          flexDirection: "column",
          animation: `bp-pop-in ${tokens.duration.base}ms ${tokens.easing.out}`,
          borderTop: `4px solid ${accent}`,
        }}
      >
        {/* Header */}
        <div
          style={{
            padding: `${tokens.space.lg}px ${tokens.space.lg}px ${tokens.space.sm}px`,
            display: "flex",
            alignItems: "center",
            gap: tokens.space.sm,
          }}
        >
          <span
            aria-hidden
            style={{
              fontSize: 22,
              lineHeight: 1,
              filter:
                meta.level === "error"
                  ? `drop-shadow(0 0 6px ${tokens.color.danger.bg}55)`
                  : "none",
            }}
          >
            {meta.icon}
          </span>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div
              style={{
                fontSize: tokens.text.xs.size,
                color: tokens.color.neutral[500],
                fontWeight: tokens.weight.medium,
                marginBottom: 2,
              }}
            >
              权限请求
            </div>
            <h3
              id="permission-popup-title"
              style={{
                margin: 0,
                fontSize: tokens.text.lg.size,
                fontWeight: tokens.weight.semibold,
                color: tokens.color.neutral[900],
                lineHeight: 1.2,
              }}
            >
              {meta.label}
            </h3>
          </div>
        </div>

        {/* Summary + hint */}
        <div
          style={{
            padding: `0 ${tokens.space.lg}px`,
          }}
        >
          <div
            style={{
              fontSize: tokens.text.md.size,
              color: tokens.color.neutral[800],
              lineHeight: 1.55,
              marginBottom: tokens.space.sm,
            }}
          >
            {request.summary}
          </div>
          <div
            style={{
              ...bannerStyle(meta.level),
              fontSize: tokens.text.sm.size,
            }}
            role="status"
          >
            {meta.hint}
          </div>
        </div>

        {/* Params details (collapsible) */}
        {Object.keys(request.params || {}).length > 0 && (
          <div
            style={{
              padding: `${tokens.space.md}px ${tokens.space.lg}px 0`,
            }}
          >
            <details>
              <summary
                style={{
                  fontSize: tokens.text.sm.size,
                  color: tokens.color.neutral[500],
                  cursor: "pointer",
                  userSelect: "none",
                  paddingBottom: tokens.space.xs,
                }}
              >
                查看详细参数
              </summary>
              <pre
                style={{
                  background: tokens.color.neutral[50],
                  border: `1px solid ${tokens.color.neutral[200]}`,
                  padding: tokens.space.sm,
                  borderRadius: tokens.radius.md,
                  fontFamily: tokens.font.mono,
                  fontSize: tokens.text.xs.size,
                  lineHeight: 1.5,
                  color: tokens.color.neutral[700],
                  whiteSpace: "pre-wrap",
                  wordBreak: "break-all",
                  margin: 0,
                  maxHeight: 180,
                  overflow: "auto",
                }}
              >
                {JSON.stringify(request.params, null, 2)}
              </pre>
            </details>
          </div>
        )}

        {/* Footer / actions */}
        <div
          style={{
            padding: tokens.space.lg,
            display: "flex",
            justifyContent: "flex-end",
            gap: tokens.space.sm,
            flexWrap: "wrap",
          }}
        >
          <button
            type="button"
            className="bp-btn-secondary"
            onClick={() => onResolve("deny")}
            style={buttonStyle("secondary", "md")}
          >
            拒绝<span style={{ opacity: 0.5, marginLeft: 6 }}>Esc</span>
          </button>
          <button
            type="button"
            className="bp-btn-secondary"
            onClick={() => onResolve("allow_session")}
            style={buttonStyle("secondary", "md")}
            title="本会话内同类操作不再询问"
          >
            本会话始终允许
          </button>
          <button
            ref={allowOnceRef}
            type="button"
            className={meta.level === "error" ? "bp-btn-danger" : "bp-btn-primary"}
            onClick={() => onResolve("allow")}
            style={buttonStyle(meta.level === "error" ? "danger" : "primary", "md")}
          >
            允许一次
          </button>
        </div>
      </div>
    </div>
  );
};

export default PermissionPopup;
