// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1
import { beforeEach, describe, expect, it } from "vitest";

import { useSubagentStore } from "./subagentStore";

describe("subagentStore (WI-3.4)", () => {
  beforeEach(() => useSubagentStore.getState().clear());

  it("upserts a run and merges partial updates", () => {
    const s = useSubagentStore.getState();
    s.upsert({ run_id: "r1", kind: "research", task_id: "t1", status: "queued", ts: 1 });
    s.upsert({ run_id: "r1", status: "running" });
    const run = useSubagentStore.getState().runs["r1"];
    expect(run.status).toBe("running");
    expect(run.kind).toBe("research"); // merged, not lost
    expect(run.task_id).toBe("t1");
  });

  it("tracks multiple concurrent runs", () => {
    const s = useSubagentStore.getState();
    s.upsert({ run_id: "a", kind: "doc", status: "running", ts: 1 });
    s.upsert({ run_id: "b", kind: "web", status: "queued", ts: 2 });
    expect(Object.keys(useSubagentStore.getState().runs)).toHaveLength(2);
  });

  it("clearTerminal keeps active runs, drops completed/failed", () => {
    const s = useSubagentStore.getState();
    s.upsert({ run_id: "a", status: "running", ts: 1 });
    s.upsert({ run_id: "b", status: "completed", ts: 2 });
    s.upsert({ run_id: "c", status: "failed", ts: 3 });
    useSubagentStore.getState().clearTerminal();
    const runs = useSubagentStore.getState().runs;
    expect(runs["a"]).toBeTruthy();
    expect(runs["b"]).toBeUndefined();
    expect(runs["c"]).toBeUndefined();
  });

  it("queued run flips to terminal when cancellation emits failed (2026-06-21 V5)", () => {
    // 回归：排队中被取消时后端补发 status=failed(reason=cancelled)，
    // 卡片该行须从 queued 归位到 failed（之前后端漏发 → 永远卡 queued）。
    const s = useSubagentStore.getState();
    s.upsert({ run_id: "vic", kind: "general", task_id: "vic", status: "queued", ts: 1 });
    expect(useSubagentStore.getState().runs["vic"].status).toBe("queued");
    s.upsert({ run_id: "vic", status: "failed", ts: 2 });
    const run = useSubagentStore.getState().runs["vic"];
    expect(run.status).toBe("failed"); // ❌ 归位，不再卡 queued
    expect(run.kind).toBe("general"); // 上下文不丢
    useSubagentStore.getState().clearTerminal();
    expect(useSubagentStore.getState().runs["vic"]).toBeUndefined(); // 终态可清
  });

  it("carries reason on terminal events (cancelled vs failed)", () => {
    const s = useSubagentStore.getState();
    s.upsert({ run_id: "vic", kind: "research", status: "queued", ts: 1 });
    s.upsert({ run_id: "vic", status: "failed", reason: "cancelled", ts: 2 });
    const run = useSubagentStore.getState().runs["vic"];
    expect(run.status).toBe("failed");
    expect(run.reason).toBe("cancelled"); // 用于前端区分「已取消」与「失败」
  });

  it("clear removes everything", () => {
    useSubagentStore.getState().upsert({ run_id: "a", status: "running", ts: 1 });
    useSubagentStore.getState().clear();
    expect(Object.keys(useSubagentStore.getState().runs)).toHaveLength(0);
  });

  // WI-OC-2 累计观测指标
  it("metrics default to zero (BC: 旧后端不推 → 不崩)", () => {
    const m = useSubagentStore.getState().metrics;
    expect(m).toEqual({ peak_concurrent: 0, total_queued: 0, total_rejected: 0 });
  });

  it("setMetrics overwrites only the carried numeric fields", () => {
    const s = useSubagentStore.getState();
    s.setMetrics({ peak_concurrent: 3, total_queued: 5, total_rejected: 1 });
    expect(useSubagentStore.getState().metrics).toEqual({
      peak_concurrent: 3,
      total_queued: 5,
      total_rejected: 1,
    });
    // 部分快照：undefined 字段不动旧值（旧后端可能不推某些 key）。
    s.setMetrics({ total_queued: 8 });
    const m = useSubagentStore.getState().metrics;
    expect(m.total_queued).toBe(8);
    expect(m.peak_concurrent).toBe(3); // 未携带 → 保持
    expect(m.total_rejected).toBe(1);
  });

  it("clear resets metrics too", () => {
    useSubagentStore.getState().setMetrics({ peak_concurrent: 4, total_queued: 9 });
    useSubagentStore.getState().clear();
    expect(useSubagentStore.getState().metrics).toEqual({
      peak_concurrent: 0,
      total_queued: 0,
      total_rejected: 0,
    });
  });
});
