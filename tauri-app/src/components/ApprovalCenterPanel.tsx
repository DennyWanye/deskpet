// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * WI-TG-2 — ApprovalCenterPanel（审批聚合视图）
 *
 * 现状（重构前）：`PermissionPopup.tsx` + `usePermissionRequests.ts` 一次只渲染
 * 一个 pending 权限请求（FIFO 单弹窗）。当 agent 并发 dispatch 多个敏感工具时，
 * 用户得逐个点，看不到「待办全景」。
 *
 * 本面板把**多个** pending 请求聚合在一处，支持**批量批准 / 批量拒绝**。它复用
 * 既有的 `permission_response` 协议——批量批准 = 对每个 request_id 各发一条
 * `{decision: "allow"}`，批量拒绝同理发 `{decision: "deny"}`。后端 gate 决策逻辑
 * **完全不变**；本面板只做聚合 UX，不加重权限墙。
 *
 * 数据来源：后端只读接口 `permissions_pending_list` →
 * `permissions_pending_list_response`（见 backend/p4_ipc.py + gate.list_pending）。
 * 也会监听 `permission_request` 推送，实时把新到的 pending 并进列表。
 *
 * BC：默认 `enabled={false}` → 整个面板不渲染（return null），现有单弹窗路径
 * （App.tsx 的 PermissionPopup）完全不受影响。只有显式开 prop 才显示聚合视图。
 *
 * 测试约定（同 MemoryPanel.facts.test.tsx）：vitest env=jsdom，但本组件的关键
 * 逻辑（消息构造 + 列表 reducer + 批量决策构造）拆成纯函数导出，测试直接 import
 * 断言，不 mount React 树。
 */
import React, { useCallback, useEffect, useRef, useState } from "react";

import type { ControlChannel } from "../ws/ControlChannel";
import type {
  IncomingMessage,
  PendingPermissionItem,
} from "../types/messages";
import { buttonStyle, surfaceLight } from "../theme/components";
import { tokens } from "../theme/tokens";

export type ApprovalDecision = "allow" | "allow_session" | "deny";

// ----------------------------------------------------------------------
// Pure helpers — wire format + list reducers（测试直接 import）
// ----------------------------------------------------------------------

/** 构造「列出当前 session pending 权限请求」的只读查询消息。 */
export function buildPendingListMessage(
  sessionId?: string,
): { type: "permissions_pending_list"; payload: { session_id?: string } } {
  const payload: { session_id?: string } = {};
  if (sessionId) payload.session_id = sessionId;
  return { type: "permissions_pending_list", payload };
}

/**
 * 构造一条对单个 request_id 的 `permission_response`（与既有协议一致）。
 * 批量决策 = 对每个 pending item 各调一次本函数发送。
 */
export function buildDecisionMessage(
  requestId: string,
  decision: ApprovalDecision,
): {
  type: "permission_response";
  payload: { request_id: string; decision: ApprovalDecision };
} {
  return {
    type: "permission_response",
    payload: { request_id: requestId, decision },
  };
}

/**
 * 把后端快照 + 实时推送 merge 进当前列表（按 request_id 去重，新值覆盖）。
 * 顺序：保留既有顺序，新 request_id 追加到尾部 —— 用户视觉稳定。
 */
export function mergePending(
  current: PendingPermissionItem[],
  incoming: PendingPermissionItem[],
): PendingPermissionItem[] {
  const byId = new Map<string, PendingPermissionItem>();
  const order: string[] = [];
  for (const item of current) {
    if (!byId.has(item.request_id)) order.push(item.request_id);
    byId.set(item.request_id, item);
  }
  for (const item of incoming) {
    if (!byId.has(item.request_id)) order.push(item.request_id);
    byId.set(item.request_id, item);
  }
  return order.map((id) => byId.get(id)!).filter(Boolean);
}

/** 从列表移除一批已决议的 request_id（批量批准/拒绝后用）。 */
export function removeResolved(
  current: PendingPermissionItem[],
  resolvedIds: Iterable<string>,
): PendingPermissionItem[] {
  const drop = new Set(resolvedIds);
  return current.filter((item) => !drop.has(item.request_id));
}

/** 把单条 permission_request 推送规整成 PendingPermissionItem。 */
export function pendingFromRequest(
  payload: Partial<PendingPermissionItem> & { request_id: string },
): PendingPermissionItem {
  return {
    request_id: payload.request_id,
    category: payload.category ?? "",
    summary: payload.summary ?? "",
    params: payload.params ?? {},
    default_action: payload.default_action ?? "prompt",
    dangerous: payload.dangerous ?? false,
    session_id: payload.session_id ?? "",
  };
}

// ----------------------------------------------------------------------
// Component
// ----------------------------------------------------------------------

interface Props {
  channel: ControlChannel | null;
  /**
   * BC gate：默认 false → 面板不渲染。App 只在显式开聚合视图时传 true，
   * 现有单弹窗路径不受影响。
   */
  enabled?: boolean;
  /** 限定只看某个 session 的 pending（缺省 = 全部）。 */
  sessionId?: string;
}

const CATEGORY_ACCENT: Record<string, string> = {
  shell: tokens.color.danger.bg,
  skill_install: tokens.color.danger.bg,
  read_file_sensitive: tokens.color.danger.bg,
  write_file: tokens.color.warning.bg,
  desktop_write: tokens.color.warning.bg,
  network: tokens.color.warning.bg,
  mcp_call: tokens.color.warning.bg,
};

export const ApprovalCenterPanel: React.FC<Props> = ({
  channel,
  enabled = false,
  sessionId,
}) => {
  const [pending, setPending] = useState<PendingPermissionItem[]>([]);
  const pendingRef = useRef<PendingPermissionItem[]>([]);
  pendingRef.current = pending;

  // Subscribe to backend snapshots + live permission_request pushes.
  useEffect(() => {
    if (!enabled || !channel) return undefined;
    // Initial pull.
    channel.send(buildPendingListMessage(sessionId) as never);
    const off = channel.onMessage((msg: IncomingMessage) => {
      if (msg.type === "permissions_pending_list_response") {
        const items = (msg.payload?.pending ?? []) as PendingPermissionItem[];
        // Snapshot is authoritative for which ids exist.
        setPending(mergePending([], items.map(pendingFromRequest)));
      } else if (msg.type === "permission_request") {
        const p = (msg as { payload?: { request_id?: string } }).payload;
        if (!p || !p.request_id) return;
        const item = pendingFromRequest(
          p as Partial<PendingPermissionItem> & { request_id: string },
        );
        if (sessionId && item.session_id && item.session_id !== sessionId) {
          return;
        }
        setPending((cur) => mergePending(cur, [item]));
      }
    });
    return () => {
      off();
    };
  }, [enabled, channel, sessionId]);

  const decideAll = useCallback(
    (decision: ApprovalDecision) => {
      if (!channel) return;
      const ids = pendingRef.current.map((p) => p.request_id);
      for (const id of ids) {
        channel.send(buildDecisionMessage(id, decision) as never);
      }
      setPending((cur) => removeResolved(cur, ids));
    },
    [channel],
  );

  const decideOne = useCallback(
    (requestId: string, decision: ApprovalDecision) => {
      if (!channel) return;
      channel.send(buildDecisionMessage(requestId, decision) as never);
      setPending((cur) => removeResolved(cur, [requestId]));
    },
    [channel],
  );

  if (!enabled) return null;
  if (pending.length === 0) return null;

  return (
    <div
      role="region"
      aria-label="审批中心"
      style={{
        ...surfaceLight,
        position: "fixed",
        right: tokens.space.lg,
        bottom: tokens.space.lg,
        width: 360,
        maxWidth: "92vw",
        maxHeight: "70vh",
        overflow: "auto",
        zIndex: 9998,
        display: "flex",
        flexDirection: "column",
        gap: tokens.space.sm,
        padding: tokens.space.md,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: tokens.space.sm,
        }}
      >
        <strong style={{ fontSize: tokens.text.md.size }}>
          审批中心
          <span
            style={{
              marginLeft: 6,
              fontSize: tokens.text.xs.size,
              color: tokens.color.neutral[500],
            }}
          >
            {pending.length} 待处理
          </span>
        </strong>
        <div style={{ display: "flex", gap: tokens.space.xs }}>
          <button
            type="button"
            onClick={() => decideAll("deny")}
            style={buttonStyle("secondary", "sm")}
            title="拒绝全部待处理请求"
          >
            全部拒绝
          </button>
          <button
            type="button"
            onClick={() => decideAll("allow")}
            style={buttonStyle("primary", "sm")}
            title="批准全部待处理请求（各发一次 allow）"
          >
            全部批准
          </button>
        </div>
      </div>

      <ul
        style={{
          listStyle: "none",
          margin: 0,
          padding: 0,
          display: "flex",
          flexDirection: "column",
          gap: tokens.space.xs,
        }}
      >
        {pending.map((item) => (
          <li
            key={item.request_id}
            style={{
              border: `1px solid ${tokens.color.neutral[200]}`,
              borderLeft: `3px solid ${
                CATEGORY_ACCENT[item.category] ?? tokens.color.info.bg
              }`,
              borderRadius: tokens.radius.md,
              padding: tokens.space.sm,
              display: "flex",
              flexDirection: "column",
              gap: tokens.space.xs,
            }}
          >
            <div
              style={{
                fontSize: tokens.text.sm.size,
                color: tokens.color.neutral[800],
                wordBreak: "break-word",
              }}
            >
              <span
                style={{
                  fontFamily: tokens.font.mono,
                  fontSize: tokens.text.xs.size,
                  color: tokens.color.neutral[500],
                  marginRight: 6,
                }}
              >
                {item.category}
              </span>
              {item.summary}
            </div>
            <div style={{ display: "flex", gap: tokens.space.xs }}>
              <button
                type="button"
                onClick={() => decideOne(item.request_id, "deny")}
                style={buttonStyle("secondary", "sm")}
              >
                拒绝
              </button>
              <button
                type="button"
                onClick={() => decideOne(item.request_id, "allow")}
                style={buttonStyle("primary", "sm")}
              >
                允许
              </button>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
};

export default ApprovalCenterPanel;
