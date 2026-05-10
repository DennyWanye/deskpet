/**
 * P5-S3 — Pet supervisor bubble.
 *
 * Small overlay that appears above Hiyori when a `supervisor_alert` is
 * active for the focus session. Carries the supervisor's user_message
 * + up to 2 buttons. Click on background → invokes onClickBackground
 * (parent jumps to that session in the code panel). Click on a button
 * → onChoice + immediate hide (optimistic UI).
 *
 * Hiyori has no "concerned" expression, so this bubble carries the
 * semantic meaning: the visual transitions to "looking down + frequent
 * blinks" only show *that* something is wrong; the bubble tells the
 * user *what*.
 */
import { useEffect, useState } from "react";
import type { CSSProperties, MouseEvent } from "react";

export type BubbleSeverity = "yellow" | "red" | "blue";

export interface PetSupervisorBubbleProps {
  /** Visual severity. yellow=worried, red=alert, blue=intervening. */
  severity: BubbleSeverity;
  /** Body text from supervisor.user_message. */
  message: string;
  /** Up to 2 button labels from supervisor.suggested_buttons. */
  buttons?: string[];
  /** sid of the focus session, displayed truncated for context. */
  session_id: string;
  /** Alert id used for click-back correlation. */
  alert_id: string;
  /** Called when the user clicks the bubble background (not a button). */
  onClickBackground?: (sid: string) => void;
  /** Called when the user clicks button at index. */
  onChoice?: (index: number, text: string, alert_id: string, sid: string) => void;
}

const COLORS: Record<BubbleSeverity, { bg: string; fg: string; border: string; pulse: boolean }> = {
  yellow: {
    bg: "rgba(120, 80, 0, 0.92)",
    fg: "#fde68a",
    border: "rgba(245, 158, 11, 0.6)",
    pulse: false,
  },
  red: {
    bg: "rgba(120, 30, 30, 0.94)",
    fg: "#fecaca",
    border: "rgba(239, 68, 68, 0.7)",
    pulse: true,
  },
  blue: {
    bg: "rgba(30, 60, 120, 0.92)",
    fg: "#bfdbfe",
    border: "rgba(59, 130, 246, 0.6)",
    pulse: false,
  },
};

export function PetSupervisorBubble({
  severity,
  message,
  buttons = [],
  session_id,
  alert_id,
  onClickBackground,
  onChoice,
}: PetSupervisorBubbleProps) {
  const c = COLORS[severity];
  const [visible, setVisible] = useState(false);
  // Trigger fade-in on mount + after prop changes
  useEffect(() => {
    setVisible(true);
    return () => setVisible(false);
  }, [alert_id]);

  // Click on background (not on a button) → jump to session
  function onBgClick(_e: MouseEvent<HTMLDivElement>) {
    onClickBackground?.(session_id);
  }

  function onBtn(e: MouseEvent<HTMLButtonElement>, idx: number, text: string) {
    e.stopPropagation();
    onChoice?.(idx, text, alert_id, session_id);
  }

  const truncated_sid =
    session_id.length > 18 ? `${session_id.slice(0, 16)}…` : session_id;

  const wrapperStyle: CSSProperties = {
    position: "absolute",
    top: 96,
    left: "50%",
    transform: "translateX(-50%)",
    zIndex: 25,
    pointerEvents: "auto",
    minWidth: 220,
    maxWidth: 360,
    padding: "10px 14px",
    borderRadius: 12,
    background: c.bg,
    color: c.fg,
    border: `1.5px solid ${c.border}`,
    backdropFilter: "blur(10px)",
    boxShadow: "0 4px 16px rgba(0,0,0,0.4)",
    cursor: "pointer",
    fontSize: 12,
    lineHeight: 1.4,
    transition: "opacity 300ms ease, transform 300ms ease",
    opacity: visible ? 1 : 0,
    animation: c.pulse ? "deskpet-pulse 0.9s ease-in-out infinite" : undefined,
  };

  return (
    <>
      {/* Inject keyframes once. CSS is also fine to put in a global stylesheet
          but keeping it co-located makes the component self-contained. */}
      <style>{`
        @keyframes deskpet-pulse {
          0%, 100% { box-shadow: 0 4px 16px rgba(239, 68, 68, 0.5); }
          50%      { box-shadow: 0 4px 24px rgba(239, 68, 68, 0.95); }
        }
      `}</style>
      <div role="alert" style={wrapperStyle} onClick={onBgClick}>
        <div style={{ display: "flex", alignItems: "center", marginBottom: 6, fontSize: 10, opacity: 0.8 }}>
          <span style={{ marginRight: 6 }}>
            {severity === "red" ? "🚨" : severity === "yellow" ? "⚠️" : "💡"}
          </span>
          <span style={{ fontWeight: 600 }}>桌宠提醒</span>
          <span style={{ marginLeft: "auto", opacity: 0.6 }}>{truncated_sid}</span>
        </div>
        <div style={{ marginBottom: buttons.length ? 8 : 0, whiteSpace: "pre-wrap" }}>
          {message || "supervisor 检测到一个需要关注的状态。"}
        </div>
        {buttons.length > 0 && (
          <div style={{ display: "flex", gap: 6, justifyContent: "flex-end" }}>
            {buttons.slice(0, 2).map((b, i) => (
              <button
                key={i}
                style={{
                  fontSize: 11,
                  fontWeight: 600,
                  padding: "4px 10px",
                  borderRadius: 6,
                  border: `1px solid ${c.border}`,
                  background: i === 0 ? c.fg : "transparent",
                  color: i === 0 ? c.bg : c.fg,
                  cursor: "pointer",
                }}
                onClick={(e) => onBtn(e, i, b)}
              >
                {b}
              </button>
            ))}
          </div>
        )}
      </div>
    </>
  );
}
