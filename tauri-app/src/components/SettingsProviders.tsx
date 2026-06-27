// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * P5-S2 Phase 4 — LLM Provider settings UI (drag-drop reorderable list).
 *
 * Pairs with `LLMProviderRegistry` (backend phase 1) + the
 * `settings_providers_*` ws messages (backend phase 2). The component
 * lives inside `SettingsPanel` and talks to the control WS via
 * `getChannel()` (same pattern as the rest of SettingsPanel).
 *
 * Pure helpers below are exported so vitest can test them without
 * needing a DOM — matches the project's existing test convention
 * (see AutoResumeBanner.test.tsx / SettingsToggle.test.tsx).
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { create } from "zustand";
import {
  DndContext,
  closestCenter,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";
import {
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
  arrayMove,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";

import { AddProviderModal, type ProviderDraft } from "./AddProviderModal";
import type { ControlChannel } from "../ws/ControlChannel";
import type { IncomingMessage } from "../types/messages";
import type { RelayAuthAdapter } from "../auth/RelayAuthAdapter";
import type { Provider as RelayProvider } from "../auth/types";
import { RELAY_MANAGED_PROVIDER } from "../auth/relayConfig";
import { relayProviderRegistration } from "../auth/relayProviderRegistration";

// ---- Relay-edition virtual providers --------------------------------------
//
// 2026-05-26: 用户登录中转站后，把 relay 下发的 provider 作为只读虚拟项
// 显示在列表里（跟手动加的 provider 视觉一致）。relay 的 tsk_xxx key 不
// 落 LLMProviderRegistry（避免明文存到 settings.json），所以我们不通过
// `settings_providers_add` 真注册，而是在前端 merge 一条虚拟 Provider，
// 渲染时禁用编辑/拖拽/删除并加"中转站"徽章。

/** id 前缀 — 用来在 ordered list 里识别 relay 虚拟项。 */
export const RELAY_PROVIDER_ID_PREFIX = "__relay__:";

/** 把 auth 层的 Provider 转成 SettingsProviders 显示用的 Provider。 */
export function relayProviderToDisplay(rp: RelayProvider): Provider {
  const models = (rp.models ?? []).map((m) => m.id);
  return {
    id: `${RELAY_PROVIDER_ID_PREFIX}${rp.id}`,
    source: "relay",
    name: rp.name + " · 中转站",
    base_url: rp.base_url,
    models,
    default_model: models[0] ?? null,
    model: models[0] ?? "",
    // relay 虚拟项 key 永远显示为 ******** (sentinel)
    api_key: REDACTED_API_KEY,
    // priority 最低数 = 排最前；用 -1 保证 relay 项总在第一位
    priority: typeof rp.priority === "number" ? rp.priority - 1000 : -1,
    enabled: true,
  };
}

/** 把中转站下发的多条 relay provider 合并成**单个** "relay" 虚拟项显示。
 *
 * 中转站登录后会下发两条同源 provider —— OpenAI 协议入口（/v1）和 Anthropic
 * 原生协议入口（/anthropic/v1）。但 OpenAI /v1 入口的模型目录已经**包含全部
 * 模型（含 Claude 系）**，Anthropic /anthropic/v1 只是冗余的原生协议入口，
 * deskpet 默认走 /v1。为减少"两个一样的中转站"的困惑，列表里只显示一条
 * 合并后的 "relay"（优先用 /v1 入口，模型最全）。 */
export function relayListToProviders(list: RelayProvider[]): Provider[] {
  if (!list || list.length === 0) return [];
  // 优先 OpenAI /v1 入口（含全部模型）；找不到则退第一个。
  const primary =
    list.find(
      (p) => p.base_url.includes("/v1") && !p.base_url.includes("/anthropic"),
    ) ?? list[0];
  const disp = relayProviderToDisplay(primary);
  return [{ ...disp, name: "relay · 中转站" }];
}

/** 判断某个 Provider 是不是 relay 管理项。 */
export function isRelayProvider(p: Pick<Provider, "source">): boolean {
  return p.source === "relay";
}

// ---- Domain types ---------------------------------------------------------

export interface Provider {
  id: string;
  source?: "user" | "relay";
  account_ref?: string;
  name: string;
  base_url: string;
  /** P5-S2 v2: canonical model list (a provider can serve multiple models). */
  models: string[];
  /** P5-S2 v2: which model in `models` is used by default for chain calls. */
  default_model?: string | null;
  /** Back-compat scalar — server includes it derived from models[0]/default_model. */
  model?: string;
  /** Always `"********"` when sourced from backend list (sanitized). */
  api_key: string;
  priority: number;
  enabled: boolean;
}

// ---- Pure helpers (exported for tests) ------------------------------------

/** Backend redaction sentinel for api_key field in list responses. */
export const REDACTED_API_KEY = "********";

/** Is the api_key string the redaction sentinel (never plaintext)? */
export function isRedactedApiKey(v: unknown): boolean {
  return typeof v === "string" && v === REDACTED_API_KEY;
}

/** Display string for api_key cell — always 8 stars when not editing. */
export function displayApiKey(_p: Pick<Provider, "api_key">): string {
  // Spec: list_providers SHALL return api_key="********".
  // Regardless of what the backend sent we render the sentinel to make
  // an accidental plaintext leak in the UI impossible.
  return REDACTED_API_KEY;
}

/** Build the ws message frontend sends to reorder providers. */
export function buildReorderMessage(ordered_ids: string[]): {
  type: "settings_providers_reorder";
  payload: { ordered_ids: string[] };
} {
  return {
    type: "settings_providers_reorder",
    payload: { ordered_ids },
  };
}

/** Build the ws message frontend sends to toggle enabled. */
export function buildToggleEnabledMessage(
  id: string,
  enabled: boolean,
): { type: "settings_providers_update"; payload: { id: string; patch: { enabled: boolean } } } {
  return {
    type: "settings_providers_update",
    payload: { id, patch: { enabled } },
  };
}

/** Build the ws message frontend sends to remove a provider. */
export function buildRemoveMessage(id: string): {
  type: "settings_providers_remove";
  payload: { id: string };
} {
  return {
    type: "settings_providers_remove",
    payload: { id },
  };
}

/** Build the ws message frontend sends to request the provider list. */
export function buildListRequestMessage(): { type: "settings_providers_list_request" } {
  return { type: "settings_providers_list_request" };
}

/**
 * Apply a keyboard-driven reorder (↑ / ↓ on a focused row).
 * Pure helper so vitest can verify without a real DOM.
 * Returns the new ordered ids; if the move is a no-op (already at the
 * edge), returns the input unchanged.
 */
export function applyKeyboardReorder(
  ordered_ids: string[],
  focused_id: string,
  direction: "up" | "down",
): string[] {
  const idx = ordered_ids.indexOf(focused_id);
  if (idx < 0) return ordered_ids;
  const target = direction === "up" ? idx - 1 : idx + 1;
  if (target < 0 || target >= ordered_ids.length) return ordered_ids;
  return arrayMove(ordered_ids, idx, target);
}

/** Sort providers for display: priority asc, then name asc as tie-break. */
export function sortProvidersForDisplay(providers: Provider[]): Provider[] {
  return [...providers].sort((a, b) => {
    if (a.priority !== b.priority) return a.priority - b.priority;
    return a.name.localeCompare(b.name);
  });
}

// ---- Providers store + ws dispatcher (used by code-panel/ws.ts) -----------
//
// Providers are app-global state, but per the Phase 4 file-whitelist we
// keep the store co-located with the component that owns it. ws.ts
// imports `dispatchProviderEvent` to route the 4 new wire events here.

interface ProvidersStore {
  providers: Provider[];
  error: string | null;
}

export const useProvidersStore = create<ProvidersStore>(() => ({
  providers: [],
  error: null,
}));

/**
 * Route a backend provider-related ws message into useProvidersStore.
 * Pure side-effect on the store (no React), so vitest can drive it
 * straight from the test file without needing a DOM.
 *
 * Recognised event types:
 *   - settings_providers_list_response   { providers: [...] }
 *   - providers_changed                  { providers: [...] }
 *   - settings_providers_reordered       { providers: [...] }
 *   - settings_providers_added           { provider }
 *   - settings_providers_updated         { provider }
 *   - settings_providers_removed         { id }
 *   - settings_providers_error           { reason, detail }
 *
 * Unknown types are ignored so adding new server-side events later
 * doesn't accidentally clobber the store.
 */
export function dispatchProviderEvent(msg: {
  type: string;
  payload?: any;
}): void {
  switch (msg.type) {
    case "settings_providers_list_response":
    case "providers_changed":
    case "settings_providers_reordered": {
      const list: Provider[] = Array.isArray(msg.payload?.providers)
        ? msg.payload.providers
        : [];
      useProvidersStore.setState({ providers: list, error: null });
      break;
    }
    case "settings_providers_added": {
      const p: Provider | undefined = msg.payload?.provider;
      if (!p || !p.id) break;
      useProvidersStore.setState((state) => {
        const exists = state.providers.some((x) => x.id === p.id);
        const next = exists
          ? state.providers.map((x) => (x.id === p.id ? p : x))
          : [...state.providers, p];
        return { providers: next, error: null };
      });
      break;
    }
    case "settings_providers_updated": {
      const p: Provider | undefined = msg.payload?.provider;
      if (!p || !p.id) break;
      useProvidersStore.setState((state) => ({
        providers: state.providers.map((x) => (x.id === p.id ? p : x)),
        error: null,
      }));
      break;
    }
    case "settings_providers_removed": {
      const id: string | undefined = msg.payload?.id;
      if (!id) break;
      useProvidersStore.setState((state) => ({
        providers: state.providers.filter((x) => x.id !== id),
        error: null,
      }));
      break;
    }
    case "settings_providers_error": {
      const reason = String(msg.payload?.reason ?? "");
      const detail = String(msg.payload?.detail ?? "");
      const text = [reason, detail].filter(Boolean).join(": ");
      useProvidersStore.setState({ error: text || "未知错误" });
      break;
    }
    default:
      break;
  }
}

/** Test-only alias mirroring AutoResumeBanner's `__test_dispatch` convention. */
export const __test_dispatch_provider_event = dispatchProviderEvent;

// ---- Component ------------------------------------------------------------

interface SettingsProvidersProps {
  getChannel: () => ControlChannel | null;
  lastMessage: IncomingMessage | null;
  /** 2026-05-26: 可选的 relay adapter — 有时把中转站 providers 作为
   * 只读虚拟项 merge 进列表。OSS / manual 编辑 = null → 行为零回归。 */
  relayAdapter?: RelayAuthAdapter | null;
}

interface SortableRowProps {
  provider: Provider;
  onToggle(id: string, next_enabled: boolean): void;
  onDefaultModelChange(id: string, default_model: string): void;
  onDelete(id: string): void;
  onEdit(provider: Provider): void;
  onResetKey(id: string): void;
}

function SortableRow({
  provider,
  onToggle,
  onDefaultModelChange,
  onDelete,
  onEdit,
  onResetKey,
}: SortableRowProps) {
  const relayManaged = isRelayProvider(provider);
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } =
    useSortable({ id: provider.id, disabled: false });
  const style: React.CSSProperties = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.6 : 1,
    border: "1px solid #e5e7eb",
    borderRadius: 4,
    padding: "8px 10px",
    display: "flex",
    flexDirection: "column",
    gap: 6,
    background: provider.enabled ? "white" : "#f3f4f6",
    fontSize: 12,
    minWidth: 0,
  };
  const headerRow: React.CSSProperties = {
    display: "flex",
    alignItems: "flex-start",
    gap: 8,
    minWidth: 0,
  };
  const actionsRow: React.CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: 8,
    flexWrap: "wrap",
    justifyContent: "flex-end",
  };

  return (
    <li
      ref={setNodeRef}
      style={style}
      data-testid={`provider-row-${provider.id}`}
      aria-label={`provider ${provider.name}`}
    >
      <div style={headerRow}>
        <span
          {...attributes}
          {...listeners}
          aria-label={`拖拽 ${provider.name}`}
          style={{
            cursor: "grab",
            color: "#9ca3af",
            userSelect: "none",
            flexShrink: 0,
            lineHeight: "16px",
          }}
        >
          ⠿
        </span>
        <div style={{ minWidth: 0, flex: 1 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
            <span style={{ fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{provider.name}</span>
            {relayManaged && (
              <span
                data-testid="provider-relay-badge"
                style={{
                  fontSize: 10,
                  padding: "1px 6px",
                  borderRadius: 4,
                  background: "linear-gradient(180deg,#dbeafe,#bfdbfe)",
                  color: "#1d4ed8",
                  border: "1px solid #93c5fd",
                  fontWeight: 600,
                  whiteSpace: "nowrap",
                }}
                title="此 provider 来自中转站登录账户，由账户系统自动管理"
              >
                relay
              </span>
            )}
          </div>
          <div style={{ color: "#6b7280", fontSize: 11, overflowWrap: "anywhere" }}>
            {provider.default_model || provider.model || (provider.models && provider.models[0]) || "(no model)"}
            {provider.models && provider.models.length > 1 ? ` (+${provider.models.length - 1})` : ""}
          </div>
          <div style={{ color: "#9ca3af", fontSize: 11, overflowWrap: "anywhere" }}>
            {provider.base_url}
          </div>
          <div style={{ color: "#9ca3af", fontSize: 11 }}>
            API Key: {displayApiKey(provider)}
          </div>
        </div>
      </div>
      <div style={actionsRow}>
        {relayManaged && provider.models.length > 0 && (
          <label style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 11 }}>
            默认
            <select
              value={provider.default_model || provider.model || provider.models[0] || ""}
              onChange={(e) => onDefaultModelChange(provider.id, e.target.value)}
              style={selectStyle}
              data-testid={`provider-default-model-select-${provider.id}`}
              aria-label={`默认模型 ${provider.name}`}
            >
              {provider.models.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          </label>
        )}
        <label style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 11 }}>
          <input
            type="checkbox"
            checked={provider.enabled}
            onChange={(e) => onToggle(provider.id, e.target.checked)}
            aria-label={`启用 ${provider.name}`}
          />
          启用
        </label>
        <button
          type="button"
          onClick={() => onEdit(provider)}
          style={{ ...rowBtn }}
          data-testid={`provider-edit-btn-${provider.id}`}
        >
          编辑
        </button>
        {relayManaged ? (
          <button
            type="button"
            onClick={() => onResetKey(provider.id)}
            style={{ ...rowBtn, color: "#1d4ed8" }}
            data-testid={`provider-reset-key-btn-${provider.id}`}
            title="重新向中转站申请并写入本机 provider key"
          >
            🔄 重置 key
          </button>
        ) : (
          <button
            type="button"
            onClick={() => onDelete(provider.id)}
            style={{ ...rowBtn, color: "#b91c1c" }}
            data-testid={`provider-delete-btn-${provider.id}`}
          >
            删除
          </button>
        )}
      </div>
    </li>
  );
}

export function SettingsProviders({
  getChannel,
  lastMessage,
  relayAdapter,
}: SettingsProvidersProps) {
  const [providers, setProviders] = useState<Provider[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<Provider | null>(null);
  // 2026-05-26: relay 虚拟 providers — 来自 RelayAuthAdapter 的 in-memory
  // cache，不进 backend LLMProviderRegistry（避免 tsk_xxx key 明文落盘）。
  const [relayProviders, setRelayProviders] = useState<Provider[]>([]);

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  // Hydrate from backend on mount.
  useEffect(() => {
    const ch = getChannel();
    if (ch && ch.state === "connected") {
      ch.send(buildListRequestMessage());
    }
  }, [getChannel]);

  // 2026-05-26: 订阅 relay adapter 的 providers-updated 事件，把中转站
  // 下发的 provider 当作只读虚拟项 merge 进列表。无 adapter（OSS 默认）
  // → relayProviders 保持空 → 行为零回归。
  useEffect(() => {
    // WI-3: in managed mode the relay account is collected into the
    // backend registry as a real `relay-cloud` provider (shown via the
    // normal registry list), so the legacy frontend-only virtual
    // "relay" row + its extra `/v1/providers` fetch are retired.
    // (WI-4 will add the relay-managed readonly badge to that registry
    // row.) Flag OFF → legacy virtual row (BC).
    if (!relayAdapter || RELAY_MANAGED_PROVIDER) {
      setRelayProviders([]);
      return;
    }
    const apply = (list: RelayProvider[]) => {
      // 多条同源 relay（OpenAI /v1 + Anthropic /anthropic/v1）合并成单个 "relay"。
      setRelayProviders(relayListToProviders(list));
    };
    // 退订器先订阅事件流，再用 cached 拉一次（避免 listProviders 异步期间
    // 错过事件）
    const unsub = relayAdapter.onEvent((e) => {
      if (e.type === "providers-updated") apply(e.providers);
      if (e.type === "logout") setRelayProviders([]);
    });
    void relayAdapter
      .listProviders()
      .then(apply)
      .catch(() => {
        /* 静默 — bridge 已经处理错误并显示红 banner */
      });
    return unsub;
  }, [relayAdapter]);

  // Listen for inbound provider events on the shared lastMessage prop.
  useEffect(() => {
    if (!lastMessage) return;
    const msg = lastMessage as { type: string; payload?: any };
    switch (msg.type) {
      case "settings_providers_list_response":
      case "providers_changed":
      case "settings_providers_reordered": {
        const list: Provider[] = Array.isArray(msg.payload?.providers)
          ? msg.payload.providers
          : [];
        setProviders(list);
        setError(null);
        break;
      }
      case "settings_providers_error": {
        setError(String(msg.payload?.detail || msg.payload?.reason || "未知错误"));
        break;
      }
      default:
        break;
    }
  }, [lastMessage]);

  // 2026-05-26: 合并 backend registry + relay 虚拟项 — relay 在前
  // (因为它的 priority 是 -1000 起，sort 排首位)，再走 sort 排序。
  const ordered = useMemo(
    () => sortProvidersForDisplay([...relayProviders, ...providers]),
    [providers, relayProviders],
  );
  const ordered_ids = useMemo(() => ordered.map((p) => p.id), [ordered]);

  const send = useCallback(
    (msg: { type: string; payload?: Record<string, unknown> }) => {
      const ch = getChannel();
      if (!ch || ch.state !== "connected") {
        setError("控制通道未连接");
        return false;
      }
      ch.send(msg);
      return true;
    },
    [getChannel],
  );

  const handleDragEnd = useCallback(
    (event: DragEndEvent) => {
      const { active, over } = event;
      if (!over || active.id === over.id) return;
      const old_idx = ordered_ids.indexOf(String(active.id));
      const new_idx = ordered_ids.indexOf(String(over.id));
      if (old_idx < 0 || new_idx < 0) return;
      const next = arrayMove(ordered_ids, old_idx, new_idx);
      send(buildReorderMessage(next));
    },
    [ordered_ids, send],
  );

  const handleToggle = useCallback(
    (id: string, next_enabled: boolean) => {
      send(buildToggleEnabledMessage(id, next_enabled));
    },
    [send],
  );

  const handleDefaultModelChange = useCallback(
    (id: string, default_model: string) => {
      send({
        type: "settings_providers_update",
        payload: { id, patch: { default_model } },
      });
    },
    [send],
  );

  const handleDelete = useCallback(
    (id: string) => {
      if (!window.confirm(`确认删除 provider "${id}"？`)) return;
      send(buildRemoveMessage(id));
    },
    [send],
  );

  const handleResetKey = useCallback(
    (_id: string) => {
      if (relayAdapter) void relayProviderRegistration.recover(relayAdapter);
    },
    [relayAdapter],
  );

  const handleSaveDraft = useCallback(
    (draft: ProviderDraft, editing: Provider | null) => {
      const models = draft.models.map((m) => m.trim()).filter(Boolean);
      const default_model = draft.default_model.trim() || models[0] || "";
      if (editing) {
        // relay-managed provider 由登录自动铸 key；编辑弹窗只允许改默认模型/启用。
        const patch: Record<string, unknown> = isRelayProvider(editing)
          ? { default_model }
          : {
              name: draft.name,
              base_url: draft.base_url,
              models,
              default_model,
            };
        if (!isRelayProvider(editing) && draft.api_key && draft.api_key.trim().length > 0) {
          patch.api_key = draft.api_key.trim();
        }
        if (isRelayProvider(editing) && typeof draft.enabled === "boolean") {
          patch.enabled = draft.enabled;
        }
        send({
          type: "settings_providers_update",
          payload: { id: editing.id, patch },
        });
      } else {
        send({
          type: "settings_providers_add",
          payload: {
            id: draft.id,
            name: draft.name,
            base_url: draft.base_url,
            models,
            default_model,
            api_key: draft.api_key,
            enabled: true,
          },
        });
      }
      setAddOpen(false);
      setEditTarget(null);
    },
    [send],
  );

  // ---- Probe models flow (auto-fetch /models from base_url) ----------
  const [probedModels, setProbedModels] = useState<string[]>([]);
  const [probeError, setProbeError] = useState<string | null>(null);
  const [probing, setProbing] = useState(false);

  // Watch for the backend's probe response on the shared ws inbox.
  useEffect(() => {
    if (!lastMessage) return;
    const msg = lastMessage as { type: string; payload?: any };
    if (msg.type !== "settings_providers_probe_models_response") return;
    setProbing(false);
    if (msg.payload?.ok) {
      const list: string[] = Array.isArray(msg.payload.models) ? msg.payload.models : [];
      setProbedModels(list);
      setProbeError(null);
    } else {
      setProbedModels([]);
      setProbeError(String(msg.payload?.detail || "未知错误"));
    }
  }, [lastMessage]);

  const handleProbeModels = useCallback(
    (base_url: string, api_key: string) => {
      setProbing(true);
      setProbeError(null);
      setProbedModels([]);
      send({
        type: "settings_providers_probe_models",
        payload: { base_url, api_key },
      });
    },
    [send],
  );

  return (
    <div data-testid="settings-providers">
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: 6,
        }}
      >
        <p style={{ fontSize: 11, color: "#6b7280", margin: 0 }}>
          按优先级排列；拖拽 ⠿ 改顺序，第一个失败时自动落到下一个。
        </p>
        <button
          type="button"
          onClick={() => {
            setEditTarget(null);
            setAddOpen(true);
          }}
          style={addBtnStyle}
          data-testid="provider-add-button"
        >
          + 添加
        </button>
      </div>

      {error && (
        <div role="alert" style={errorStyle}>
          {error}
        </div>
      )}

      {ordered.length === 0 ? (
        <div style={emptyStyle}>请添加你的第一个 LLM provider</div>
      ) : (
        <DndContext
          sensors={sensors}
          collisionDetection={closestCenter}
          onDragEnd={handleDragEnd}
        >
          <SortableContext items={ordered_ids} strategy={verticalListSortingStrategy}>
            <ul style={listStyle} data-testid="provider-list">
              {ordered.map((p) => (
                <SortableRow
                  key={p.id}
                  provider={p}
                  onToggle={handleToggle}
                  onDefaultModelChange={handleDefaultModelChange}
                  onDelete={handleDelete}
                  onEdit={(prov) => {
                    setEditTarget(prov);
                    setAddOpen(true);
                  }}
                  onResetKey={handleResetKey}
                />
              ))}
            </ul>
          </SortableContext>
        </DndContext>
      )}

      {addOpen && (
        <AddProviderModal
          editing={editTarget}
          onClose={() => {
            setAddOpen(false);
            setEditTarget(null);
            setProbedModels([]);
            setProbeError(null);
          }}
          onSave={(draft) => handleSaveDraft(draft, editTarget)}
          onProbeModels={handleProbeModels}
          probedModels={probedModels}
          probeError={probeError}
          probing={probing}
        />
      )}
    </div>
  );
}

// ---- inline styles --------------------------------------------------------

const listStyle: React.CSSProperties = {
  listStyle: "none",
  margin: 0,
  padding: 0,
  display: "grid",
  gap: 6,
};

const rowBtn: React.CSSProperties = {
  padding: "3px 8px",
  borderRadius: 4,
  border: "1px solid #d1d5db",
  background: "white",
  fontSize: 11,
  cursor: "pointer",
};

const selectStyle: React.CSSProperties = {
  maxWidth: 180,
  padding: "2px 6px",
  borderRadius: 4,
  border: "1px solid #d1d5db",
  background: "white",
  fontSize: 11,
};

const addBtnStyle: React.CSSProperties = {
  padding: "4px 10px",
  borderRadius: 4,
  border: "1px solid #2563eb",
  background: "#2563eb",
  color: "white",
  fontSize: 12,
  cursor: "pointer",
};

const errorStyle: React.CSSProperties = {
  fontSize: 12,
  padding: "5px 8px",
  background: "#fef2f2",
  color: "#b91c1c",
  borderRadius: 4,
  marginBottom: 6,
};

const emptyStyle: React.CSSProperties = {
  fontSize: 12,
  padding: "12px 8px",
  background: "#f9fafb",
  color: "#6b7280",
  borderRadius: 4,
  textAlign: "center",
};
