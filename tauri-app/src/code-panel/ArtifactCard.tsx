// WI-T1.4/T1.7 — ArtifactCard：按 kind 分发的产物卡片 + action 埋点
//
// PRD §3 D2：MessageBubble.tsx 的 ToolResultCard 在 result 含 artifacts[]
// 时分发到此组件；否则保留旧 ToolResultCard（字节级一致，TG-5 T5-5）。
//
// TDD §B TG-5 + TG-3 + MR-1/MR-22。
import { useCallback, useMemo, useState } from "react";
import { invoke } from "@tauri-apps/api/core";

// ─── Data types (与 backend/deskpet/tools/artifact.py 对应) ─

export type ArtifactKind = "file" | "url" | "text" | "image" | "table";
export type ActionId = "open" | "show_in_folder" | "copy_path" | "save_as" | "preview";

export interface ArtifactAction {
  id: ActionId;
  label?: string;
}

export interface ToolArtifact {
  kind: ArtifactKind;
  path?: string | null;
  url?: string | null;
  mime?: string | null;
  title: string;
  preview?: string | null;
  size_bytes?: number | null;
  sha256?: string | null;
  created_at?: string;
  actions?: ArtifactAction[];
}

// ─── Metric event helper (WI-T1.7 埋点) ─────────────────────

declare global {
  interface Window {
    __deskpet_metrics_emit?: (event: string, payload: Record<string, unknown>) => void;
  }
}

function emitArtifactAction(actionId: ActionId, toolName: string, ok: boolean) {
  try {
    // 走全局 metric sink（main.tsx 启动期注入；缺则静默丢，dev/test 友好）
    window.__deskpet_metrics_emit?.("artifact_action", {
      action_id: actionId,
      tool_name: toolName,
      ok,
      // **不**含 path —— MR-22 脱敏要求
    });
  } catch (e) {
    // 埋点失败永不破 UI 操作
    // eslint-disable-next-line no-console
    console.warn("[ArtifactCard] metric emit failed:", e);
  }
}

// ─── Tauri invoke 桥 (D3 4 commands) ──────────────────────────

async function invokeArtifactOpen(path: string, toolName: string) {
  try {
    await invoke("artifact_open", { path });
    emitArtifactAction("open", toolName, true);
  } catch (e) {
    emitArtifactAction("open", toolName, false);
    throw e;
  }
}

async function invokeShowInFolder(path: string, toolName: string) {
  try {
    await invoke("artifact_show_in_folder", { path });
    emitArtifactAction("show_in_folder", toolName, true);
  } catch (e) {
    emitArtifactAction("show_in_folder", toolName, false);
    throw e;
  }
}

async function invokeCopyPath(path: string, toolName: string) {
  try {
    await invoke("artifact_copy_path", { path });
    emitArtifactAction("copy_path", toolName, true);
  } catch (e) {
    emitArtifactAction("copy_path", toolName, false);
    throw e;
  }
}

async function invokeSaveAs(src: string, suggestedName: string, toolName: string) {
  try {
    const dest = await invoke<string | null>("artifact_save_as", {
      src,
      suggestedName,
    });
    emitArtifactAction("save_as", toolName, dest !== null);
    return dest;
  } catch (e) {
    emitArtifactAction("save_as", toolName, false);
    throw e;
  }
}

// ─── Styles ──────────────────────────────────────────────────

const cardStyle: React.CSSProperties = {
  border: "1px solid #475569",
  borderRadius: 6,
  padding: "8px 12px",
  marginTop: 4,
  background: "#1e293b",
  fontSize: 13,
};

const titleStyle: React.CSSProperties = {
  fontWeight: 600,
  color: "#e2e8f0",
  marginBottom: 4,
  wordBreak: "break-all",
};

const metaStyle: React.CSSProperties = {
  color: "#94a3b8",
  fontSize: 11,
  marginBottom: 6,
};

const actionRowStyle: React.CSSProperties = {
  display: "flex",
  gap: 6,
  flexWrap: "wrap",
  marginTop: 6,
};

const buttonStyle: React.CSSProperties = {
  padding: "3px 10px",
  fontSize: 12,
  border: "1px solid #64748b",
  borderRadius: 4,
  background: "#334155",
  color: "#e2e8f0",
  cursor: "pointer",
};

// ─── Kind icon (MIME → emoji) ─────────────────────────────────

function mimeIcon(mime?: string | null, kind?: ArtifactKind): string {
  if (kind === "url") return "🔗";
  if (kind === "image") return "🖼️";
  if (kind === "text") return "📝";
  if (kind === "table") return "📊";
  if (!mime) return "📄";
  if (mime.includes("presentation")) return "📊";
  if (mime.includes("spreadsheet")) return "📈";
  if (mime.includes("wordprocessing") || mime.includes("msword")) return "📝";
  if (mime.includes("pdf")) return "📕";
  if (mime.startsWith("image/")) return "🖼️";
  return "📄";
}

function humanSize(bytes?: number | null): string {
  if (bytes == null) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

// ─── Sub-cards by kind ───────────────────────────────────────

function FileArtifactCard({ artifact, toolName }: { artifact: ToolArtifact; toolName: string }) {
  const path = artifact.path || "";
  const actions = useMemo<ArtifactAction[]>(
    () => artifact.actions ?? [
      { id: "open", label: "打开" },
      { id: "show_in_folder", label: "在文件夹中显示" },
      { id: "copy_path", label: "复制路径" },
      { id: "save_as", label: "另存为" },
    ],
    [artifact.actions],
  );
  const handle = useCallback(
    (id: ActionId) => {
      if (!path) return;
      if (id === "open") void invokeArtifactOpen(path, toolName);
      else if (id === "show_in_folder") void invokeShowInFolder(path, toolName);
      else if (id === "copy_path") void invokeCopyPath(path, toolName);
      else if (id === "save_as")
        void invokeSaveAs(path, artifact.title || "untitled", toolName);
    },
    [path, artifact.title, toolName],
  );
  return (
    <div style={cardStyle} data-testid="artifact-card-file">
      <div style={titleStyle}>
        {mimeIcon(artifact.mime, "file")} {artifact.title}
      </div>
      <div style={metaStyle}>
        {artifact.mime ?? "file"} · {humanSize(artifact.size_bytes)}
      </div>
      <div style={actionRowStyle}>
        {actions.map((a) => (
          <button
            key={a.id}
            type="button"
            style={buttonStyle}
            data-testid={`artifact-action-${a.id}`}
            onClick={() => handle(a.id)}
          >
            {a.label || a.id}
          </button>
        ))}
      </div>
    </div>
  );
}

function UrlArtifactCard({ artifact, toolName }: { artifact: ToolArtifact; toolName: string }) {
  const url = artifact.url || "";
  return (
    <div style={cardStyle} data-testid="artifact-card-url">
      <div style={titleStyle}>
        🔗 <a href={url} target="_blank" rel="noopener noreferrer" style={{ color: "#7dd3fc" }}>
          {artifact.title || url}
        </a>
      </div>
      <div style={metaStyle}>{url}</div>
      <div style={actionRowStyle}>
        <button
          type="button"
          style={buttonStyle}
          data-testid="artifact-action-copy_path"
          onClick={() => {
            void navigator.clipboard.writeText(url).then(
              () => emitArtifactAction("copy_path", toolName, true),
              () => emitArtifactAction("copy_path", toolName, false),
            );
          }}
        >
          复制链接
        </button>
      </div>
    </div>
  );
}

function TextArtifactCard({ artifact, toolName }: { artifact: ToolArtifact; toolName: string }) {
  const [open, setOpen] = useState(true);
  const preview = artifact.preview ?? "";
  return (
    <div style={cardStyle} data-testid="artifact-card-text">
      <div style={titleStyle}>
        📝 {artifact.title}
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          style={{ ...buttonStyle, marginLeft: 8, padding: "1px 8px", fontSize: 11 }}
        >
          {open ? "收起" : "展开"}
        </button>
      </div>
      {open && (
        <pre
          data-bp-selectable=""
          style={{
            color: "#e2e8f0",
            fontSize: 12,
            background: "#0f172a",
            padding: 8,
            borderRadius: 4,
            maxHeight: 320,
            overflowY: "auto",
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
          }}
        >
          {preview}
        </pre>
      )}
      <div style={actionRowStyle}>
        <button
          type="button"
          style={buttonStyle}
          data-testid="artifact-action-copy_path"
          onClick={() => {
            void navigator.clipboard.writeText(preview).then(
              () => emitArtifactAction("copy_path", toolName, true),
              () => emitArtifactAction("copy_path", toolName, false),
            );
          }}
        >
          复制内容
        </button>
      </div>
    </div>
  );
}

function ImageArtifactCard({ artifact, toolName }: { artifact: ToolArtifact; toolName: string }) {
  // 与 file 类似，但显示更简洁
  const path = artifact.path || "";
  return (
    <div style={cardStyle} data-testid="artifact-card-image">
      <div style={titleStyle}>
        🖼️ {artifact.title}
      </div>
      <div style={metaStyle}>
        {artifact.mime ?? "image"} · {humanSize(artifact.size_bytes)}
      </div>
      <div style={actionRowStyle}>
        <button
          type="button"
          style={buttonStyle}
          data-testid="artifact-action-open"
          onClick={() => path && void invokeArtifactOpen(path, toolName)}
        >
          打开
        </button>
        <button
          type="button"
          style={buttonStyle}
          data-testid="artifact-action-show_in_folder"
          onClick={() => path && void invokeShowInFolder(path, toolName)}
        >
          在文件夹中显示
        </button>
      </div>
    </div>
  );
}

function TableArtifactCard({ artifact }: { artifact: ToolArtifact; toolName: string }) {
  return (
    <div style={cardStyle} data-testid="artifact-card-table">
      <div style={titleStyle}>📊 {artifact.title}</div>
      <pre
        data-bp-selectable=""
        style={{
          color: "#e2e8f0",
          fontSize: 11,
          background: "#0f172a",
          padding: 6,
          borderRadius: 4,
          overflowX: "auto",
          maxHeight: 240,
          whiteSpace: "pre",
        }}
      >
        {artifact.preview ?? "(no preview)"}
      </pre>
    </div>
  );
}

// ─── Dispatcher ──────────────────────────────────────────────

export function ArtifactCard({
  artifact,
  toolName,
}: {
  artifact: ToolArtifact;
  toolName: string;
}) {
  switch (artifact.kind) {
    case "file":
      return <FileArtifactCard artifact={artifact} toolName={toolName} />;
    case "url":
      return <UrlArtifactCard artifact={artifact} toolName={toolName} />;
    case "text":
      return <TextArtifactCard artifact={artifact} toolName={toolName} />;
    case "image":
      return <ImageArtifactCard artifact={artifact} toolName={toolName} />;
    case "table":
      return <TableArtifactCard artifact={artifact} toolName={toolName} />;
    default:
      // 未知 kind 退化为 file 兜底
      return <FileArtifactCard artifact={artifact} toolName={toolName} />;
  }
}

// ─── Result 解析：从 tool envelope 字符串提取 artifacts ────────

export function extractArtifactsFromResult(resultRaw: string): ToolArtifact[] {
  try {
    const obj = JSON.parse(resultRaw);
    if (obj && typeof obj === "object") {
      // 优先 envelope.artifacts（registry 包装后）
      if (Array.isArray(obj.artifacts)) {
        return obj.artifacts as ToolArtifact[];
      }
      // 兜底：从工具自身的 result.artifacts 嵌套（dry_run 等场景）
      if (typeof obj.result === "string") {
        try {
          const inner = JSON.parse(obj.result);
          if (inner && Array.isArray(inner.artifacts)) {
            return inner.artifacts as ToolArtifact[];
          }
        } catch {
          /* ignore */
        }
      }
    }
  } catch {
    return [];
  }
  return [];
}
