// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * WI-T2-B2 v2 — Slash command autocomplete dropdown.
 *
 * 受控组件：父 InputBar 传入 candidates + selectedIdx + onAccept。
 * 自身不管 filter / 状态机，纯渲染 + click 转发。
 *
 * 显示规则：
 *  - candidates 为空 → 不渲染（dropdown 隐藏）
 *  - 高亮 selectedIdx 行
 *  - hover 时不改 selectedIdx（避免 mouse + keyboard 抢）
 *  - click 单行 → onAccept(idx)
 */
import type { CSSProperties } from "react";

export interface SlashCommand {
  name: string;
  description: string;
  args_schema?: Array<{
    name: string;
    type: string;
    description: string;
    required: boolean;
  }>;
}

export function SlashDropdown({
  candidates,
  selectedIdx,
  onAccept,
}: {
  candidates: SlashCommand[];
  selectedIdx: number;
  onAccept: (idx: number) => void;
}) {
  if (candidates.length === 0) return null;

  const wrap: CSSProperties = {
    position: "absolute",
    bottom: "100%",
    left: 0,
    right: 0,
    marginBottom: 4,
    background: "rgba(20, 24, 36, 0.98)",
    border: "1px solid rgba(148, 163, 184, 0.3)",
    borderRadius: 8,
    boxShadow: "0 4px 20px rgba(0,0,0,0.4)",
    maxHeight: 280,
    overflowY: "auto",
    zIndex: 1000,
  };

  return (
    <div style={wrap} data-testid="slash-dropdown" role="listbox">
      {candidates.map((cmd, i) => {
        const selected = i === selectedIdx;
        const row: CSSProperties = {
          padding: "8px 12px",
          cursor: "pointer",
          background: selected ? "rgba(37, 99, 235, 0.25)" : "transparent",
          borderLeft: selected
            ? "3px solid #60a5fa"
            : "3px solid transparent",
          display: "flex",
          flexDirection: "column",
          gap: 2,
        };
        return (
          <div
            key={cmd.name}
            style={row}
            role="option"
            aria-selected={selected}
            data-testid={`slash-item-${cmd.name}`}
            onMouseDown={(e) => {
              // mousedown 不是 click — 避免 input blur 抢先
              e.preventDefault();
              onAccept(i);
            }}
          >
            <div
              style={{
                color: selected ? "#bfdbfe" : "#e2e8f0",
                fontSize: 13,
                fontWeight: selected ? 600 : 500,
              }}
            >
              /{cmd.name}
              {cmd.args_schema && cmd.args_schema.length > 0 && (
                <span style={{ opacity: 0.55, marginLeft: 6, fontSize: 11 }}>
                  {cmd.args_schema
                    .map((a) => (a.required ? `<${a.name}>` : `[${a.name}]`))
                    .join(" ")}
                </span>
              )}
            </div>
            {cmd.description && (
              <div style={{ color: "#94a3b8", fontSize: 11 }}>
                {cmd.description}
              </div>
            )}
          </div>
        );
      })}
      <div
        style={{
          padding: "4px 12px",
          fontSize: 10,
          color: "#64748b",
          borderTop: "1px solid rgba(148, 163, 184, 0.15)",
          background: "rgba(15, 18, 28, 0.6)",
        }}
      >
        ↑↓ 选择 · Tab/Enter 接受 · ESC 关闭
      </div>
    </div>
  );
}
