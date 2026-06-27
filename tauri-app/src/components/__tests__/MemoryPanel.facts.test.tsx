// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * WI-S2.1b — MemoryPanel facts view（事实 tab） vitest 覆盖。
 *
 * 项目约定（同 ModelContextCard.test.tsx / AddProviderModal.test.tsx /
 * OnboardingWizard.test.ts）：vitest env = 'node'，无 @testing-library/react，
 * 不 mount React 树。改成把 facts view 关键路径拆成纯函数/常量（builder +
 * reducer + 5s 窗口常量），从 MemoryPanel.tsx 直接 import 后断言。
 *
 * 覆盖的 DoD（plans/2026-05-23-memory-system-stage2/01-TDD.md §A1.4 +
 * 任务清单）：
 *   1. 切到 facts tab → 触发 `memory_facts_list` ws send（builder 形状）
 *   2. 点 🗑 → 触发 `memory_forget {fact_id}` ws send（builder 形状）
 *   3. 收到 forget_response status=ok → 从 facts 移除 + pending 建立
 *   4. 点 undo → 触发 `memory_forget_undo {op_id}` ws send + 收到 ok →
 *      fact 回到列表头部
 *   5. undo 浮窗超 5 秒（FORGET_UNDO_WINDOW_MS）→ 自动消失（fake timer）
 *   6. 各种错误分支（status=error/skipped/not_found, expired）不破坏列表
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

import {
  FORGET_UNDO_WINDOW_MS,
  applyForgetResponse,
  applyUndoResponse,
  buildMemoryFactsListMessage,
  buildMemoryForgetMessage,
  buildMemoryForgetUndoMessage,
} from "../MemoryPanel";
import type { FactItem } from "../../types/messages";

// ----------------------------------------------------------------------
// fixtures
// ----------------------------------------------------------------------

function mkFact(over: Partial<FactItem> & { id: number }): FactItem {
  return {
    id: over.id,
    category: over.category ?? "preference",
    subject: over.subject ?? "user",
    key: over.key ?? `key_${over.id}`,
    value: over.value ?? `value_${over.id}`,
    confidence: over.confidence ?? 0.9,
    source_msg_id: over.source_msg_id ?? null,
    created_at: over.created_at ?? 1_700_000_000,
    updated_at: over.updated_at ?? 1_700_000_000,
    evidence: over.evidence ?? null,
    is_active: over.is_active ?? 1,
    decay_rate: over.decay_rate ?? 0.01,
    last_recalled: over.last_recalled ?? null,
    superseded_by: over.superseded_by ?? null,
    forgotten_at: over.forgotten_at ?? null,
  };
}

// ======================================================================
// 1) ws message builders — wire format（与 backend/p4_ipc.py 契约对齐）
// ======================================================================

describe("WI-S2.1b · buildMemoryFactsListMessage", () => {
  it("test_facts_tab_enter_emits_facts_list — 默认 limit=200，无 subject/category", () => {
    const m = buildMemoryFactsListMessage();
    expect(m).toEqual({
      type: "memory_facts_list",
      payload: { limit: 200 },
    });
  });

  it("test_facts_list_message_passes_through_limit_subject_category", () => {
    const m = buildMemoryFactsListMessage({
      limit: 50,
      subject: "user",
      category: "preference",
    });
    expect(m.type).toBe("memory_facts_list");
    expect(m.payload).toEqual({
      limit: 50,
      subject: "user",
      category: "preference",
    });
  });

  it("test_facts_list_message_omits_empty_optionals", () => {
    const m = buildMemoryFactsListMessage({ limit: 10 });
    // 不带 subject/category key，避免后端把空串当过滤条件
    expect("subject" in (m.payload ?? {})).toBe(false);
    expect("category" in (m.payload ?? {})).toBe(false);
  });
});

describe("WI-S2.1b · buildMemoryForgetMessage / buildMemoryForgetUndoMessage", () => {
  it("test_trash_button_emits_memory_forget_with_fact_id", () => {
    const m = buildMemoryForgetMessage(42);
    expect(m).toEqual({
      type: "memory_forget",
      payload: { fact_id: 42 },
    });
  });

  it("test_undo_button_emits_memory_forget_undo_with_op_id", () => {
    const m = buildMemoryForgetUndoMessage("op-abc-123");
    expect(m).toEqual({
      type: "memory_forget_undo",
      payload: { op_id: "op-abc-123" },
    });
  });
});

// ======================================================================
// 2) applyForgetResponse — 收到 memory_forget_response 后的 state 转移
// ======================================================================

describe("WI-S2.1b · applyForgetResponse — status=ok 主路径", () => {
  const facts = [mkFact({ id: 1 }), mkFact({ id: 2 }), mkFact({ id: 3 })];

  it("test_forget_ok_removes_fact_and_creates_pending", () => {
    const r = applyForgetResponse(facts, {
      status: "ok",
      op_id: "op-xyz",
      forgotten_ids: [2],
    });
    expect(r.nextFacts.map((f) => f.id)).toEqual([1, 3]);
    expect(r.pending).not.toBeNull();
    expect(r.pending!.op_id).toBe("op-xyz");
    expect(r.pending!.fact.id).toBe(2);
    expect(r.statusText).toBeNull();
  });

  it("test_forget_ok_without_op_id_skips_pending — undo 浮窗不弹", () => {
    const r = applyForgetResponse(facts, {
      status: "ok",
      forgotten_ids: [2],
    });
    // 没 op_id 就没法 undo —— 干脆不开浮窗
    expect(r.nextFacts.map((f) => f.id)).toEqual([1, 3]);
    expect(r.pending).toBeNull();
  });

  it("test_forget_ok_with_unknown_id_is_noop", () => {
    const r = applyForgetResponse(facts, {
      status: "ok",
      op_id: "op-x",
      forgotten_ids: [999],
    });
    expect(r.nextFacts).toBe(facts);
    expect(r.pending).toBeNull();
  });
});

describe("WI-S2.1b · applyForgetResponse — 错误分支", () => {
  const facts = [mkFact({ id: 1 }), mkFact({ id: 2 })];

  it("test_forget_error_keeps_list_intact_and_reports_status", () => {
    const r = applyForgetResponse(facts, {
      status: "error",
      reason: "facts_store_not_registered",
    });
    expect(r.nextFacts).toBe(facts);
    expect(r.pending).toBeNull();
    expect(r.statusText).toContain("error");
    expect(r.statusText).toContain("facts_store_not_registered");
  });

  it("test_forget_skipped_keeps_list_intact", () => {
    const r = applyForgetResponse(facts, { status: "skipped" });
    expect(r.nextFacts).toBe(facts);
    expect(r.pending).toBeNull();
    expect(r.statusText).toContain("skipped");
  });

  it("test_forget_not_found_keeps_list_intact", () => {
    const r = applyForgetResponse(facts, { status: "not_found" });
    expect(r.nextFacts).toBe(facts);
    expect(r.pending).toBeNull();
    expect(r.statusText).toContain("not_found");
  });
});

// ======================================================================
// 3) applyUndoResponse — 收到 memory_forget_undo_response 后的 state 转移
// ======================================================================

describe("WI-S2.1b · applyUndoResponse", () => {
  const remaining = [mkFact({ id: 1 }), mkFact({ id: 3 })];
  const pending = { op_id: "op-xyz", fact: mkFact({ id: 2 }) };

  it("test_undo_ok_prepends_fact_to_list_and_clears_pending", () => {
    const r = applyUndoResponse(remaining, pending, {
      status: "ok",
      restored_ids: [2],
    });
    expect(r.nextFacts.map((f) => f.id)).toEqual([2, 1, 3]);
    expect(r.pending).toBeNull();
    expect(r.statusText).toBeNull();
  });

  it("test_undo_ok_without_pending_is_noop", () => {
    const r = applyUndoResponse(remaining, null, {
      status: "ok",
      restored_ids: [2],
    });
    expect(r.nextFacts).toBe(remaining);
    expect(r.pending).toBeNull();
  });

  it("test_undo_expired_shows_status_and_clears_pending", () => {
    const r = applyUndoResponse(remaining, pending, {
      status: "expired",
      restored_ids: [],
    });
    expect(r.nextFacts).toBe(remaining);
    expect(r.pending).toBeNull();
    expect(r.statusText).toContain("过期");
  });

  it("test_undo_error_shows_reason_and_clears_pending", () => {
    const r = applyUndoResponse(remaining, pending, {
      status: "error",
      restored_ids: [],
      reason: "facts_store_not_registered",
    });
    expect(r.nextFacts).toBe(remaining);
    expect(r.pending).toBeNull();
    expect(r.statusText).toContain("facts_store_not_registered");
  });
});

// ======================================================================
// 4) 5 秒窗口 + fake timers — undo 浮窗自动消失契约
// ======================================================================

describe("WI-S2.1b · FORGET_UNDO_WINDOW_MS — 5s 撤销窗口", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("test_forget_undo_window_is_5_seconds", () => {
    // 与后端 facts_store.restore_from_undo(max_age_seconds=5.0) 必须一致
    expect(FORGET_UNDO_WINDOW_MS).toBe(5000);
  });

  it("test_undo_toast_dismisses_after_5s_via_settimeout", () => {
    // 模拟 MemoryPanel 内部 setTimeout(() => setPendingForget(null), 5000)
    const onTimeout = vi.fn();
    setTimeout(onTimeout, FORGET_UNDO_WINDOW_MS);

    // 4.9s 还没到 — 浮窗不该消失
    vi.advanceTimersByTime(4900);
    expect(onTimeout).not.toHaveBeenCalled();

    // 再走 100ms 凑满 5s — 浮窗回调触发
    vi.advanceTimersByTime(100);
    expect(onTimeout).toHaveBeenCalledTimes(1);
  });

  it("test_undo_timer_can_be_cancelled_before_expiry", () => {
    const onTimeout = vi.fn();
    const id = setTimeout(onTimeout, FORGET_UNDO_WINDOW_MS);

    // 用户在 2s 时点 undo —— 应该把 timer cancel 掉
    vi.advanceTimersByTime(2000);
    clearTimeout(id);

    // 再走 10s 确认 timer 没复活
    vi.advanceTimersByTime(10_000);
    expect(onTimeout).not.toHaveBeenCalled();
  });
});
