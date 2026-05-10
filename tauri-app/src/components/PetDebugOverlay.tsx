/**
 * P5-S1 D — Pet supervisor debug overlay.
 *
 * Activates only when ``localStorage.deskpet_debug === "1"``. Displays
 * the pet state machine internals + per-session severity breakdown so
 * a developer (or user filing a bug) can see exactly why the pet
 * picked a given state.
 *
 * Renders in the bottom-left corner with an orange/black scientific-
 * instrument vibe. Should NOT be shown to end users — gating via
 * ``localStorage`` keeps it dev-only without needing a build flag.
 */
import { useEffect, useState } from "react";
import type { CSSProperties } from "react";

import {
  useSessionsStore,
  pet_focus_sid,
  severity_score_breakdown,
} from "../stores/sessionsStore";
import type { PetState } from "../pet-state/PetStateMachine";

interface Props {
  /** Current visible state from PetStateMachine.tick(). */
  pet_state: PetState;
  /** Focus sid the state machine picked for this tick. */
  focus_sid: string | null;
  /** Total score of the focus session. */
  focus_score: number;
}

function isDebugEnabled(): boolean {
  try {
    return localStorage.getItem("deskpet_debug") === "1";
  } catch {
    return false;
  }
}

export function PetDebugOverlay({ pet_state, focus_sid, focus_score }: Props) {
  // React to localStorage flips without a full reload — listen for the
  // synthetic 'storage' event AND a 5s polling fallback (Chrome doesn't
  // fire 'storage' for same-tab writes).
  const [debug, setDebug] = useState(isDebugEnabled());
  useEffect(() => {
    const onStorage = () => setDebug(isDebugEnabled());
    window.addEventListener("storage", onStorage);
    const id = window.setInterval(onStorage, 5000);
    return () => {
      window.removeEventListener("storage", onStorage);
      window.clearInterval(id);
    };
  }, []);

  const sessions = useSessionsStore((s) => s.sessions);

  if (!debug) return null;

  // Recompute focus + per-session breakdowns at render time. Cheap.
  const computed_focus = pet_focus_sid(sessions);
  const session_rows = Object.entries(sessions)
    .filter(([, s]) => s.code_session_id || s.project_root)
    .map(([sid, s]) => ({
      sid,
      breakdown: severity_score_breakdown(s),
      status: s.status,
    }))
    .sort((a, b) => b.breakdown.total - a.breakdown.total);

  return (
    <div role="region" aria-label="Pet supervisor debug overlay" style={panelStyle}>
      <div style={titleStyle}>
        🔬 Pet Supervisor — debug
      </div>

      <div style={lineStyle}>
        <span style={labelStyle}>state</span>
        <span style={valueStyle}>{pet_state}</span>
      </div>
      <div style={lineStyle}>
        <span style={labelStyle}>focus_sid (sm)</span>
        <span style={valueStyle}>{focus_sid ?? "<none>"}</span>
      </div>
      <div style={lineStyle}>
        <span style={labelStyle}>focus_sid (live)</span>
        <span style={{ ...valueStyle, opacity: computed_focus === focus_sid ? 0.6 : 1 }}>
          {computed_focus ?? "<none>"}
        </span>
      </div>
      <div style={lineStyle}>
        <span style={labelStyle}>focus_score</span>
        <span style={valueStyle}>{Math.round(focus_score)}</span>
      </div>

      <div style={{ ...lineStyle, marginTop: 6, opacity: 0.7 }}>
        <span style={labelStyle}>sessions</span>
        <span style={valueStyle}>{session_rows.length}</span>
      </div>

      {session_rows.length > 0 && (
        <table style={tableStyle}>
          <thead>
            <tr>
              <th style={thStyle}>sid</th>
              <th style={thStyle}>status</th>
              <th style={thStyleN}>base</th>
              <th style={thStyleN}>age</th>
              <th style={thStyleN}>rep</th>
              <th style={thStyleN}>sup</th>
              <th style={thStyleN}>iter</th>
              <th style={thStyleN}>tot</th>
            </tr>
          </thead>
          <tbody>
            {session_rows.map((r) => (
              <tr
                key={r.sid}
                style={{
                  background: r.sid === focus_sid ? "rgba(245, 158, 11, 0.18)" : "transparent",
                }}
              >
                <td style={tdStyle}>{r.sid.slice(0, 14)}</td>
                <td style={tdStyle}>{r.status}</td>
                <td style={tdStyleN}>{Math.round(r.breakdown.base)}</td>
                <td style={tdStyleN}>{Math.round(r.breakdown.age)}</td>
                <td style={tdStyleN}>{Math.round(r.breakdown.repeat)}</td>
                <td style={tdStyleN}>{Math.round(r.breakdown.supervisor)}</td>
                <td style={tdStyleN}>{Math.round(r.breakdown.iteration)}</td>
                <td style={{ ...tdStyleN, fontWeight: 700 }}>
                  {Math.round(r.breakdown.total)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <div style={{ ...lineStyle, marginTop: 4, opacity: 0.55, fontSize: 9 }}>
        关闭：localStorage.deskpet_debug = "0"
      </div>
    </div>
  );
}

// ─── styles ─────────────────────────────────────────────────────────

const panelStyle: CSSProperties = {
  position: "fixed",
  bottom: 8,
  left: 8,
  zIndex: 30,
  background: "rgba(10, 10, 14, 0.92)",
  color: "#fbbf24",
  border: "1px solid rgba(245, 158, 11, 0.4)",
  borderRadius: 6,
  padding: "6px 10px",
  fontFamily:
    'ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace',
  fontSize: 10.5,
  lineHeight: 1.5,
  pointerEvents: "auto",
  maxWidth: 480,
  boxShadow: "0 2px 12px rgba(0,0,0,0.5)",
};

const titleStyle: CSSProperties = {
  fontWeight: 700,
  marginBottom: 4,
  fontSize: 11,
  color: "#fde68a",
  borderBottom: "1px solid rgba(245, 158, 11, 0.3)",
  paddingBottom: 2,
};

const lineStyle: CSSProperties = {
  display: "flex",
  alignItems: "baseline",
  gap: 6,
};

const labelStyle: CSSProperties = {
  width: 110,
  color: "rgba(251, 191, 36, 0.65)",
};

const valueStyle: CSSProperties = {
  color: "#fef3c7",
};

const tableStyle: CSSProperties = {
  marginTop: 4,
  borderCollapse: "collapse",
  fontSize: 10,
  width: "100%",
};

const thStyle: CSSProperties = {
  textAlign: "left",
  padding: "2px 4px",
  color: "rgba(251, 191, 36, 0.65)",
  borderBottom: "1px solid rgba(245, 158, 11, 0.25)",
};

const thStyleN: CSSProperties = {
  ...thStyle,
  textAlign: "right",
  width: 30,
};

const tdStyle: CSSProperties = {
  padding: "2px 4px",
  color: "#fef3c7",
};

const tdStyleN: CSSProperties = {
  ...tdStyle,
  textAlign: "right",
};
