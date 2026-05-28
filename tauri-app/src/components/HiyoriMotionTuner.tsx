// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * P5-S1 D — Hiyori motion calibration tuner.
 *
 * Renders inside SettingsPanel only when localStorage.deskpet_debug=1.
 * For each Idle motion (m01..m10) and TapBody, exposes a "play" button
 * + label/notes input + radio for "fast/medium/slow/special" tag.
 *
 * On save, writes to localStorage.deskpet_motion_labels (JSON map of
 * { [motionIndex: number]: { tag, note } }). PetStateMachine reads
 * from this when localStorage is present, falling back to its hard-
 * coded STATE_CONFIG when not.
 *
 * Why localStorage instead of a code change: lets you record per-user
 * calibration without recompiling, and the tags are subjective enough
 * that two testers might land on different splits.
 */
import { useEffect, useState } from "react";
import type { CSSProperties } from "react";

const TAG_OPTIONS = ["fast", "medium", "slow", "special"] as const;
type Tag = (typeof TAG_OPTIONS)[number];

interface Label {
  tag?: Tag;
  note?: string;
}

const LS_KEY = "deskpet_motion_labels";
const HIYORI_IDLE_INDICES = Array.from({ length: 10 }, (_, i) => i);  // 0..9 → m01..m10

function loadLabels(): Record<string, Label> {
  try {
    const raw = localStorage.getItem(LS_KEY);
    return raw ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
}

function saveLabels(labels: Record<string, Label>) {
  try {
    localStorage.setItem(LS_KEY, JSON.stringify(labels));
  } catch {
    /* full / disabled — non-fatal */
  }
}

function isDebugEnabled(): boolean {
  try {
    return localStorage.getItem("deskpet_debug") === "1";
  } catch {
    return false;
  }
}

export function HiyoriMotionTuner() {
  const [debug, setDebug] = useState(isDebugEnabled());
  useEffect(() => {
    const id = window.setInterval(() => setDebug(isDebugEnabled()), 5000);
    return () => window.clearInterval(id);
  }, []);

  const [labels, setLabels] = useState<Record<string, Label>>(() => loadLabels());

  if (!debug) return null;

  const update = (key: string, patch: Partial<Label>) => {
    const next = { ...labels, [key]: { ...labels[key], ...patch } };
    setLabels(next);
    saveLabels(next);
  };

  // pixi-live2d-display motion API: motion(group, index?, priority?).
  // The Live2DCanvas exposes an index-targeting helper on window so we
  // can play a specific Idle slot (m01..m10) — the imperative
  // Live2DHandle.playMotion only does random group selection.
  const play = (group: string, idx?: number) => {
    try {
      const fn = (window as any).__deskpet_play_motion;
      if (typeof fn === "function") fn(group, idx);
    } catch (e) {
      console.warn("[Tuner] motion failed:", e);
    }
  };

  return (
    <details
      style={{
        marginTop: 12,
        background: "rgba(15, 15, 20, 0.85)",
        border: "1px dashed rgba(245, 158, 11, 0.3)",
        borderRadius: 6,
        padding: "8px 10px",
        color: "#fbbf24",
        fontSize: 11,
      }}
    >
      <summary
        style={{
          cursor: "pointer",
          fontWeight: 600,
          fontSize: 12,
          color: "#fde68a",
        }}
      >
        🎬 Hiyori motion 校准（debug only）
      </summary>
      <p style={{ fontSize: 10.5, color: "rgba(251, 191, 36, 0.7)", lineHeight: 1.5 }}>
        逐个试听 Idle m01–m10 + TapBody，给每个标 fast/medium/slow，回填到
        PetStateMachine 的 motion_pool。保存到 <code>localStorage.{LS_KEY}</code>，
        无副作用，可随时清空。
      </p>

      <table style={tableStyle}>
        <thead>
          <tr>
            <th style={thStyle}>motion</th>
            <th style={thStyle}>play</th>
            <th style={thStyle}>tag</th>
            <th style={thStyle}>note</th>
          </tr>
        </thead>
        <tbody>
          {HIYORI_IDLE_INDICES.map((i) => {
            const key = `Idle:${i}`;
            const lab = labels[key] || {};
            return (
              <tr key={key}>
                <td style={tdStyle}>m{String(i + 1).padStart(2, "0")}</td>
                <td style={tdStyle}>
                  <button onClick={() => play("Idle", i)} style={playBtnStyle}>▶</button>
                </td>
                <td style={tdStyle}>
                  <select
                    value={lab.tag ?? ""}
                    onChange={(e) => update(key, { tag: (e.target.value || undefined) as Tag })}
                    style={selectStyle}
                  >
                    <option value="">-</option>
                    {TAG_OPTIONS.map((t) => (
                      <option key={t} value={t}>{t}</option>
                    ))}
                  </select>
                </td>
                <td style={tdStyle}>
                  <input
                    type="text"
                    value={lab.note ?? ""}
                    onChange={(e) => update(key, { note: e.target.value })}
                    placeholder="挥手 / 看周围 / ..."
                    style={inputStyle}
                  />
                </td>
              </tr>
            );
          })}
          <tr style={{ borderTop: "1px solid rgba(245, 158, 11, 0.25)" }}>
            <td style={tdStyle}>TapBody</td>
            <td style={tdStyle}>
              <button onClick={() => play("TapBody", 0)} style={playBtnStyle}>▶</button>
            </td>
            <td style={tdStyle} colSpan={2}>
              <span style={{ fontSize: 10, opacity: 0.7 }}>
                作为 alert/intervening 的入场触发，固定使用，无需 tag。
              </span>
            </td>
          </tr>
        </tbody>
      </table>

      <div style={{ marginTop: 8, display: "flex", gap: 8 }}>
        <button
          style={{ ...playBtnStyle, padding: "3px 10px" }}
          onClick={() => {
            setLabels({});
            saveLabels({});
          }}
        >
          清空
        </button>
        <span style={{ fontSize: 10, opacity: 0.6, alignSelf: "center" }}>
          已保存 {Object.values(labels).filter((l) => l.tag).length} / 10 项
        </span>
      </div>
    </details>
  );
}

// ─── styles ─────────────────────────────────────────────────────────

const tableStyle: CSSProperties = {
  marginTop: 6,
  borderCollapse: "collapse",
  fontSize: 11,
  width: "100%",
};

const thStyle: CSSProperties = {
  textAlign: "left",
  padding: "2px 4px",
  color: "rgba(251, 191, 36, 0.65)",
  borderBottom: "1px solid rgba(245, 158, 11, 0.25)",
  fontSize: 10,
};

const tdStyle: CSSProperties = {
  padding: "3px 4px",
  color: "#fef3c7",
};

const playBtnStyle: CSSProperties = {
  background: "rgba(245, 158, 11, 0.25)",
  color: "#fde68a",
  border: "1px solid rgba(245, 158, 11, 0.5)",
  borderRadius: 4,
  padding: "2px 8px",
  fontSize: 11,
  cursor: "pointer",
};

const selectStyle: CSSProperties = {
  background: "rgba(20, 20, 28, 0.95)",
  color: "#fde68a",
  border: "1px solid rgba(245, 158, 11, 0.35)",
  borderRadius: 3,
  fontSize: 10,
  padding: "1px 4px",
};

const inputStyle: CSSProperties = {
  ...selectStyle,
  width: "100%",
  padding: "2px 5px",
};

// Helper: read calibrated labels from outside the component (for tests
// or for PetStateMachine's optional lookup).
export function getCalibratedMotions(): Record<string, Label> {
  return loadLabels();
}
