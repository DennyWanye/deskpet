// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * WI-T2-B3 v2 — argument hint bar.
 *
 * 跟随 InputBar 显示在输入框上方（顶部小条），告诉用户当前 `/cmd` 还需要
 * 哪些参数 + 当前在填第几个。
 *
 * 显示规则：
 *  - schema 空 → 不渲染
 *  - 已填的 args 灰色 + 完成
 *  - 当前正在填的 arg 高亮 + 蓝色
 *  - 未填的 args 灰白
 */
import type { CSSProperties } from "react";

export interface ArgSchema {
  name: string;
  type: string;
  description: string;
  required: boolean;
}

export function ArgHintBar({
  commandName,
  argSchema,
  currentArgIndex,
}: {
  commandName: string;
  argSchema: ArgSchema[];
  currentArgIndex: number;
}) {
  if (!argSchema || argSchema.length === 0) return null;

  const bar: CSSProperties = {
    padding: "4px 10px",
    background: "rgba(30, 41, 59, 0.6)",
    borderRadius: 6,
    border: "1px solid rgba(148, 163, 184, 0.15)",
    fontSize: 11,
    color: "#cbd5e1",
    display: "flex",
    alignItems: "center",
    gap: 6,
  };

  return (
    <div style={bar} data-testid="arg-hint-bar">
      <span style={{ color: "#60a5fa", fontWeight: 600 }}>/{commandName}</span>
      {argSchema.map((arg, i) => {
        const filled = i < currentArgIndex;
        const current = i === currentArgIndex;
        const color = current
          ? "#60a5fa"
          : filled
            ? "#475569"
            : "#94a3b8";
        const decoration = filled ? "line-through" : "none";
        return (
          <span
            key={arg.name}
            style={{
              color,
              textDecoration: decoration,
              fontWeight: current ? 600 : 400,
            }}
            data-testid={`arg-${arg.name}`}
            title={arg.description || arg.type}
          >
            {arg.required ? `<${arg.name}>` : `[${arg.name}]`}
            {arg.description && current && (
              <span style={{ opacity: 0.6, marginLeft: 4, fontSize: 10 }}>
                — {arg.description}
              </span>
            )}
          </span>
        );
      })}
    </div>
  );
}
