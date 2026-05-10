/**
 * P5-S2 Phase 5.2 — AutoResumeBanner.
 *
 * 一个轻量 inline 横幅：当 backend 的 AutoResumeOrchestrator 决定自愈
 * 一个失败任务时（chat handler 返回 max_iterations / permanent_tool_error /
 * circuit_open），通过 ws 三个事件控制本组件：
 *
 *   auto_resume_started      → set auto_resume_attempts = N，banner 显示
 *   auto_resume_succeeded    → reset 0，banner 消失
 *   auto_resume_exhausted    → reset 0 + emit 红色 error message（在 ws.ts dispatch 里做）
 *
 * 设计要点：
 *   1. 不阻断输入框 / 不弹模态。
 *   2. 订阅当前 active_sid 的 auto_resume_attempts 字段（zustand selector）。
 *   3. 纯函数 buildAutoResumeBannerText / shouldShowAutoResumeBanner
 *      可被测试单独 import；React 组件本身只是把它们映射成 JSX。
 */
import { useSessionsStore } from "../stores/sessionsStore";

const DEFAULT_MAX_ATTEMPTS = 2;

export function buildAutoResumeBannerText(attempts: number, max: number): string {
  return `🔄 agent 自愈中... (尝试 ${attempts}/${max})`;
}

export function shouldShowAutoResumeBanner(
  attempts: number,
  inflight: boolean,
): boolean {
  return attempts > 0 && inflight;
}

interface AutoResumeBannerProps {
  /** Optional: pin to a specific session (e.g. tile view). Defaults to active_sid. */
  sessionId?: string;
  /** Display max for "N/M" — defaults to backend cap (2). */
  maxAttempts?: number;
}

export function AutoResumeBanner({
  sessionId,
  maxAttempts = DEFAULT_MAX_ATTEMPTS,
}: AutoResumeBannerProps) {
  const sid = useSessionsStore((s) => sessionId ?? s.active_sid);
  const session = useSessionsStore((s) => s.sessions[sid]);
  const attempts = session?.auto_resume_attempts ?? 0;
  const inflight = session?.inflight ?? false;
  const visible = shouldShowAutoResumeBanner(attempts, inflight);

  if (!visible) return null;

  const text = buildAutoResumeBannerText(attempts, maxAttempts);

  return (
    <div
      role="status"
      aria-live="polite"
      data-testid="auto-resume-banner"
      style={{
        margin: "6px 0",
        padding: "6px 12px",
        background: "rgba(245, 158, 11, 0.15)",
        color: "#fcd34d",
        border: "1px solid rgba(245, 158, 11, 0.55)",
        borderRadius: 6,
        fontSize: 12.5,
        // 500ms transition for the spec'd "smooth dismiss" — visibility flip
        // is instant from React's view but opacity gives the eye a half-step.
        transition: "opacity 500ms ease",
      }}
    >
      {text}
    </div>
  );
}
