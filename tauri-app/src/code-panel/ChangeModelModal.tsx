/**
 * code-session-model-params S2 — Cursor-style per-code-session model +
 * params picker (replaces the old free-text model `<input>`).
 *
 * Layout mirrors Cursor's model switcher. The model dropdown is
 * data-driven from the relay's live /models catalog (codeModelsStore),
 * NOT a hardcoded preset list. Each model carries a capability map, so
 * the picker only renders the controls that model supports — gpt-5.x
 * shows Effort (reasoning_effort); claude opus/sonnet shows Thinking
 * (no Effort); an embedding model shows nothing. On 保存 we emit
 *   code_session_set_model { session_id, model, params }
 * with only the caps-supported keys of { thinking, fast, context, effort }.
 *
 * Back-compat: 清空 sends the legacy `{ session_id, model:null }` shape
 * (no `params` key) so the backend clears the binding and falls back to
 * the global chain / provider defaults — exactly the pre-S2 behaviour.
 *
 * Pre-fill: model from `current_model`, controls from `current_params`
 * (the backend echoes the raw picker dict back, so it round-trips).
 */
import { useState, useEffect } from "react";

import { codePanelWS } from "./ws";
import { buildSetModelMessage } from "./SessionGridView";
import {
  useCodeModelsStore,
  capsForModel,
  buildModelOptionsFromCatalog,
} from "./codeModelsStore";
import { useSessionsStore, type CodeModelParams } from "../stores/sessionsStore";

type EffortValue = "low" | "medium" | "high" | "extra_high" | "max";
type ContextValue = "300k" | "1m";
type ReasonMode = "default" | "thinking" | "fast";

const EFFORT_OPTIONS: ReadonlyArray<{ value: EffortValue; label: string }> = [
  { value: "low", label: "Low" },
  { value: "medium", label: "Medium" },
  { value: "high", label: "High" },
  { value: "extra_high", label: "Extra High" },
  { value: "max", label: "Max" },
];

const CONTEXT_OPTIONS: ReadonlyArray<{ value: ContextValue; label: string }> = [
  { value: "300k", label: "300K" },
  { value: "1m", label: "1M" },
];

export interface ChangeModelModalProps {
  session_id: string;
  /** Current preferred_model value (null/undefined → "follow default"). */
  current_model: string | null | undefined;
  /** Current persisted model_params (null/undefined → picker defaults). */
  current_params?: CodeModelParams | null;
  onClose: () => void;
}

export function ChangeModelModal({
  session_id,
  current_model,
  current_params,
  onClose,
}: ChangeModelModalProps) {
  const catalog = useCodeModelsStore((s) => s.models);
  const model_opts = buildModelOptionsFromCatalog(current_model, catalog);
  const [model, set_model] = useState<string>(current_model ?? "");
  // Thinking (重推理/慢) and Fast (低延迟/快) are mutually exclusive —
  // a single-select "推理模式" instead of two independent toggles.
  // "默认" = neither (provider default).
  const [reason_mode, set_reason_mode] = useState<ReasonMode>(
    current_params?.thinking
      ? "thinking"
      : current_params?.fast
        ? "fast"
        : "default",
  );
  const [context, set_context] = useState<ContextValue>(
    current_params?.context ?? "300k",
  );
  const [effort, set_effort] = useState<EffortValue>(
    current_params?.effort ?? "medium",
  );
  // Per-model capability map — gpt-5.x exposes reasoning_effort, claude
  // opus/sonnet exposes thinking; the picker only renders the controls
  // the chosen model actually supports. Unknown/custom id → permissive.
  const caps = capsForModel(model || current_model, catalog);

  useEffect(() => {
    const onEsc = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onEsc);
    return () => window.removeEventListener("keydown", onEsc);
  }, [onClose]);

  const submit = () => {
    // Only emit the params the selected model supports — sending
    // reasoning_effort to a claude model (or thinking to an embedding
    // model) is meaningless. Backend mapper also clamps, but a clean
    // payload keeps the binding honest per-model.
    const params: CodeModelParams = {};
    // Single-select reasoning mode → the two backend booleans. They are
    // never both true (mutually exclusive by construction).
    if (caps.thinking) params.thinking = reason_mode === "thinking";
    if (caps.fast) params.fast = reason_mode === "fast";
    if (caps.context) params.context = context;
    if (caps.effort) params.effort = effort;
    codePanelWS.send(buildSetModelMessage(session_id, model, params));
    // Optimistic write — backend's code_session_model_set ack reconciles
    // (same pattern as the provider dropdown).
    useSessionsStore.getState().upsert(session_id, {
      preferred_model: model.trim() !== "" ? model : null,
      model_params: params,
    });
    onClose();
  };

  const clear_and_submit = () => {
    // Legacy shape (no `params`) ⇒ backend clears the binding row.
    codePanelWS.send(buildSetModelMessage(session_id, null));
    useSessionsStore.getState().upsert(session_id, {
      preferred_model: null,
      model_params: null,
    });
    onClose();
  };

  return (
    <div
      style={overlayStyle}
      onClick={onClose}
      role="dialog"
      aria-label="改 model"
    >
      <div style={dialogStyle} onClick={(e) => e.stopPropagation()}>
        <header style={headerStyle}>模型与参数</header>

        <div style={bodyStyle}>
          {/* Model dropdown */}
          <label style={labelStyle} htmlFor="change-model-select">
            模型
          </label>
          <select
            id="change-model-select"
            aria-label="模型"
            value={model_opts.some((o) => o.value === model) ? model : (current_model ?? "")}
            onChange={(e) => set_model(e.target.value)}
            style={selectStyle}
          >
            {model_opts.map((o) => (
              <option key={o.value || "__default__"} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>

          {/* Reasoning mode — single-select (Thinking ⟺ Fast are
              mutually exclusive: high reasoning/slow vs low latency).
              Only the modes the model supports are offered. */}
          {(caps.thinking || caps.fast) && (
            <>
              <label style={labelStyle}>推理模式</label>
              <Segmented
                ariaLabel="推理模式"
                options={[
                  { value: "default" as ReasonMode, label: "默认" },
                  ...(caps.thinking
                    ? [{ value: "thinking" as ReasonMode, label: "Thinking" }]
                    : []),
                  ...(caps.fast
                    ? [{ value: "fast" as ReasonMode, label: "Fast" }]
                    : []),
                ]}
                value={reason_mode}
                onChange={set_reason_mode}
              />
            </>
          )}

          {/* Context segmented */}
          {caps.context && (
            <>
              <label style={labelStyle}>上下文窗口</label>
              <Segmented
                ariaLabel="上下文窗口"
                options={CONTEXT_OPTIONS}
                value={context}
                onChange={set_context}
              />
            </>
          )}

          {/* Effort segmented — gpt-5.x / OpenAI family only */}
          {caps.effort && (
            <>
              <label style={labelStyle}>推理强度（Effort）</label>
              <Segmented
                ariaLabel="推理强度"
                options={EFFORT_OPTIONS}
                value={effort}
                onChange={set_effort}
              />
              <span style={hintStyle}>
                Extra High / Max 在仅支持 low/medium/high 的后端会收敛到 High
              </span>
            </>
          )}

          {!caps.thinking && !caps.fast && !caps.context && !caps.effort && (
            <span style={hintStyle}>
              该模型无可调参数（仅切换模型本身）
            </span>
          )}
        </div>

        <footer style={footerStyle}>
          <button type="button" onClick={onClose} style={btnSecondary}>
            取消
          </button>
          <button
            type="button"
            onClick={clear_and_submit}
            style={btnSecondary}
          >
            清空（回全局链）
          </button>
          <button type="button" onClick={submit} style={btnPrimary}>
            保存
          </button>
        </footer>
      </div>
    </div>
  );
}

function Segmented<T extends string>({
  ariaLabel,
  options,
  value,
  onChange,
}: {
  ariaLabel: string;
  options: ReadonlyArray<{ value: T; label: string }>;
  value: T;
  onChange: (v: T) => void;
}) {
  return (
    <div role="group" aria-label={ariaLabel} style={segmentedWrapStyle}>
      {options.map((o) => {
        const active = o.value === value;
        return (
          <button
            key={o.value}
            type="button"
            aria-pressed={active}
            onClick={() => onChange(o.value)}
            style={{
              ...segmentBtnStyle,
              background: active ? "#2563eb" : "transparent",
              color: active ? "#fff" : "#cbd5e1",
              fontWeight: active ? 600 : 500,
            }}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

const overlayStyle: React.CSSProperties = {
  position: "fixed",
  inset: 0,
  background: "rgba(0, 0, 0, 0.55)",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  zIndex: 1000,
};

const dialogStyle: React.CSSProperties = {
  background: "#1e2330",
  color: "#e2e8f0",
  borderRadius: 8,
  padding: 18,
  width: 380,
  boxShadow: "0 10px 40px rgba(0,0,0,0.5)",
  border: "1px solid rgba(148,163,184,0.18)",
};

const headerStyle: React.CSSProperties = {
  fontSize: 14,
  fontWeight: 700,
  marginBottom: 12,
};

const bodyStyle: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 7,
  marginBottom: 14,
};

const labelStyle: React.CSSProperties = {
  fontSize: 11,
  color: "#94a3b8",
  marginTop: 4,
};

const hintStyle: React.CSSProperties = {
  fontSize: 10,
  color: "rgba(148,163,184,0.7)",
  fontStyle: "italic",
};

const selectStyle: React.CSSProperties = {
  background: "rgba(30, 35, 48, 0.85)",
  color: "#e2e8f0",
  border: "1px solid rgba(148, 163, 184, 0.30)",
  borderRadius: 6,
  padding: "6px 9px",
  fontSize: 12.5,
  outline: "none",
  cursor: "pointer",
};


const segmentedWrapStyle: React.CSSProperties = {
  display: "flex",
  gap: 3,
  background: "rgba(15, 18, 28, 0.6)",
  border: "1px solid rgba(148, 163, 184, 0.22)",
  borderRadius: 6,
  padding: 3,
};

const segmentBtnStyle: React.CSSProperties = {
  flex: 1,
  border: "none",
  borderRadius: 4,
  padding: "5px 6px",
  fontSize: 11.5,
  cursor: "pointer",
  transition: "background 120ms",
};

const footerStyle: React.CSSProperties = {
  display: "flex",
  justifyContent: "flex-end",
  gap: 8,
};

const btnPrimary: React.CSSProperties = {
  background: "#2563eb",
  color: "#fff",
  border: "none",
  borderRadius: 5,
  padding: "5px 12px",
  fontSize: 12,
  cursor: "pointer",
  fontWeight: 600,
};

const btnSecondary: React.CSSProperties = {
  background: "rgba(148, 163, 184, 0.18)",
  color: "#e2e8f0",
  border: "1px solid rgba(148, 163, 184, 0.30)",
  borderRadius: 5,
  padding: "5px 12px",
  fontSize: 12,
  cursor: "pointer",
};
