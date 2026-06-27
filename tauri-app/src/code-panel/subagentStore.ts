// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * subagent-concurrency-driver WI-3.4 — 子代理并发进度 store。
 *
 * 后端 SubagentScheduler 经 control WS 广播 `subagent_progress`
 * （queued → running → completed/failed，含 run_id/kind/task_id）。
 * ws.ts 的 dispatch 把它喂给本 store；SubagentProgressPanel 订阅渲染。
 * 独立 slice，不碰 sessionsStore 的消息派生（计划 R7：避免与 ChatRow 冲突）。
 */
import { create } from "zustand";

export interface SubagentRunView {
  run_id: string;
  task_id: string;
  kind: string;
  status: string; // queued | running | completed | failed
  /** 终态补充原因。后端排队中被取消时发 status="failed" + reason="cancelled"，
   * 前端据此把该行渲染成「🚫 已取消」而非「❌ 失败」（见 SubagentProgressPanel）。 */
  reason?: string;
  summary?: string;
  ts: number;
}

/**
 * WI-OC-2 — 调度器累计观测指标（背压/lane 维度）。这些是**调度器全局**计数，
 * 不是单个 run 的属性：后端在每条 `subagent_progress` 事件 payload 上附带最新
 * 快照，前端取最新值覆盖即可。旧后端不推 → 字段缺省 0（优雅降级，不崩）。
 */
export interface SubagentMetrics {
  /** 历史运行峰值（≤ global cap，验证背压真生效）。 */
  peak_concurrent: number;
  /** 累计入队总数（只增，吞吐口径）。 */
  total_queued: number;
  /** 累计拒绝/取消计数（排队或运行中被取消）。 */
  total_rejected: number;
}

const EMPTY_METRICS: SubagentMetrics = {
  peak_concurrent: 0,
  total_queued: 0,
  total_rejected: 0,
};

interface SubagentState {
  runs: Record<string, SubagentRunView>;
  /** WI-OC-2 调度器累计指标（全局，最新快照覆盖）。 */
  metrics: SubagentMetrics;
  upsert: (p: Partial<SubagentRunView> & { run_id: string }) => void;
  /** 用最新进度事件携带的累计快照覆盖（缺字段忽略，BC 降级）。 */
  setMetrics: (m: Partial<SubagentMetrics>) => void;
  clear: () => void;
  /** 清掉已终止（completed/failed）的，保留还在跑的。 */
  clearTerminal: () => void;
}

export const useSubagentStore = create<SubagentState>((set) => ({
  runs: {},
  metrics: EMPTY_METRICS,
  setMetrics: (m) =>
    set((s) => {
      // 只覆盖事件实际携带的数值字段（旧后端不推 → 不动旧值，保持 0 缺省）。
      const next: SubagentMetrics = { ...s.metrics };
      if (typeof m.peak_concurrent === "number")
        next.peak_concurrent = m.peak_concurrent;
      if (typeof m.total_queued === "number")
        next.total_queued = m.total_queued;
      if (typeof m.total_rejected === "number")
        next.total_rejected = m.total_rejected;
      return { metrics: next };
    }),
  upsert: (p) =>
    set((s) => {
      const prev =
        s.runs[p.run_id] ||
        ({
          run_id: p.run_id,
          task_id: "",
          kind: "",
          status: "queued",
          ts: 0,
        } as SubagentRunView);
      return { runs: { ...s.runs, [p.run_id]: { ...prev, ...p } } };
    }),
  clear: () => set({ runs: {}, metrics: EMPTY_METRICS }),
  clearTerminal: () =>
    set((s) => {
      const next: Record<string, SubagentRunView> = {};
      for (const [k, v] of Object.entries(s.runs)) {
        if (v.status === "queued" || v.status === "running") next[k] = v;
      }
      return { runs: next };
    }),
}));
