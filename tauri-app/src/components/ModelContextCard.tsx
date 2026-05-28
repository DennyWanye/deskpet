// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { useCallback, useEffect, useState } from "react";
import type { ControlChannel } from "../ws/ControlChannel";
import type {
  IncomingMessage,
  ModelContextGetResponse,
  ModelContextResolved,
  ModelContextSetAck,
} from "../types/messages";

type Props = {
  getChannel: () => ControlChannel | null;
};

// ---------------------------------------------------------------------------
// Pure ws message builders + edit→fields 解析。导出供 vitest（node env，不
// mount React，同 SettingsProviders.test.tsx 约定）单测线格式。
// ---------------------------------------------------------------------------

export function buildModelContextGetMessage(model: string) {
  return { type: "model_context_get" as const, payload: { model } };
}

export function buildModelContextSetMessage(
  scope: "global" | "project",
  model: string,
  fields: Partial<{
    context_window: number;
    effective_pct: number;
    compact_at_pct: number;
    recall_sweet_tokens: number;
  }>,
  projectRoot?: string,
) {
  return {
    type: "model_context_set" as const,
    payload: {
      scope,
      model,
      fields,
      ...(projectRoot ? { project_root: projectRoot } : {}),
    },
  };
}

/** 把受控输入字符串解析成合法的 fields（白名单 + 范围校验）。
 *  返回 null 表示无有效改动（UI 据此提示，不发 ws）。 */
export function parseModelContextEdits(
  windowEdit: string,
  compactEdit: string,
): { context_window?: number; compact_at_pct?: number } | null {
  const fields: { context_window?: number; compact_at_pct?: number } = {};
  const w = Number(windowEdit);
  const c = Number(compactEdit);
  if (Number.isFinite(w) && w > 0) fields.context_window = Math.round(w);
  if (Number.isFinite(c) && c > 0 && c <= 1) fields.compact_at_pct = c;
  return Object.keys(fields).length === 0 ? null : fields;
}

// ModelContextCard — Phase 1.1.6（context-1m-rearch）：SettingsPanel 内嵌
// 「模型上下文」卡片。后端 p4_ipc handler:
//   model_context_get → 当前 model 三层 resolve 结果 + builtin 全表
//   model_context_set → 把字段覆盖写回 global / project TOML
//
// 渲染：
//   - 下拉选 builtin 模型
//   - 显示 resolved window / compact_at_pct / source（来源链）
//   - 就地编辑 context_window + compact_at_pct，保存到 global（默认）
//
// 跟 EmbedderStatusCard 同样的 ws message → response → render 路径，
// 不偷 App.tsx 主分发（用 ch.onMessage 旁路订阅）。
type State =
  | { kind: "loading" }
  | {
      kind: "ready";
      model: string;
      resolved: ModelContextResolved;
      models: string[];
    }
  | { kind: "error"; reason: string };

export function ModelContextCard({ getChannel }: Props) {
  const [state, setState] = useState<State>({ kind: "loading" });
  const [model, setModel] = useState<string>("deepseek-v4-pro");
  // 编辑缓冲（字符串便于受控输入；保存时解析为数值）
  const [windowEdit, setWindowEdit] = useState<string>("");
  const [compactEdit, setCompactEdit] = useState<string>("");
  const [saveMsg, setSaveMsg] = useState<string | null>(null);

  const requestGet = useCallback(
    (m: string) => {
      const ch = getChannel();
      if (!ch) {
        setState({ kind: "error", reason: "控制通道未连接" });
        return;
      }
      setState({ kind: "loading" });
      ch.send(buildModelContextGetMessage(m));
    },
    [getChannel],
  );

  useEffect(() => {
    const ch = getChannel();
    if (!ch) return;
    const unsub = ch.onMessage((msg: IncomingMessage) => {
      if (msg.type === "model_context_get_response") {
        const m = msg as ModelContextGetResponse;
        const p = m.payload;
        if (p.reason || !p.resolved || !("context_window" in p.resolved)) {
          setState({
            kind: "error",
            reason: p.reason || "解析失败",
          });
          return;
        }
        const resolved = p.resolved as ModelContextResolved;
        const models = Object.keys(p.builtin || {}).filter(
          (k) => k !== "_default",
        );
        setState({ kind: "ready", model: p.model, resolved, models });
        setWindowEdit(String(resolved.context_window));
        setCompactEdit(String(resolved.compact_at_pct));
      } else if (msg.type === "model_context_set_ack") {
        const a = msg as ModelContextSetAck;
        if (a.payload.ok) {
          setSaveMsg("已保存，已重新解析");
          requestGet(model);
        } else {
          setSaveMsg(`保存失败：${a.payload.reason || "未知错误"}`);
        }
      }
    });
    requestGet(model);
    return unsub;
    // model 变化时重新拉取在 onModelChange 里手动触发，这里只挂一次。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [getChannel]);

  const onModelChange = useCallback(
    (m: string) => {
      setModel(m);
      setSaveMsg(null);
      requestGet(m);
    },
    [requestGet],
  );

  const onSave = useCallback(() => {
    const ch = getChannel();
    if (!ch) {
      setSaveMsg("控制通道未连接");
      return;
    }
    const fields = parseModelContextEdits(windowEdit, compactEdit);
    if (fields === null) {
      setSaveMsg("无有效改动（window 需 >0，compact_at_pct 需 0–1）");
      return;
    }
    setSaveMsg("保存中…");
    ch.send(buildModelContextSetMessage("global", model, fields));
  }, [getChannel, model, windowEdit, compactEdit]);

  return (
    <div
      data-testid="model-context-card"
      style={{
        border: "1px solid #2d3748",
        borderRadius: "6px",
        padding: "8px 10px",
        marginTop: "8px",
        background: "rgba(15,23,42,0.4)",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginBottom: "6px",
        }}
      >
        <strong style={{ fontSize: "12px" }}>模型上下文窗口</strong>
        <select
          data-testid="model-context-select"
          value={model}
          onChange={(e) => onModelChange(e.target.value)}
          style={{
            background: "rgba(15,23,42,0.6)",
            color: "#cbd5e1",
            border: "1px solid #475569",
            borderRadius: "3px",
            padding: "1px 4px",
            fontSize: "11px",
          }}
        >
          {state.kind === "ready" && state.models.length > 0 ? (
            state.models.map((m) => <option key={m}>{m}</option>)
          ) : (
            <option>{model}</option>
          )}
        </select>
      </div>

      {state.kind === "loading" && (
        <div style={{ fontSize: "11px", color: "#64748b" }}>加载中…</div>
      )}

      {state.kind === "error" && (
        <div style={{ fontSize: "11px", color: "#f59e0b" }}>
          后端提示：{state.reason}
        </div>
      )}

      {state.kind === "ready" && (
        <>
          <div
            style={{ fontSize: "11px", color: "#cbd5e1", lineHeight: 1.6 }}
          >
            <div>
              生效窗口：
              <strong>
                {" "}
                {state.resolved.context_window.toLocaleString()} tokens
              </strong>
            </div>
            <div>
              compaction 触发：{(state.resolved.compact_at_pct * 100).toFixed(0)}
              %（≈
              {Math.round(
                state.resolved.context_window * state.resolved.compact_at_pct,
              ).toLocaleString()}{" "}
              tokens）
            </div>
            <div>
              来源链：
              <SourceBadge source={state.resolved.source} />
            </div>
          </div>

          <div
            style={{
              marginTop: "8px",
              display: "grid",
              gridTemplateColumns: "auto 1fr",
              gap: "4px 8px",
              alignItems: "center",
              fontSize: "11px",
            }}
          >
            <label htmlFor="mc-window">context_window</label>
            <input
              id="mc-window"
              data-testid="model-context-window-input"
              value={windowEdit}
              onChange={(e) => setWindowEdit(e.target.value)}
              inputMode="numeric"
              style={inputStyle}
            />
            <label htmlFor="mc-compact">compact_at_pct</label>
            <input
              id="mc-compact"
              data-testid="model-context-compact-input"
              value={compactEdit}
              onChange={(e) => setCompactEdit(e.target.value)}
              inputMode="decimal"
              style={inputStyle}
            />
          </div>

          <div
            style={{
              marginTop: "6px",
              display: "flex",
              alignItems: "center",
              gap: "8px",
            }}
          >
            <button
              data-testid="model-context-save"
              onClick={onSave}
              style={{
                background: "#10b981",
                color: "white",
                border: "none",
                borderRadius: "3px",
                padding: "2px 10px",
                fontSize: "11px",
                cursor: "pointer",
              }}
            >
              保存到全局
            </button>
            {saveMsg && (
              <span style={{ fontSize: "10px", color: "#94a3b8" }}>
                {saveMsg}
              </span>
            )}
          </div>
          <div
            style={{
              fontSize: "10px",
              color: "#64748b",
              marginTop: "6px",
              lineHeight: 1.5,
            }}
          >
            写回 %APPDATA%/deskpet/model_overrides.toml。切模型零配置编辑——
            ContextManager 阈值随窗口自动伸缩（v2）。
          </div>
        </>
      )}
    </div>
  );
}

function SourceBadge({ source }: { source: string }) {
  const color =
    source === "project"
      ? "#10b981"
      : source === "global"
        ? "#f59e0b"
        : "#64748b";
  const label =
    source === "project"
      ? "项目覆盖"
      : source === "global"
        ? "全局覆盖"
        : "内置默认";
  return (
    <span
      style={{
        display: "inline-block",
        background: color,
        color: "white",
        padding: "1px 6px",
        borderRadius: "8px",
        fontSize: "10px",
        fontWeight: 600,
        marginLeft: "4px",
      }}
    >
      {label}（{source}）
    </span>
  );
}

const inputStyle: React.CSSProperties = {
  background: "rgba(15,23,42,0.6)",
  color: "#e2e8f0",
  border: "1px solid #475569",
  borderRadius: "3px",
  padding: "2px 6px",
  fontSize: "11px",
  fontFamily: "monospace",
};
