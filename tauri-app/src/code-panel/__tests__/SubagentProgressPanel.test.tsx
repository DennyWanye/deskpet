// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * subagent-concurrency-driver WI-3.4 交互升级 vitest（2026-06-21）。
 * 覆盖：空时不渲染、运行中摘要、取消态(🚫 vs ❌)、折叠/展开、清除、dark 变体。
 */
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup, within } from "@testing-library/react";

import { SubagentProgressPanel } from "../SubagentProgressPanel";
import { useSubagentStore } from "../subagentStore";

afterEach(() => cleanup());
beforeEach(() => useSubagentStore.getState().clear());

describe("SubagentProgressPanel", () => {
  it("renders nothing when there are no runs", () => {
    const { container } = render(<SubagentProgressPanel />);
    expect(container.firstChild).toBeNull();
  });

  it("shows running summary N/M with active runs", () => {
    const s = useSubagentStore.getState();
    s.upsert({ run_id: "a", kind: "web", task_id: "bj", status: "running", ts: 1 });
    s.upsert({ run_id: "b", kind: "general", task_id: "poem", status: "queued", ts: 2 });
    render(<SubagentProgressPanel />);
    expect(screen.getByText(/运行中 2\/2/)).toBeTruthy();
    // 明细行(默认展开): 2 行
    expect(screen.getAllByTestId("subagent-run-row")).toHaveLength(2);
  });

  it("distinguishes cancelled (failed+reason=cancelled) from genuine failure", () => {
    const s = useSubagentStore.getState();
    s.upsert({ run_id: "c", kind: "research", task_id: "cx", status: "failed", reason: "cancelled", ts: 1 });
    s.upsert({ run_id: "f", kind: "research", task_id: "fx", status: "failed", ts: 2 });
    render(<SubagentProgressPanel />);
    const rows = screen.getAllByTestId("subagent-run-row");
    const phases = rows.map((r) => r.getAttribute("data-phase"));
    expect(phases).toContain("cancelled");
    expect(phases).toContain("failed");
    // 取消行显示「已取消」，失败行显示「失败」
    const cancelledRow = rows.find((r) => r.getAttribute("data-phase") === "cancelled")!;
    expect(within(cancelledRow).getByText("已取消")).toBeTruthy();
  });

  it("header toggles collapse/expand", () => {
    const s = useSubagentStore.getState();
    s.upsert({ run_id: "a", kind: "web", task_id: "bj", status: "running", ts: 1 });
    render(<SubagentProgressPanel />);
    expect(screen.getAllByTestId("subagent-run-row")).toHaveLength(1);
    fireEvent.click(screen.getByTestId("subagent-progress-header"));
    expect(screen.queryAllByTestId("subagent-run-row")).toHaveLength(0); // 收起
    fireEvent.click(screen.getByTestId("subagent-progress-header"));
    expect(screen.getAllByTestId("subagent-run-row")).toHaveLength(1); // 再展开
  });

  it("shows clear button only when all terminal; clears terminal runs", () => {
    const s = useSubagentStore.getState();
    s.upsert({ run_id: "a", kind: "web", task_id: "bj", status: "completed", ts: 1 });
    render(<SubagentProgressPanel />);
    expect(screen.getByText(/全部完成 \(1\)/)).toBeTruthy();
    fireEvent.click(screen.getByTestId("subagent-progress-clear"));
    expect(useSubagentStore.getState().runs["a"]).toBeUndefined();
  });

  it("renders sane elapsed for running rows (backend ts is epoch seconds)", () => {
    // 回归：后端 ts=epoch 秒，若按毫秒算会得到天文数字。归一后应是 ~Ns 的小数。
    const s = useSubagentStore.getState();
    s.upsert({
      run_id: "a",
      kind: "research",
      task_id: "tx",
      status: "running",
      ts: Math.floor(Date.now() / 1000) - 4, // 4 秒前（秒级 epoch）
    });
    render(<SubagentProgressPanel />);
    const row = screen.getByTestId("subagent-run-row");
    const txt = row.textContent || "";
    const m = txt.match(/(\d+)s\b/);
    expect(m).toBeTruthy(); // 形如 "4s"
    expect(Number(m![1])).toBeLessThan(60); // 不是天文数字
  });

  it("applies dark variant attribute", () => {
    useSubagentStore.getState().upsert({ run_id: "a", kind: "web", status: "running", ts: 1 });
    render(<SubagentProgressPanel variant="dark" />);
    expect(screen.getByTestId("subagent-progress-panel").getAttribute("data-variant")).toBe("dark");
  });

  // WI-OC-2 累计观测汇总
  it("does not render metrics summary when all cumulative counters are zero (BC)", () => {
    useSubagentStore.getState().upsert({ run_id: "a", kind: "web", status: "running", ts: 1 });
    render(<SubagentProgressPanel />);
    // 旧后端不推累计字段 → 全 0 → 不渲染汇总（优雅降级）。
    expect(screen.queryByTestId("subagent-metrics-summary")).toBeNull();
  });

  it("renders cumulative metrics (peak / queued / rejected) when present", () => {
    const s = useSubagentStore.getState();
    s.upsert({ run_id: "a", kind: "web", task_id: "bj", status: "completed", ts: 1 });
    s.setMetrics({ peak_concurrent: 3, total_queued: 7, total_rejected: 2 });
    render(<SubagentProgressPanel />);
    const summary = screen.getByTestId("subagent-metrics-summary");
    expect(within(summary).getByTestId("subagent-metric-peak").textContent).toMatch(/峰值\s*3/);
    expect(within(summary).getByTestId("subagent-metric-queued").textContent).toMatch(/累计入队\s*7/);
    expect(within(summary).getByTestId("subagent-metric-rejected").textContent).toMatch(/拒绝\s*2/);
  });
});
