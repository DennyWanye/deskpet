// SPDX-License-Identifier: BUSL-1.1

import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import type { ComponentProps } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useSessionsStore } from "../../stores/sessionsStore";
import { PPTOutlineCard } from "../PPTOutlineCard";

const sendMock = vi.hoisted(() => vi.fn());

vi.mock("../ws", () => ({
  codePanelWS: {
    send: sendMock,
  },
}));

const history = [
  {
    outline_id: "old-1",
    topic: "上季度 AI 产品复盘",
    created_at: "2026-06-20T08:00:00Z",
    sources_count: 7,
    status: "accepted",
  },
];

function renderCard(overrides: Partial<ComponentProps<typeof PPTOutlineCard>> = {}) {
  useSessionsStore.getState().ensure("default");
  useSessionsStore.getState().push_message("default", {
    role: "ppt_outline",
    ppt_outline_awaiting: true,
    outline_id: "outline-1",
    topic: "AI 产业趋势",
    outline_md: "## 封面\n\n- 市场变化\n- 产品机会",
    sources_count: 12,
    no_research: false,
    history,
  } as any);

  return render(
    <PPTOutlineCard
      outlineId="outline-1"
      topic="AI 产业趋势"
      outlineMd="## 封面\n\n- 市场变化\n- 产品机会"
      sourcesCount={12}
      noResearch={false}
      history={history}
      awaiting
      sessionId="default"
      {...overrides}
    />,
  );
}

function lastSent() {
  return sendMock.mock.calls.at(-1)?.[0];
}

beforeEach(() => {
  sendMock.mockClear();
  useSessionsStore.setState((s) => ({
    ...s,
    active_sid: "default",
    sessions: {
      default: {
        ...s.sessions.default,
        messages: [],
      },
    },
  }));
});

afterEach(() => cleanup());

describe("PPTOutlineCard", () => {
  it("renders outline markdown, source count, no-research warning, and no cost wording", () => {
    const { container } = renderCard({ noResearch: true, sourcesCount: 0 });

    expect(screen.getByText(/AI 产业趋势/)).toBeTruthy();
    expect(screen.getByText(/封面/)).toBeTruthy();
    expect(screen.getByText(/市场变化/)).toBeTruthy();
    expect(screen.getByText(/0 个调研来源/)).toBeTruthy();
    expect(screen.getByText(/本次未取得调研来源/)).toBeTruthy();
    expect(container.textContent).not.toMatch(/费用|价格|成本|收费/);
  });

  it("sends accept decision and clears local awaiting state", () => {
    renderCard();

    fireEvent.click(screen.getByRole("button", { name: /确认生成/ }));

    expect(lastSent()).toEqual({
      type: "ppt_outline_decision",
      payload: { outline_id: "outline-1", action: "accept" },
    });
    const msg = useSessionsStore.getState().sessions.default.messages[0];
    expect(msg.ppt_outline_awaiting).toBe(false);
  });

  it("opens modify textarea and sends feedback only on submit", () => {
    renderCard();

    fireEvent.click(screen.getByRole("button", { name: /修改/ }));
    expect(sendMock).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText("说说想改哪里"), {
      target: { value: "第 3 页改成竞品对比" },
    });
    fireEvent.click(screen.getByRole("button", { name: /提交修改/ }));

    expect(lastSent()).toEqual({
      type: "ppt_outline_decision",
      payload: {
        outline_id: "outline-1",
        action: "modify",
        feedback: "第 3 页改成竞品对比",
      },
    });
  });

  it("sends cancel decision", () => {
    renderCard();

    fireEvent.click(screen.getByRole("button", { name: /取消/ }));

    expect(lastSent()).toEqual({
      type: "ppt_outline_decision",
      payload: { outline_id: "outline-1", action: "cancel" },
    });
  });

  it("sends reuse decision from history", () => {
    renderCard();

    fireEvent.click(screen.getByRole("button", { name: /历史大纲/ }));
    const historyRegion = screen.getByTestId("ppt-outline-history");
    fireEvent.click(within(historyRegion).getByRole("button", { name: /上季度 AI 产品复盘/ }));

    expect(lastSent()).toEqual({
      type: "ppt_outline_decision",
      payload: {
        outline_id: "outline-1",
        action: "reuse",
        reuse_id: "old-1",
      },
    });
  });

  it("deduplicates awaiting outline cards by outline_id", () => {
    const store = useSessionsStore.getState();
    store.ensure("default");

    store.push_message("default", {
      role: "ppt_outline",
      ppt_outline_awaiting: true,
      outline_id: "dup-1",
      topic: "第一次广播",
      outline_md: "A",
      sources_count: 1,
      no_research: false,
      history: [],
    } as any);
    store.push_message("default", {
      role: "ppt_outline",
      ppt_outline_awaiting: true,
      outline_id: "dup-1",
      topic: "第二次广播",
      outline_md: "B",
      sources_count: 2,
      no_research: false,
      history: [],
    } as any);

    const outlines = useSessionsStore
      .getState()
      .sessions.default.messages.filter((m) => m.role === "ppt_outline");
    expect(outlines).toHaveLength(1);
    expect(outlines[0].topic).toBe("第二次广播");
  });

  it("resolved clears stale awaiting outline cards and set_messages preserves live awaiting cards", () => {
    const store = useSessionsStore.getState();
    store.ensure("default");
    store.push_message("default", {
      role: "ppt_outline",
      ppt_outline_awaiting: true,
      outline_id: "alive-1",
      topic: "待确认大纲",
      outline_md: "A",
      sources_count: 3,
      no_research: false,
      history: [],
    } as any);

    const liveCard = useSessionsStore.getState().sessions.default.messages[0];
    store.set_messages("default", [
      {
        id: "persisted-1",
        ts: 1,
        role: "assistant",
        text: "历史消息",
      },
    ]);
    expect(useSessionsStore.getState().sessions.default.messages).toEqual(
      expect.arrayContaining([expect.objectContaining({ id: liveCard.id, outline_id: "alive-1" })]),
    );

    store.resolve_ppt_outline("default", "alive-1");
    const msg = useSessionsStore
      .getState()
      .sessions.default.messages.find((m) => m.outline_id === "alive-1");
    expect(msg?.ppt_outline_awaiting).toBe(false);
  });
});
