/**
 * P2-1-S3: SettingsPanel —— 云端账号 / 路由策略 / 今日使用（占位）。
 *
 * Three sections, all controlled-input. Save flow:
 *   1. If user typed a new apiKey, invoke Rust `set_cloud_api_key` (keyring).
 *   2. Persist strategy / budget to backend config: deferred — S6 owns the
 *      backend-side strategy switching, S8 owns the daily-budget ledger.
 *      For S3 we just keep them in component state so the UI is honest
 *      about what works right now.
 *
 * 测试连接 path uses the control WS (already authenticated via shared
 * secret) so the apiKey never touches an HTTP endpoint and never lands
 * in a network log.
 *
 * The 今日使用 section reads from `fetchDailyBudget`, which round-trips
 * through the control WS to the BillingLedger (S8). The DailyBudgetStatus
 * contract (snake_case fields) is frozen in types/messages.ts.
 */
import { useCallback, useEffect, useState } from "react";

import { Icon } from "./Icon";
import { EmbedderStatusCard } from "./EmbedderStatusCard";
import { ModelContextCard } from "./ModelContextCard";
import { SettingsProviders } from "./SettingsProviders";
import { HiyoriMotionTuner } from "./HiyoriMotionTuner";
import type {
  DailyBudgetStatus,
  IncomingMessage,
} from "../types/messages";
import type { ControlChannel } from "../ws/ControlChannel";

interface SettingsPanelProps {
  open: boolean;
  onClose: () => void;
  /** Accessor so the component can both `send` and subscribe without
   * recreating subscriptions every parent render. */
  getChannel: () => ControlChannel | null;
  /** The most recent incoming control message — we narrow to our reply
   * type inside an effect. Piggybacking the existing App-level state
   * avoids an extra onMessage listener that'd need manual teardown. */
  lastMessage: IncomingMessage | null;
  secret: string;
  onConfigChanged?: () => void;
}

// ---------------------------------------------------------------------------
// P5-S2 Phase 5.3 — auto-resume toggle helper.
//
// Spec contract (frontend-ipc-surface/auto-resume-events.md):
//   { type: "settings_update",
//     payload: { supervisor: { auto_resume_enabled: bool } } }
//
// Exported as a pure function so SettingsToggle.test.tsx can verify the wire
// format without instantiating React. The component below uses it.
// ---------------------------------------------------------------------------
export interface AutoResumeSettingsMessage {
  type: "settings_update";
  payload: { supervisor: { auto_resume_enabled: boolean } };
}

export function buildAutoResumeSettingsMessage(
  enabled: boolean,
): AutoResumeSettingsMessage {
  return {
    type: "settings_update",
    payload: { supervisor: { auto_resume_enabled: enabled } },
  };
}

/**
 * Send a `budget_status` request on the control channel and resolve with the
 * next `budget_status` reply (or reject after `timeoutMs`).
 *
 * P2-1-S8: replaced S3's hardcoded stub with the real control-WS roundtrip.
 * The DailyBudgetStatus contract (snake_case fields) is the cross-slice
 * import point locked in spec §1.3.
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

export function SettingsPanel({
  open,
  onClose,
  getChannel,
  lastMessage,
}: SettingsPanelProps) {
  // ----- Daily budget section ------------------------------------------------
  const [budget, setBudget] = useState<DailyBudgetStatus | null>(null);
  const [budgetError, setBudgetError] = useState<string | null>(null);

  // Refresh daily budget every time the panel opens.
  useEffect(() => {
    if (!open) return;
    let cancelled = false;

    void (async () => {
      try {
        const ch = getChannel();
        if (!ch) throw new Error("控制通道未连接");
        const b = await fetchDailyBudget(ch);
        if (!cancelled) {
          setBudget(b);
          setBudgetError(null);
        }
      } catch (e) {
        if (!cancelled) {
          setBudget(null);
          setBudgetError(String(e));
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [open, getChannel]);

  const handleRefreshBudget = useCallback(async () => {
    try {
      const ch = getChannel();
      if (!ch) throw new Error("控制通道未连接");
      const b = await fetchDailyBudget(ch);
      setBudget(b);
      setBudgetError(null);
    } catch (e) {
      setBudget(null);
      setBudgetError(String(e));
    }
  }, [getChannel]);

  if (!open) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="设置"
      style={overlayStyle}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div style={panelStyle} onClick={(e) => e.stopPropagation()}>
        <header style={headerStyle}>
          <span style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <span
              style={{
                display: "inline-flex",
                alignItems: "center",
                justifyContent: "center",
                width: 32,
                height: 32,
                borderRadius: 10,
                background: "linear-gradient(180deg,#eff6ff,#dbeafe)",
                color: "#2563eb",
              }}
            >
              <Icon name="settings" size={18} />
            </span>
            <h2 style={{ margin: 0, fontSize: 17, fontWeight: 700, letterSpacing: 0.2 }}>
              设置
            </h2>
          </span>
          <button
            type="button"
            onClick={onClose}
            aria-label="关闭设置"
            style={closeBtnStyle}
          >
            <Icon name="close" size={16} />
          </button>
        </header>

        {/* P5-S2 multi-provider-management: legacy single-provider LLM
            configuration section was removed. All provider config now lives
            under the "LLM Providers" section below (drag-drop reorder,
            multiple endpoints, per-card pinning). */}

        {/* ================ 今日使用 ================ */}
        <section style={sectionStyle}>
          <h3 style={h3Style}>今日使用</h3>
          {budgetError && (
            <div role="status" style={{ ...statusStyle, color: "#b91c1c" }}>
              {budgetError}
            </div>
          )}
          {budget && (
            <div style={{ display: "grid", gap: 6, fontSize: 13 }}>
              <div>
                已消耗 ¥{budget.spent_today_cny.toFixed(2)} /
                ¥{budget.daily_budget_cny.toFixed(2)}
              </div>
              <div>剩余 ¥{budget.remaining_cny.toFixed(2)}</div>
              <div>
                使用率 {budget.percent_used.toFixed(1)}%
              </div>
              <div
                style={{
                  height: 6,
                  background: "#e5e7eb",
                  borderRadius: 3,
                  overflow: "hidden",
                }}
              >
                <div
                  style={{
                    width: `${Math.min(100, Math.max(0, budget.percent_used))}%`,
                    height: "100%",
                    background:
                      budget.percent_used >= 90 ? "#dc2626" : "#10b981",
                    transition: "width 0.2s",
                  }}
                />
              </div>
            </div>
          )}
          <div style={btnRowStyle}>
            <button type="button" onClick={handleRefreshBudget} style={btnStyle}>
              刷新
            </button>
          </div>
          <p style={hintStyle}>
            数据来自 BillingLedger（S8），按 Asia/Shanghai 时区按日累计。
          </p>
        </section>

        {/* ================ LLM Providers (P5-S2 Phase 4) ================ */}
        <section style={sectionStyle}>
          <h3 style={h3Style}>LLM Providers</h3>
          <SettingsProviders
            getChannel={getChannel}
            lastMessage={lastMessage}
          />
        </section>

        {/* ================ 模型状态 (P4-S16) ================ */}
        <section style={sectionStyle}>
          <h3 style={h3Style}>模型状态</h3>
          <EmbedderStatusCard getChannel={getChannel} />
          {/* Phase 1.1.6（context-1m-rearch）：per-model 上下文窗口卡片 */}
          <ModelContextCard getChannel={getChannel} />
        </section>

        {/* ================ 自动模式 (P4-S21 #13) ================ */}
        <section style={sectionStyle}>
          <h3 style={h3Style}>权限</h3>
          <AutoModeToggle getChannel={getChannel} />
        </section>

        {/* ================ 桌宠 supervisor (P5-S1) ================ */}
        <section style={sectionStyle}>
          <h3 style={h3Style}>桌宠 supervisor（P5-S1）</h3>
          <SupervisorToggleSection getChannel={getChannel} />
          <AutoResumeToggleSection getChannel={getChannel} />
          <HiyoriMotionTuner />
        </section>

        {/* ================ 数据目录 (2026-05-21) ================ */}
        <DataDirSection />

        {/* ================ 危险区 (P3-S9) ================ */}
        <DangerZoneSection />

        {/* P5-S2: footer 保存按钮删除 —— SettingsProviders 直接发 ws
            消息保存每条改动，AutoMode/Supervisor/Hiyori 各自有自己的 toggle，
            不再需要全局"保存"。关闭走顶部 X 按钮。 */}
      </div>
    </div>
  );
}

// ----------------------------------------------------------------------
// P4-S21 #13 — Auto mode toggle.
//
// When ON, the backend's PermissionGate auto-allows every tool category
// (read/write/shell/network/...). Useful for voice-driven sessions or
// power users who don't want to click through each PermissionPopup.
// State is per-process: turning OFF the deskpet (or restarting backend)
// resets to disabled. Default OFF — has to be explicitly opted in.
// ----------------------------------------------------------------------
function AutoModeToggle({
  getChannel,
}: { getChannel: () => ControlChannel | null }) {
  const [enabled, setEnabled] = useState<boolean>(() => {
    try { return localStorage.getItem("deskpet.auto_mode") === "true"; }
    catch { return false; }
  });
  const [pending, setPending] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // Push current state to backend whenever the channel becomes available.
  // Channel may be null on first render (still connecting); the effect
  // re-runs when getChannel returns a live channel.
  useEffect(() => {
    const ch = getChannel();
    if (!ch) return;
    try { ch.send({ type: "permission_auto_mode_set", payload: { enabled } }); }
    catch { /* will retry on next toggle */ }
  }, [getChannel, enabled]);

  const onToggle = useCallback(() => {
    setErr(null);
    setPending(true);
    const next = !enabled;
    try {
      const ch = getChannel();
      if (!ch) throw new Error("控制通道未连接");
      ch.send({ type: "permission_auto_mode_set", payload: { enabled: next } });
      try { localStorage.setItem("deskpet.auto_mode", String(next)); }
      catch { /* localStorage full / disabled — non-fatal */ }
      setEnabled(next);
    } catch (e) {
      setErr(String(e));
    } finally {
      setPending(false);
    }
  }, [enabled, getChannel]);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <label
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          fontSize: 13,
          cursor: pending ? "default" : "pointer",
          opacity: pending ? 0.6 : 1,
        }}
      >
        <input
          type="checkbox"
          checked={enabled}
          onChange={onToggle}
          disabled={pending}
        />
        <span>
          自动模式（高级）：所有工具自动允许，不弹确认窗口
        </span>
      </label>
      <p style={{ fontSize: 11, color: "#64748b", margin: 0, lineHeight: 1.5 }}>
        关闭时（默认），DeskPet 在执行写文件、运行 shell 等操作前会弹出确认。
        语音模式下还会同时朗读"请点击允许"，提醒你回到屏幕。开启此项 = 跳过
        所有确认（仅在你完全信任 LLM 配置时使用）。
      </p>
      {err && <span style={{ color: "#b91c1c", fontSize: 11 }}>{err}</span>}
    </div>
  );
}

// ----------------------------------------------------------------------
// P5-S1 — supervisor toggle.
//
// Backend default is on. Toggling here sends `supervisor_toggle` ws msg
// (handled in main.py). The toggle persists locally (localStorage) so
// the user's preference survives a refresh; the backend's runtime flag
// resyncs from this on connect.
// ----------------------------------------------------------------------
function SupervisorToggleSection({
  getChannel,
}: { getChannel: () => ControlChannel | null }) {
  const [enabled, setEnabled] = useState<boolean>(() => {
    try {
      const v = localStorage.getItem("deskpet.supervisor.enabled");
      return v === null ? true : v !== "false"; // default ON
    } catch {
      return true;
    }
  });
  const [pending, setPending] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // Sync state to backend whenever channel becomes available.
  useEffect(() => {
    const ch = getChannel();
    if (!ch) return;
    try {
      ch.send({ type: "supervisor_toggle", payload: { enabled } });
    } catch {
      /* retry on next toggle */
    }
  }, [getChannel, enabled]);

  const onToggle = useCallback(() => {
    setErr(null);
    setPending(true);
    const next = !enabled;
    try {
      const ch = getChannel();
      if (!ch) throw new Error("控制通道未连接");
      ch.send({ type: "supervisor_toggle", payload: { enabled: next } });
      try {
        localStorage.setItem("deskpet.supervisor.enabled", String(next));
      } catch {
        /* non-fatal */
      }
      setEnabled(next);
    } catch (e) {
      setErr(String(e));
    } finally {
      setPending(false);
    }
  }, [enabled, getChannel]);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <label
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          fontSize: 13,
          cursor: pending ? "default" : "pointer",
          opacity: pending ? 0.6 : 1,
        }}
      >
        <input
          type="checkbox"
          checked={enabled}
          onChange={onToggle}
          disabled={pending}
        />
        <span>启用桌宠 supervisor（自动监督卡住的 Code 模式任务）</span>
      </label>
      <p style={{ fontSize: 11, color: "#64748b", margin: 0, lineHeight: 1.5 }}>
        每 60 秒扫描一次活动 session，对 15 分钟无进展或报错的任务用 LLM
        自检判断是否需要干预。桌宠用气泡 + 表情提示你最危险的 session。
        关闭后所有 supervisor 行为停止；运行时切换需要重启 backend 完全生效。
      </p>
      {err && <span style={{ color: "#b91c1c", fontSize: 11 }}>{err}</span>}
    </div>
  );
}

// ----------------------------------------------------------------------
// P5-S2 Phase 5.3 — auto-resume toggle.
//
// 控制 backend 的 AutoResumeOrchestrator 是否在 chat 失败时自动重试。
// 默认 ON（spec'd default in config.toml [supervisor]）。toggle 改变时发
// `settings_update` 给 backend；backend 持久化到 config.toml。本地
// localStorage 也存一份以便面板刷新时记得用户偏好。
// ----------------------------------------------------------------------
function AutoResumeToggleSection({
  getChannel,
}: { getChannel: () => ControlChannel | null }) {
  const [enabled, setEnabled] = useState<boolean>(() => {
    try {
      const v = localStorage.getItem("deskpet.supervisor.auto_resume_enabled");
      return v === null ? true : v !== "false"; // default ON
    } catch {
      return true;
    }
  });
  const [pending, setPending] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // Sync to backend whenever channel becomes available.
  useEffect(() => {
    const ch = getChannel();
    if (!ch) return;
    try {
      ch.send(buildAutoResumeSettingsMessage(enabled));
    } catch {
      /* retry on next toggle */
    }
  }, [getChannel, enabled]);

  const onToggle = useCallback(() => {
    setErr(null);
    setPending(true);
    const next = !enabled;
    try {
      const ch = getChannel();
      if (!ch) throw new Error("控制通道未连接");
      ch.send(buildAutoResumeSettingsMessage(next));
      try {
        localStorage.setItem(
          "deskpet.supervisor.auto_resume_enabled",
          String(next),
        );
      } catch {
        /* non-fatal */
      }
      setEnabled(next);
    } catch (e) {
      setErr(String(e));
    } finally {
      setPending(false);
    }
  }, [enabled, getChannel]);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 10 }}>
      <label
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          fontSize: 13,
          cursor: pending ? "default" : "pointer",
          opacity: pending ? 0.6 : 1,
        }}
      >
        <input
          type="checkbox"
          checked={enabled}
          onChange={onToggle}
          disabled={pending}
          data-testid="auto-resume-toggle"
        />
        <span>自动自愈失败任务（agent 撞 max_iterations / 工具用法错时自动重试 ≤2 次）</span>
      </label>
      <p style={{ fontSize: 11, color: "#64748b", margin: 0, lineHeight: 1.5 }}>
        关闭后 agent 失败时直接弹 supervisor 提示窗口让你决定。开启时
        AutoResumeOrchestrator 会带着 hint 重跑 1-2 次，跑通就静默；都跑不通才弹窗。
      </p>
      {err && <span style={{ color: "#b91c1c", fontSize: 11 }}>{err}</span>}
    </div>
  );
}

// ----------------------------------------------------------------------
// P3-S9 — Danger Zone: 完全卸载（清除用户数据）.
//
// `完全卸载` wipes %AppData%\deskpet\ (config / SQLite / logs). A
// second opt-in checkbox additionally wipes %LocalAppData%\deskpet\
// models — that's ~9 GB so we require explicit consent.
//
// Two-step confirm via window.confirm keeps the UI trivial while still
// preventing single-click destruction.
// ----------------------------------------------------------------------
function DangerZoneSection() {
  const [includeModels, setIncludeModels] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const handlePurge = useCallback(async () => {
    setErr(null);
    const scope = includeModels
      ? "用户数据 + 本地模型缓存（%LocalAppData%\\deskpet\\models）"
      : "用户数据（配置 / 数据库 / 日志）";
    const confirmed = window.confirm(
      `即将删除：${scope}\n\n` +
        "这将清除所有聊天历史、云端账号设置、预算记录和日志，无法撤销。\n" +
        "删除完成后 DeskPet 将自动退出。\n\n确认继续？",
    );
    if (!confirmed) return;

    setBusy(true);
    try {
      const core = await import("@tauri-apps/api/core");
      await core.invoke("purge_user_data", { includeModels });
      // Rust will exit the app shortly; nothing else to do here.
    } catch (e) {
      setErr(typeof e === "string" ? e : (e as Error)?.message ?? String(e));
      setBusy(false);
    }
  }, [includeModels]);

  return (
    <section style={{ ...sectionStyle, borderTop: "1px solid #fecaca" }}>
      <h3 style={{ ...h3Style, color: "#b91c1c" }}>危险区</h3>
      <p style={hintStyle}>
        "完全卸载" 会清除 <code>%AppData%\deskpet\</code> 下的配置、SQLite
        数据库与日志。卸载安装包本身仍需在「应用和功能」里进行。
      </p>
      <label
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          fontSize: 12,
        }}
      >
        <input
          type="checkbox"
          checked={includeModels}
          onChange={(e) => setIncludeModels(e.target.checked)}
          data-testid="purge-include-models"
        />
        <span>
          同时删除 <code>%LocalAppData%\deskpet\models</code>（模型缓存 ~9 GB）
        </span>
      </label>
      {err && (
        <div role="alert" style={{ ...statusStyle, color: "#b91c1c" }}>
          {err}
        </div>
      )}
      <div style={btnRowStyle}>
        <button
          type="button"
          data-testid="purge-user-data"
          onClick={handlePurge}
          disabled={busy}
          style={{
            ...btnStyle,
            background: "#b91c1c",
            color: "white",
            borderColor: "#b91c1c",
          }}
        >
          {busy ? "删除中…" : "完全卸载（清除用户数据）"}
        </button>
      </div>
    </section>
  );
}

// ----------------------------------------------------------------------
// 2026-05-21 — 数据目录设置.
//
// Lets the user relocate %AppData%\deskpet to a roomier drive
// without leaving the app. Persistence is via the user-level
// `DESKPET_USER_DATA` env var, which `paths::user_data_dir()` reads
// on every startup (see src-tauri/src/paths.rs §57).
//
// Why a separate section vs. nesting under DangerZone: the relocate
// flow is reversible (you can always set the var back) so it doesn't
// belong with the truly destructive purge action. We do keep a
// soft confirmation before kicking off the file copy though, because
// "I clicked the wrong button and now my chat history moved" is a
// crap user experience even if it's not technically dangerous.
// ----------------------------------------------------------------------
interface DataDirSetting {
  effective: string;
  default: string | null;
  env_override: string | null;
  effective_exists: boolean;
  effective_size_bytes: number;
}

function formatMb(bytes: number): string {
  const mb = bytes / (1024 * 1024);
  if (mb >= 1024) return `${(mb / 1024).toFixed(2)} GB`;
  return `${mb.toFixed(1)} MB`;
}

function DataDirSection() {
  const [setting, setSetting] = useState<DataDirSetting | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [newPath, setNewPath] = useState("");
  const [moveData, setMoveData] = useState(true);
  const [busy, setBusy] = useState(false);
  const [opMsg, setOpMsg] = useState<string | null>(null);
  const [opErr, setOpErr] = useState<string | null>(null);

  const reload = useCallback(async () => {
    setLoadErr(null);
    try {
      const core = await import("@tauri-apps/api/core");
      const s = await core.invoke<DataDirSetting>("get_data_dir_setting");
      setSetting(s);
      // Pre-fill the input with the current effective path so the
      // user can edit-in-place rather than retype from scratch.
      if (!newPath) setNewPath(s.effective);
    } catch (e) {
      setLoadErr(e instanceof Error ? e.message : String(e));
    }
    // Intentionally not depending on newPath — we only want this to
    // pre-fill on the very first load.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    reload();
  }, [reload]);

  const handlePickDir = useCallback(async () => {
    setOpErr(null);
    try {
      const core = await import("@tauri-apps/api/core");
      const picked = await core.invoke<string | null>("open_directory_dialog");
      if (picked) setNewPath(picked);
    } catch (e) {
      setOpErr(e instanceof Error ? e.message : String(e));
    }
  }, []);

  const handleApply = useCallback(async () => {
    setOpErr(null);
    setOpMsg(null);
    if (!setting) return;
    const target = newPath.trim();
    if (!target) {
      setOpErr("请先选择或输入新路径");
      return;
    }
    if (target === setting.effective) {
      setOpErr("新路径与当前路径相同，无需修改");
      return;
    }
    const sizeStr = formatMb(setting.effective_size_bytes);
    const confirmed = window.confirm(
      `即将把数据目录切换为：\n${target}\n\n` +
        (moveData
          ? `并将现有数据（约 ${sizeStr}）从\n${setting.effective}\n复制并删除原位置文件。\n\n`
          : "（不移动现有数据 — 旧目录保留，新目录从空开始）\n\n") +
        "DeskPet 需要重启才能完全生效。继续？",
    );
    if (!confirmed) return;

    setBusy(true);
    try {
      const core = await import("@tauri-apps/api/core");
      // Set the env var first so even if the move fails halfway,
      // the next launch sees the new target and at worst boots empty.
      const updated = await core.invoke<DataDirSetting>(
        "set_data_dir_preference",
        { newPath: target },
      );
      setSetting(updated);

      if (moveData && setting.effective_exists && setting.effective !== target) {
        const moved = await core.invoke<number>("move_data_dir_contents", {
          src: setting.effective,
          dst: target,
        });
        setOpMsg(
          `已保存并移动 ${formatMb(moved)} 数据。请重启 DeskPet 让所有进程读到新路径。`,
        );
      } else {
        setOpMsg(
          "已保存。下次启动时 DeskPet 会从新路径读写。",
        );
      }
    } catch (e) {
      setOpErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [setting, newPath, moveData]);

  const handleReset = useCallback(async () => {
    if (!setting) return;
    const confirmed = window.confirm(
      "将清除 DESKPET_USER_DATA 环境变量，下次启动 DeskPet 会回到默认目录 " +
        "（%AppData%\\deskpet）。\n\n" +
        "注意：现有数据不会被自动搬回去 —— 你需要手动移动，或先在上方填入默认路径并勾选「移动」。\n\n继续？",
    );
    if (!confirmed) return;
    setBusy(true);
    setOpErr(null);
    setOpMsg(null);
    try {
      const core = await import("@tauri-apps/api/core");
      // Setting to the default path effectively "resets" — we just
      // write the same value `%AppData%\deskpet` would expand to,
      // so the env var stays consistent. Simpler than adding a
      // dedicated "clear env var" command.
      if (setting.default) {
        const updated = await core.invoke<DataDirSetting>(
          "set_data_dir_preference",
          { newPath: setting.default },
        );
        setSetting(updated);
        setNewPath(updated.effective);
        setOpMsg("已切回默认目录设置。请重启 DeskPet 生效。");
      } else {
        setOpErr("无法识别默认目录（%AppData% 未设置？）");
      }
    } catch (e) {
      setOpErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [setting]);

  if (loadErr) {
    return (
      <section style={sectionStyle}>
        <h3 style={h3Style}>数据目录</h3>
        <div style={{ ...statusStyle, color: "#b91c1c" }}>
          加载失败：{loadErr}
        </div>
      </section>
    );
  }
  if (!setting) {
    return (
      <section style={sectionStyle}>
        <h3 style={h3Style}>数据目录</h3>
        <div style={statusStyle}>加载中…</div>
      </section>
    );
  }

  return (
    <section style={sectionStyle}>
      <h3 style={h3Style}>数据目录</h3>
      <p style={hintStyle}>
        DeskPet 的聊天历史、配置、SQLite 数据库和设备 ID 都保存在这里。
        如果 C 盘空间紧张，可以搬到其他磁盘。
      </p>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "auto 1fr",
          columnGap: 10,
          rowGap: 4,
          fontSize: 12,
        }}
      >
        <div style={{ color: "#6b7280" }}>当前生效</div>
        <div style={{ fontFamily: "monospace" }}>
          {setting.effective}{" "}
          <span style={{ color: "#6b7280" }}>
            ({formatMb(setting.effective_size_bytes)})
          </span>
        </div>

        <div style={{ color: "#6b7280" }}>环境变量</div>
        <div style={{ fontFamily: "monospace" }}>
          {setting.env_override ?? (
            <span style={{ color: "#9ca3af" }}>(未设置 — 使用默认路径)</span>
          )}
        </div>

        <div style={{ color: "#6b7280" }}>默认路径</div>
        <div style={{ fontFamily: "monospace", color: "#6b7280" }}>
          {setting.default ?? "—"}
        </div>
      </div>

      <div style={{ display: "grid", gap: 6, marginTop: 8 }}>
        <label style={{ fontSize: 12, color: "#374151" }}>新路径</label>
        <div style={{ display: "flex", gap: 6 }}>
          <input
            type="text"
            value={newPath}
            onChange={(e) => setNewPath(e.target.value)}
            disabled={busy}
            placeholder="F:\deskpet\data"
            style={{
              flex: 1,
              padding: "5px 8px",
              borderRadius: 4,
              border: "1px solid #d1d5db",
              fontSize: 12,
              fontFamily: "monospace",
              outline: "none",
            }}
            data-testid="data-dir-input"
          />
          <button
            type="button"
            onClick={handlePickDir}
            disabled={busy}
            style={btnStyle}
          >
            浏览…
          </button>
        </div>
        <label
          style={{
            display: "flex",
            gap: 6,
            alignItems: "center",
            fontSize: 12,
            color: "#374151",
          }}
        >
          <input
            type="checkbox"
            checked={moveData}
            onChange={(e) => setMoveData(e.target.checked)}
            disabled={busy}
          />
          <span>同时移动现有数据到新位置（推荐）</span>
        </label>
      </div>

      <div style={{ display: "flex", gap: 8, marginTop: 4, flexWrap: "wrap" }}>
        <button
          type="button"
          onClick={handleApply}
          disabled={busy}
          style={{
            ...btnStyle,
            background: "#2563eb",
            color: "white",
            borderColor: "#2563eb",
          }}
          data-testid="data-dir-apply"
        >
          {busy ? "处理中…" : "应用"}
        </button>
        <button
          type="button"
          onClick={handleReset}
          disabled={busy}
          style={btnStyle}
          data-testid="data-dir-reset"
        >
          恢复默认
        </button>
        <button
          type="button"
          onClick={reload}
          disabled={busy}
          style={btnStyle}
        >
          刷新
        </button>
      </div>

      {opMsg && (
        <div
          role="status"
          style={{
            ...statusStyle,
            background: "#ecfdf5",
            border: "1px solid #a7f3d0",
            color: "#065f46",
          }}
        >
          {opMsg}
        </div>
      )}
      {opErr && (
        <div role="alert" style={{ ...statusStyle, color: "#b91c1c" }}>
          {opErr}
        </div>
      )}
    </section>
  );
}

// ---- inline styles (kept local so the panel has no CSS imports to wire) ----

const overlayStyle: React.CSSProperties = {
  position: "fixed",
  inset: 0,
  background: "rgba(8,11,20,0.55)",
  backdropFilter: "blur(6px)",
  WebkitBackdropFilter: "blur(6px)",
  display: "grid",
  placeItems: "center",
  padding: 12,
  zIndex: 1000,
  animation: "bp-fade-in 200ms cubic-bezier(0.16,1,0.3,1)",
};

const panelStyle: React.CSSProperties = {
  background: "#ffffff",
  padding: "0 22px 22px",
  borderRadius: 18,
  width: "min(94vw, 540px)",
  boxSizing: "border-box",
  maxHeight: "92vh",
  overflowY: "auto",
  overflowX: "hidden",
  color: "#0f172a",
  border: "1px solid rgba(255,255,255,0.6)",
  boxShadow:
    "0 32px 70px rgba(8,11,20,0.45), 0 4px 14px rgba(8,11,20,0.18)",
  fontFamily:
    '"Inter","PingFang SC","Microsoft YaHei UI",sans-serif',
  animation: "bp-pop-in 260ms cubic-bezier(0.16,1,0.3,1)",
};

const headerStyle: React.CSSProperties = {
  display: "flex",
  justifyContent: "space-between",
  alignItems: "center",
  position: "sticky",
  top: 0,
  zIndex: 2,
  margin: "0 -22px 8px",
  padding: "16px 22px",
  background: "rgba(255,255,255,0.92)",
  backdropFilter: "blur(8px)",
  WebkitBackdropFilter: "blur(8px)",
  borderBottom: "1px solid #eef1f6",
};

const closeBtnStyle: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  width: 30,
  height: 30,
  background: "#f1f5f9",
  border: "1px solid #e2e8f0",
  borderRadius: 9,
  cursor: "pointer",
  color: "#64748b",
};

const sectionStyle: React.CSSProperties = {
  borderTop: "1px solid #eef1f6",
  paddingTop: 16,
  marginTop: 16,
  display: "grid",
  gap: 9,
};

const h3Style: React.CSSProperties = {
  margin: 0,
  fontSize: 13,
  color: "#0f172a",
  fontWeight: 700,
  letterSpacing: 0.2,
};


const btnRowStyle: React.CSSProperties = {
  display: "flex",
  gap: 8,
  flexWrap: "wrap",
};

const btnStyle: React.CSSProperties = {
  padding: "7px 14px",
  borderRadius: 9,
  border: "1px solid #e2e8f0",
  background: "#ffffff",
  color: "#334155",
  fontSize: 12,
  fontWeight: 600,
  cursor: "pointer",
  transition: "background 120ms ease, border-color 120ms ease",
};

const statusStyle: React.CSSProperties = {
  fontSize: 12,
  padding: "7px 10px",
  background: "#f8fafc",
  border: "1px solid #eef1f6",
  borderRadius: 9,
  lineHeight: 1.5,
};

const hintStyle: React.CSSProperties = {
  fontSize: 11.5,
  color: "#64748b",
  margin: 0,
  lineHeight: 1.6,
};

