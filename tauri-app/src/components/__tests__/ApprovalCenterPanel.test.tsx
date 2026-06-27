// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * WI-TG-2 — ApprovalCenterPanel 聚合 + 批量决策 vitest 覆盖。
 *
 * 项目约定（同 MemoryPanel.facts.test.tsx）：不 mount React 树，把面板关键路径
 * 拆成纯函数导出后直接 import 断言。
 *
 * 覆盖 DoD（plans/2026-06-22-context-and-agent-optimization §3.5 WI-TG-2）：
 *   1. 聚合多个 pending（去重 + 顺序稳定）
 *   2. 批量批准 = 对每个 request_id 各发一条 allow（协议复用，gate 决策不变）
 *   3. 批量拒绝同理
 *   4. 只读「列 pending」查询消息形状
 *   5. 实时 permission_request 推送规整 + merge
 *   6. 决议后从列表移除
 */
import { describe, it, expect } from "vitest";

import {
  buildPendingListMessage,
  buildDecisionMessage,
  mergePending,
  removeResolved,
  pendingFromRequest,
  type ApprovalDecision,
} from "../ApprovalCenterPanel";
import type { PendingPermissionItem } from "../../types/messages";

function mkPending(
  over: Partial<PendingPermissionItem> & { request_id: string },
): PendingPermissionItem {
  return pendingFromRequest({
    request_id: over.request_id,
    category: over.category ?? "shell",
    summary: over.summary ?? `do ${over.request_id}`,
    params: over.params ?? { command: "echo" },
    default_action: over.default_action ?? "prompt",
    dangerous: over.dangerous ?? true,
    session_id: over.session_id ?? "s1",
  });
}

// ======================================================================
// 1) 只读查询消息
// ======================================================================
describe("WI-TG-2 · buildPendingListMessage", () => {
  it("test_pending_list_msg_with_session", () => {
    expect(buildPendingListMessage("s1")).toEqual({
      type: "permissions_pending_list",
      payload: { session_id: "s1" },
    });
  });

  it("test_pending_list_msg_no_session_omits_field", () => {
    expect(buildPendingListMessage()).toEqual({
      type: "permissions_pending_list",
      payload: {},
    });
  });
});

// ======================================================================
// 2) 批量决策构造 —— 复用既有 permission_response 协议
// ======================================================================
describe("WI-TG-2 · buildDecisionMessage", () => {
  it.each<ApprovalDecision>(["allow", "allow_session", "deny"])(
    "test_decision_msg_%s",
    (decision) => {
      expect(buildDecisionMessage("req-1", decision)).toEqual({
        type: "permission_response",
        payload: { request_id: "req-1", decision },
      });
    },
  );

  it("test_batch_approve_emits_one_allow_per_id", () => {
    // 模拟「全部批准」：对每个 pending item 各构造一条 allow。
    const pending = [
      mkPending({ request_id: "a" }),
      mkPending({ request_id: "b" }),
      mkPending({ request_id: "c" }),
    ];
    const msgs = pending.map((p) =>
      buildDecisionMessage(p.request_id, "allow"),
    );
    expect(msgs).toHaveLength(3);
    expect(msgs.map((m) => m.payload.request_id)).toEqual(["a", "b", "c"]);
    expect(msgs.every((m) => m.payload.decision === "allow")).toBe(true);
    // 协议字面量未变 —— 不是新协议，gate 决策逻辑完全复用。
    expect(msgs[0].type).toBe("permission_response");
  });

  it("test_batch_deny_emits_one_deny_per_id", () => {
    const pending = [mkPending({ request_id: "x" }), mkPending({ request_id: "y" })];
    const msgs = pending.map((p) => buildDecisionMessage(p.request_id, "deny"));
    expect(msgs.every((m) => m.payload.decision === "deny")).toBe(true);
  });
});

// ======================================================================
// 3) 聚合 merge —— 去重 + 顺序稳定
// ======================================================================
describe("WI-TG-2 · mergePending", () => {
  it("test_aggregate_multiple_pending", () => {
    const merged = mergePending(
      [mkPending({ request_id: "a" })],
      [mkPending({ request_id: "b" }), mkPending({ request_id: "c" })],
    );
    expect(merged.map((p) => p.request_id)).toEqual(["a", "b", "c"]);
  });

  it("test_dedup_by_request_id_keeps_order_newest_value_wins", () => {
    const merged = mergePending(
      [mkPending({ request_id: "a", summary: "old" })],
      [mkPending({ request_id: "a", summary: "new" })],
    );
    expect(merged).toHaveLength(1);
    expect(merged[0].summary).toBe("new"); // 新值覆盖
  });

  it("test_snapshot_authoritative_replaces_when_base_empty", () => {
    const snapshot = [
      mkPending({ request_id: "a" }),
      mkPending({ request_id: "b" }),
    ];
    expect(mergePending([], snapshot).map((p) => p.request_id)).toEqual([
      "a",
      "b",
    ]);
  });
});

// ======================================================================
// 4) 决议后移除
// ======================================================================
describe("WI-TG-2 · removeResolved", () => {
  it("test_remove_resolved_ids_after_batch", () => {
    const cur = [
      mkPending({ request_id: "a" }),
      mkPending({ request_id: "b" }),
      mkPending({ request_id: "c" }),
    ];
    const after = removeResolved(cur, ["a", "c"]);
    expect(after.map((p) => p.request_id)).toEqual(["b"]);
  });

  it("test_remove_all_clears_list", () => {
    const cur = [mkPending({ request_id: "a" })];
    expect(removeResolved(cur, ["a"])).toEqual([]);
  });
});

// ======================================================================
// 5) 实时推送规整
// ======================================================================
describe("WI-TG-2 · pendingFromRequest", () => {
  it("test_request_push_normalized_with_defaults", () => {
    const item = pendingFromRequest({ request_id: "only-id" });
    expect(item).toEqual({
      request_id: "only-id",
      category: "",
      summary: "",
      params: {},
      default_action: "prompt",
      dangerous: false,
      session_id: "",
    });
  });

  it("test_request_push_preserves_fields", () => {
    const item = pendingFromRequest({
      request_id: "r",
      category: "network",
      summary: "fetch X",
      params: { url: "http://x" },
      default_action: "deny",
      dangerous: true,
      session_id: "s9",
    });
    expect(item.category).toBe("network");
    expect(item.params).toEqual({ url: "http://x" });
    expect(item.session_id).toBe("s9");
  });
});
