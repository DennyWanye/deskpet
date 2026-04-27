import { useCallback, useEffect, useState } from "react";
import type { ControlChannel } from "../ws/ControlChannel";
import type {
  EmbedderStatusResponse,
  IncomingMessage,
} from "../types/messages";

type Props = {
  getChannel: () => ControlChannel | null;
};

// EmbedderStatusCard — P4-S16: SettingsPanel 内嵌卡片，显示当前 BGE-M3
// embedder 状态。后端 handler `embedder_status` 返回 is_ready/is_mock/
// model_path/reason；UI 据此分三档渲染：
//   - is_ready=true && is_mock=false → 绿色「BGE-M3 已就绪」
//   - is_ready=true && is_mock=true  → 黄色「Mock 模式」+ 下载提示
//   - is_ready=false 或服务未注册   → 灰色「加载中…」+ reason
//
// 跟 SettingsPanel 的 inline-style 风格保持一致（不引 CSS-in-JS 库）。
type Status =
  | { kind: "loading" }
  | { kind: "real"; modelPath: string }
  | { kind: "mock"; modelPath: string }
  | { kind: "error"; reason: string };

export function EmbedderStatusCard({ getChannel }: Props) {
  const [status, setStatus] = useState<Status>({ kind: "loading" });

  const refresh = useCallback(() => {
    const ch = getChannel();
    if (!ch) {
      setStatus({ kind: "error", reason: "control channel unavailable" });
      return;
    }
    setStatus({ kind: "loading" });
    ch.send({ type: "embedder_status", payload: {} });
  }, [getChannel]);

  // 订阅 embedder_status_response —— 同 ControlChannel.onMessage 广播路径，
  // 不会偷走 App.tsx 主分发逻辑。
  useEffect(() => {
    const ch = getChannel();
    if (!ch) return;
    const unsub = ch.onMessage((msg: IncomingMessage) => {
      if (msg.type !== "embedder_status_response") return;
      const m = msg as EmbedderStatusResponse;
      const p = m.payload;
      if (p.reason) {
        setStatus({ kind: "error", reason: p.reason });
        return;
      }
      if (!p.is_ready) {
        setStatus({ kind: "loading" });
        return;
      }
      setStatus(
        p.is_mock
          ? { kind: "mock", modelPath: p.model_path }
          : { kind: "real", modelPath: p.model_path },
      );
    });
    refresh();
    return unsub;
  }, [getChannel, refresh]);

  return (
    <div
      data-testid="embedder-status-card"
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
          marginBottom: "4px",
        }}
      >
        <strong style={{ fontSize: "12px" }}>BGE-M3 语义嵌入</strong>
        <button
          data-testid="embedder-status-refresh"
          onClick={refresh}
          style={{
            background: "transparent",
            color: "#cbd5e1",
            border: "1px solid #475569",
            borderRadius: "3px",
            padding: "1px 6px",
            fontSize: "10px",
            cursor: "pointer",
          }}
        >
          刷新
        </button>
      </div>

      {status.kind === "loading" && (
        <Badge color="#64748b" label="加载中…" />
      )}

      {status.kind === "real" && (
        <>
          <Badge color="#10b981" label="BGE-M3 已就绪 ✓" />
          <Hint>语义搜索完整激活（向量召回 + 跨语言）。</Hint>
          <PathLine path={status.modelPath} />
        </>
      )}

      {status.kind === "mock" && (
        <>
          <Badge color="#f59e0b" label="Mock 模式 ⚠" />
          <Hint>
            BGE-M3 模型未加载，语义搜索能力受限（仅关键词 / 历史回忆）。
            运行 <code style={codeStyle}>python backend/scripts/download_bge_m3.py</code>{" "}
            下载真实模型（约 2.3GB）。
          </Hint>
          <PathLine path={status.modelPath} />
        </>
      )}

      {status.kind === "error" && (
        <>
          <Badge color="#94a3b8" label="未启动" />
          <Hint>后端提示：{status.reason}</Hint>
        </>
      )}
    </div>
  );
}

// --- helpers --------------------------------------------------------------

function Badge({ color, label }: { color: string; label: string }) {
  return (
    <span
      style={{
        display: "inline-block",
        background: color,
        color: "white",
        padding: "2px 8px",
        borderRadius: "10px",
        fontSize: "11px",
        fontWeight: 600,
        marginRight: "6px",
      }}
    >
      {label}
    </span>
  );
}

function Hint({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        fontSize: "11px",
        color: "#cbd5e1",
        marginTop: "4px",
        lineHeight: 1.5,
      }}
    >
      {children}
    </div>
  );
}

function PathLine({ path }: { path: string }) {
  if (!path) return null;
  // 路径太长时截断中间，保留头尾两端最相关的部分。
  const display = path.length > 64 ? `${path.slice(0, 22)} … ${path.slice(-32)}` : path;
  return (
    <div
      style={{
        fontSize: "10px",
        color: "#64748b",
        marginTop: "3px",
        fontFamily: "monospace",
        wordBreak: "break-all",
      }}
      title={path}
    >
      {display}
    </div>
  );
}

const codeStyle: React.CSSProperties = {
  background: "rgba(15,23,42,0.6)",
  padding: "1px 4px",
  borderRadius: "3px",
  fontSize: "10px",
  fontFamily: "monospace",
};
