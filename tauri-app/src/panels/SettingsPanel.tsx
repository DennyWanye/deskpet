// P2-1-S8 stub SettingsPanel — minimal skeleton so S8 compiles on its own.
//
// S3 is the source-of-truth slice for the full SettingsPanel; this file
// only contains the "今日使用 (Daily Budget)" module + the `fetchDailyBudget`
// helper that talks to the backend `budget_status` WS request. When S3
// lands on master, that slice's richer panel supersedes this one; merge is
// expected to be a straight "replace" — both slices agree on the shape
// DailyBudgetStatus + the WS protocol name `budget_status`.
import { useCallback, useEffect, useRef, useState } from "react";
import type { ControlChannel } from "../ws/ControlChannel";
import type {
  DailyBudgetStatus,
  IncomingMessage,
} from "../types/messages";

/**
 * Send a `budget_status` request on the control channel and resolve with
 * the next `budget_status` reply (or reject after `timeoutMs`).
 *
 * S3's final panel will likely share / hoist this helper into a ws utility
 * module — keeping the contract (message name + payload shape) locked so
 * either slice's implementation is swappable.
 */
export async function fetchDailyBudget(
  channel: ControlChannel,
  timeoutMs = 3000,
): Promise<DailyBudgetStatus> {
  return new Promise<DailyBudgetStatus>((resolve, reject) => {
    const timer = setTimeout(() => {
      unsub();
      reject(new Error("budget_status timeout"));
    }, timeoutMs);
    const unsub = channel.onMessage((msg: IncomingMessage) => {
      if (msg.type === "budget_status") {
        clearTimeout(timer);
        unsub();
        resolve(msg.payload);
      }
    });
    channel.send({ type: "budget_status" });
  });
}

interface SettingsPanelProps {
  getChannel: () => ControlChannel | null;
}

export function SettingsPanel({ getChannel }: SettingsPanelProps) {
  const [status, setStatus] = useState<DailyBudgetStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const mounted = useRef(true);

  const refresh = useCallback(async () => {
    const ch = getChannel();
    if (!ch) {
      setError("control channel not connected");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const s = await fetchDailyBudget(ch);
      if (mounted.current) setStatus(s);
    } catch (e) {
      if (mounted.current) setError(String(e));
    } finally {
      if (mounted.current) setLoading(false);
    }
  }, [getChannel]);

  useEffect(() => {
    mounted.current = true;
    refresh();
    return () => {
      mounted.current = false;
    };
  }, [refresh]);

  return (
    <div className="settings-panel">
      <h3>今日使用</h3>
      {error && <div className="err">{error}</div>}
      {status ? (
        <div className="budget-status">
          <p>
            已用 {status.spent_today_cny.toFixed(3)} /
            {" "}
            {status.daily_budget_cny.toFixed(2)} 元
          </p>
          <p>剩余 {status.remaining_cny.toFixed(3)} 元</p>
          <p>使用率 {(status.percent_used * 100).toFixed(1)}%</p>
        </div>
      ) : (
        <p>{loading ? "加载中..." : "未获取"}</p>
      )}
      <button type="button" onClick={refresh} disabled={loading}>
        刷新
      </button>
    </div>
  );
}
