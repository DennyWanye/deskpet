// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

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
  contextWindowForModel,
  supportedWindowsForModel,
  formatContextWindow,
} from "./codeModelsStore";
import { useSessionsStore, type CodeModelParams } from "../stores/sessionsStore";

type EffortValue = "low" | "medium" | "high" | "extra_high" | "max";
type ReasonMode = "default" | "thinking" | "fast";

const EFFORT_OPTIONS: ReadonlyArray<{ value: EffortValue; label: string }> = [
  { value: "low", label: "Low" },
  { value: "medium", label: "Medium" },
  { value: "high", label: "High" },
  { value: "extra_high", label: "Extra High" },
  { value: "max", label: "Max" },
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
  const default_model = useCodeModelsStore((s) => s.default_model);
  const model_opts = buildModelOptionsFromCatalog(
    current_model,
    catalog,
    default_model,
  );
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
  const [effort, set_effort] = useState<EffortValue>(
    current_params?.effort ?? "medium",
  );
  // 用于查 caps / 上下文档位的「生效模型」：用户显式选了就用它,否则会话
  // 固定的 preferred_model,再否则 provider 默认。2026-06-26 修复:此前只取
  // `model || current_model`,「跟随 provider 默认」时两者皆空 → caps/档位
  // 查不到 → 上下文窗口退化成只读「由 provider 决定」、选不了档,且保存时
  // model_context_set 拿空 id 被后端 save_global_window_override 拒。补上
  // default_model 兜底后,gpt-5.5 的 128K/400K/1M 三档正常可选 + 可存。
  const ctx_model = (model || current_model || default_model || "").trim();
  // Per-model capability map — gpt-5.x exposes reasoning_effort, claude
  // opus/sonnet exposes thinking; the picker only renders the controls
  // the chosen model actually supports. Unknown/custom id → permissive.
  const caps = capsForModel(ctx_model, catalog);
  // 2026-06-12: 上下文窗口从「只读」升级为「按型号可选档位」。
  // supported_windows > 1 档(如 gpt-5.5: 128K/400K/1M) → 渲染下拉,
  // 选择经 model_context_set 持久化到 backend 全局 override(压缩阈值/
  // 预算同步生效);单档/未知 → 保持只读 chip。
  const ctx_window = contextWindowForModel(ctx_model, catalog);
  const ctx_options = supportedWindowsForModel(ctx_model, catalog);
  const [ctx_choice, set_ctx_choice] = useState<number | null>(null);
  // 切换模型 → 档位选择回到该模型当前值
  useEffect(() => {
    set_ctx_choice(null);
  }, [model]);
  const ctx_selected = ctx_choice ?? ctx_window;

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
    if (caps.effort) params.effort = effort;
    // 上下文档位变化 → 持久化为该模型的全局 override(非 per-session 参数:
    // 窗口是模型属性,backend 压缩阈值/预算据此对齐),然后刷新 catalog。
    if (
      ctx_choice != null &&
      ctx_choice !== ctx_window &&
      ctx_options.includes(ctx_choice)
    ) {
      // 完整协议(p4_ipc): {scope, model, fields} — 2026-06-13 修复:
      // 之前发的简版 {model, context_window} 走了一个抢路由的重复
      // handler(已删),现统一走 p4_ipc 完整版。
      codePanelWS.send({
        type: "model_context_set",
        payload: {
          scope: "global",
          // 用生效模型(含 default_model 兜底),否则跟随默认时拿空 id 被后端拒。
          model: ctx_model,
          fields: { context_window: ctx_choice },
        },
      });
      codePanelWS.send({ type: "code_models_list" });
    }
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
          {/* 当前真正生效的模型：固定了就是它,否则是 provider 默认。 */}
          {(model.trim() || default_model.trim()) && (
            <span style={hintStyle}>
              当前生效：{model.trim() || default_model.trim()}
              {!model.trim() && default_model.trim() ? "（provider 默认）" : ""}
            </span>
          )}

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

          {/* Context window — 按型号可选档位(>1 档渲染下拉,保存时持久化
              为全局 override);单档/未知型号保持只读 chip。 */}
          {caps.context && ctx_options.length > 1 && (
            <>
              <label style={labelStyle} htmlFor="ctx-window-select">
                上下文窗口
              </label>
              <select
                id="ctx-window-select"
                aria-label="上下文窗口"
                value={String(ctx_selected ?? ctx_options[0])}
                onChange={(e) => set_ctx_choice(Number(e.target.value))}
                style={selectStyle}
              >
                {ctx_options.map((w) => (
                  <option key={w} value={String(w)}>
                    {formatContextWindow(w)}
                  </option>
                ))}
              </select>
              <span style={hintStyle}>
                档位为该模型支持的窗口；选择对所有会话生效
              </span>
            </>
          )}
          {caps.context && ctx_options.length <= 1 && (
            <>
              <label style={labelStyle}>上下文窗口（模型决定）</label>
              <div style={ctxChipStyle} aria-label="上下文窗口">
                {ctx_window != null
                  ? formatContextWindow(ctx_window) || "由 provider 决定"
                  : "由 provider 决定"}
              </div>
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

const ctxChipStyle: React.CSSProperties = {
  background: "rgba(30, 35, 48, 0.85)",
  color: "#cbd5e1",
  border: "1px solid rgba(148, 163, 184, 0.22)",
  borderRadius: 6,
  padding: "6px 9px",
  fontSize: 12,
  fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
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
