/**
 * P5-S2 Phase 4 — Add / Edit Provider modal.
 *
 * UI is dumb: parent owns the editing target, parent handles save
 * (sending ws). The modal collects + validates the draft client-side
 * before letting parent commit it.
 *
 * v2 (multi-model support): `model: string` → `models: string[]` +
 * `default_model: string`. Adds inline UI for adding/removing models
 * + a "🔍 自动获取" button that asks the backend to probe `<base_url>/models`
 * and merges the result into the draft's models list.
 *
 * Pure helpers exported for vitest — matches the no-DOM testing
 * convention.
 */
import { useEffect, useMemo, useState } from "react";

import type { Provider } from "./SettingsProviders";

export interface ProviderDraft {
  id: string;
  name: string;
  base_url: string;
  models: string[];
  default_model: string;
  /** Plaintext from the input field. Empty string means "don't touch
   * the existing keychain entry" when editing. */
  api_key: string;
}

// ---- Pure validation helpers ---------------------------------------------

const KEBAB_RE = /^[a-z0-9]+(-[a-z0-9]+)*$/;

export interface ValidationResult {
  ok: boolean;
  /** Field-keyed error messages; empty when ok=true. */
  errors: Partial<Record<keyof ProviderDraft, string>>;
}

/**
 * Validate a draft client-side. `editing=true` (i.e. provider already
 * exists) skips id format check and allows empty api_key (means "leave
 * keychain alone"). When adding, api_key is required.
 */
export function validateProviderDraft(
  draft: ProviderDraft,
  opts: { editing: boolean },
): ValidationResult {
  const errors: ValidationResult["errors"] = {};
  if (!opts.editing) {
    if (!draft.id || !draft.id.trim()) {
      errors.id = "id 不能为空";
    } else if (!KEBAB_RE.test(draft.id.trim())) {
      errors.id = "id 只能是小写字母 / 数字 / 短横线（kebab-case）";
    }
  }
  if (!draft.name || !draft.name.trim()) {
    errors.name = "name 不能为空";
  }
  if (!draft.base_url || !draft.base_url.trim()) {
    errors.base_url = "base_url 不能为空";
  } else if (!/^https?:\/\//.test(draft.base_url.trim())) {
    errors.base_url = "base_url 必须 http:// 或 https:// 开头";
  }
  if (!draft.models || draft.models.length === 0) {
    errors.models = "至少配置一个 model";
  }
  if (!opts.editing) {
    if (!draft.api_key || !draft.api_key.trim()) {
      errors.api_key = "新 provider 必须填 api_key";
    }
  }
  return { ok: Object.keys(errors).length === 0, errors };
}

/**
 * Pre-fill an edit modal from an existing provider. api_key is
 * deliberately blanked: the backend only ever returned `********`,
 * and forcing the user to retype if they want to change it means
 * we never accidentally re-save the redaction sentinel as a key.
 */
export function prefillFromProvider(p: Provider): ProviderDraft {
  const models = Array.isArray(p.models) && p.models.length > 0
    ? p.models
    : (p.model ? [p.model] : []);
  const default_model = (p.default_model && models.includes(p.default_model))
    ? p.default_model
    : (models[0] || "");
  return {
    id: p.id,
    name: p.name,
    base_url: p.base_url,
    models,
    default_model,
    api_key: "",
  };
}

/**
 * Build the outbound `settings_providers_add` ws message from a draft.
 * Pure function so vitest can verify the shape.
 */
export function buildAddProviderMessage(draft: ProviderDraft): {
  type: "settings_providers_add";
  payload: {
    id: string;
    name: string;
    base_url: string;
    models: string[];
    default_model: string;
    api_key: string;
    enabled: boolean;
  };
} {
  return {
    type: "settings_providers_add",
    payload: {
      id: draft.id.trim(),
      name: draft.name.trim(),
      base_url: draft.base_url.trim(),
      models: draft.models.map((m) => m.trim()).filter(Boolean),
      default_model: draft.default_model.trim(),
      api_key: draft.api_key,
      enabled: true,
    },
  };
}

/**
 * Build the outbound `settings_providers_update` ws message from a draft.
 * Empty `api_key` is excluded from the patch so the backend keeps the
 * existing keychain entry.
 */
export function buildUpdateProviderMessage(
  id: string,
  draft: ProviderDraft,
): {
  type: "settings_providers_update";
  payload: { id: string; patch: Record<string, unknown> };
} {
  const patch: Record<string, unknown> = {
    name: draft.name.trim(),
    base_url: draft.base_url.trim(),
    models: draft.models.map((m) => m.trim()).filter(Boolean),
    default_model: draft.default_model.trim(),
  };
  if (draft.api_key && draft.api_key.trim().length > 0) {
    patch.api_key = draft.api_key.trim();
  }
  return {
    type: "settings_providers_update",
    payload: { id, patch },
  };
}

/**
 * Build the outbound `settings_providers_probe_models` ws message. The
 * backend GETs `<base_url>/models` and replies with
 * `settings_providers_probe_models_response`.
 */
export function buildProbeModelsMessage(
  base_url: string,
  api_key: string,
): {
  type: "settings_providers_probe_models";
  payload: { base_url: string; api_key: string };
} {
  return {
    type: "settings_providers_probe_models",
    payload: { base_url: base_url.trim(), api_key },
  };
}

// ---- Component -----------------------------------------------------------

interface AddProviderModalProps {
  editing: Provider | null;
  onClose(): void;
  onSave(draft: ProviderDraft): void;
  /** Send a ws probe request. Parent owns the channel. */
  onProbeModels?(base_url: string, api_key: string): void;
  /** Latest probe result from backend (managed by parent). */
  probedModels?: string[];
  /** Backend probe error (if any). */
  probeError?: string | null;
  /** Probe in-flight indicator. */
  probing?: boolean;
}

const blank_draft: ProviderDraft = {
  id: "",
  name: "",
  base_url: "",
  models: [],
  default_model: "",
  api_key: "",
};

export function AddProviderModal({
  editing,
  onClose,
  onSave,
  onProbeModels,
  probedModels,
  probeError,
  probing,
}: AddProviderModalProps) {
  const [draft, setDraft] = useState<ProviderDraft>(() =>
    editing ? prefillFromProvider(editing) : blank_draft,
  );
  const [submitted, setSubmitted] = useState(false);
  const [newModel, setNewModel] = useState("");
  const [showModels, setShowModels] = useState(true);

  // If the editing target changes (rare; UI usually re-mounts), reset draft.
  useEffect(() => {
    setDraft(editing ? prefillFromProvider(editing) : blank_draft);
    setSubmitted(false);
    setNewModel("");
  }, [editing]);

  // Merge probed models into the draft (idempotent — only adds new ones).
  useEffect(() => {
    if (!probedModels || probedModels.length === 0) return;
    setDraft((cur) => {
      const merged = [...cur.models];
      for (const m of probedModels) {
        if (m && !merged.includes(m)) merged.push(m);
      }
      const default_model = cur.default_model && merged.includes(cur.default_model)
        ? cur.default_model
        : (merged[0] || "");
      return { ...cur, models: merged, default_model };
    });
  }, [probedModels]);

  const isEditing = editing !== null;
  const validation = useMemo(
    () => validateProviderDraft(draft, { editing: isEditing }),
    [draft, isEditing],
  );

  const addModel = () => {
    const m = newModel.trim();
    if (!m || draft.models.includes(m)) return;
    const models = [...draft.models, m];
    setDraft({
      ...draft,
      models,
      default_model: draft.default_model || m,
    });
    setNewModel("");
  };

  const removeModel = (m: string) => {
    const models = draft.models.filter((x) => x !== m);
    const default_model = draft.default_model === m
      ? (models[0] || "")
      : draft.default_model;
    setDraft({ ...draft, models, default_model });
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitted(true);
    if (!validation.ok) return;
    onSave(draft);
  };

  const canProbe = !!onProbeModels && !!draft.base_url.trim();

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={isEditing ? "编辑 provider" : "添加 provider"}
      style={overlayStyle}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <form style={modalStyle} onSubmit={handleSubmit}>
        <header style={{ display: "flex", justifyContent: "space-between" }}>
          <h3 style={{ margin: 0, fontSize: 14 }}>
            {isEditing ? `编辑 provider: ${editing!.id}` : "添加 LLM Provider"}
          </h3>
          <button
            type="button"
            onClick={onClose}
            style={{
              background: "transparent",
              border: "none",
              fontSize: 14,
              cursor: "pointer",
            }}
            aria-label="关闭"
          >
            ✕
          </button>
        </header>

        <label style={fieldStyle}>
          <span>id (kebab-case)</span>
          <input
            data-testid="provider-id-input"
            disabled={isEditing}
            value={draft.id}
            onChange={(e) => setDraft({ ...draft, id: e.target.value })}
            placeholder="the relay-deepseek"
            style={inputStyle}
          />
          {submitted && validation.errors.id && (
            <span style={errStyle}>{validation.errors.id}</span>
          )}
        </label>

        <label style={fieldStyle}>
          <span>name</span>
          <input
            data-testid="provider-name-input"
            value={draft.name}
            onChange={(e) => setDraft({ ...draft, name: e.target.value })}
            placeholder="DeepSeek via Chinzy"
            style={inputStyle}
          />
          {submitted && validation.errors.name && (
            <span style={errStyle}>{validation.errors.name}</span>
          )}
        </label>

        <label style={fieldStyle}>
          <span>base_url</span>
          <input
            data-testid="provider-base-url-input"
            value={draft.base_url}
            onChange={(e) => setDraft({ ...draft, base_url: e.target.value })}
            placeholder="https://your-llm-relay.example.com/v1"
            style={inputStyle}
          />
          {submitted && validation.errors.base_url && (
            <span style={errStyle}>{validation.errors.base_url}</span>
          )}
        </label>

        <div style={fieldStyle}>
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <button
              type="button"
              onClick={() => setShowModels((v) => !v)}
              style={{
                background: "transparent",
                border: "none",
                cursor: "pointer",
                padding: 0,
                fontSize: 12,
                color: "#374151",
              }}
              aria-expanded={showModels}
              aria-label={showModels ? "收起 models" : "展开 models"}
            >
              {showModels ? "▼" : "▶"}
            </button>
            <span style={{ flex: 1 }}>
              models <em style={{ color: "#6b7280", fontSize: 10 }}>(可配置多个；默认 model 用 ◉ 标记)</em>
            </span>
            <button
              type="button"
              onClick={() => {
                if (onProbeModels && canProbe) {
                  onProbeModels(draft.base_url, draft.api_key);
                }
              }}
              disabled={!canProbe || probing}
              style={probeBtn}
              title="向 base_url/models 拉取支持的模型列表"
              data-testid="provider-probe-models-button"
            >
              {probing ? "获取中…" : "🔍 自动获取"}
            </button>
          </div>
          {showModels && (
            <div style={{ display: "grid", gap: 4, marginTop: 4 }}>
            <div
              style={{
                display: "grid",
                gap: 4,
                // Cap height for long lists (100+ models from probe), with
                // its own scroll so the modal's footer (取消/保存) stays
                // visible without endless outer scrolling.
                maxHeight: 220,
                overflowY: "auto",
                paddingRight: 2,
              }}
            >
              {draft.models.length === 0 && (
                <div style={{ fontSize: 11, color: "#9ca3af", padding: "4px 6px" }}>
                  尚未配置 model（手动添加或点 🔍 自动获取）
                </div>
              )}
              {draft.models.map((m) => (
                <div
                  key={m}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 6,
                    padding: "3px 6px",
                    border: "1px solid #e5e7eb",
                    borderRadius: 4,
                    background: draft.default_model === m ? "#eff6ff" : "white",
                  }}
                >
                  <label
                    style={{ display: "flex", alignItems: "center", gap: 4, cursor: "pointer", flex: 1 }}
                    title={draft.default_model === m ? "默认 model" : "点击设为默认"}
                  >
                    <input
                      type="radio"
                      name="default_model"
                      checked={draft.default_model === m}
                      onChange={() => setDraft({ ...draft, default_model: m })}
                      aria-label={`设 ${m} 为默认`}
                    />
                    <span style={{ overflowWrap: "anywhere", fontFamily: "monospace", fontSize: 11 }}>{m}</span>
                  </label>
                  <button
                    type="button"
                    onClick={() => removeModel(m)}
                    style={{
                      background: "transparent",
                      border: "none",
                      color: "#b91c1c",
                      cursor: "pointer",
                      fontSize: 11,
                    }}
                    aria-label={`删除 ${m}`}
                  >
                    删除
                  </button>
                </div>
              ))}
            </div>
              <div style={{ display: "flex", gap: 4 }}>
                <input
                  value={newModel}
                  onChange={(e) => setNewModel(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      addModel();
                    }
                  }}
                  placeholder="新 model 名（例：claude-sonnet-4-5）"
                  style={{ ...inputStyle, flex: 1 }}
                  data-testid="provider-new-model-input"
                />
                <button
                  type="button"
                  onClick={addModel}
                  disabled={!newModel.trim()}
                  style={smallAddBtn}
                  data-testid="provider-add-model-button"
                >
                  + 添加
                </button>
              </div>
              {probeError && (
                <div style={errStyle}>自动获取失败: {probeError}</div>
              )}
            </div>
          )}
          {submitted && validation.errors.models && (
            <span style={errStyle}>{validation.errors.models}</span>
          )}
        </div>

        <label style={fieldStyle}>
          <span>api_key {isEditing && <em style={{ fontSize: 10, color: "#6b7280" }}>(留空保留已存的 key)</em>}</span>
          <input
            data-testid="provider-api-key-input"
            type="password"
            value={draft.api_key}
            onChange={(e) => setDraft({ ...draft, api_key: e.target.value })}
            placeholder={isEditing ? "(已配置)" : "sk-..."}
            style={inputStyle}
            autoComplete="off"
          />
          {submitted && validation.errors.api_key && (
            <span style={errStyle}>{validation.errors.api_key}</span>
          )}
        </label>

        <footer style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 12 }}>
          <button type="button" onClick={onClose} style={cancelBtn}>
            取消
          </button>
          <button
            type="submit"
            data-testid="provider-save-button"
            style={saveBtn}
          >
            {isEditing ? "保存" : "添加"}
          </button>
        </footer>
      </form>
    </div>
  );
}

// ---- inline styles -------------------------------------------------------

const overlayStyle: React.CSSProperties = {
  position: "fixed",
  inset: 0,
  background: "rgba(0,0,0,0.5)",
  display: "grid",
  placeItems: "center",
  padding: 8,
  zIndex: 1100,
};

const modalStyle: React.CSSProperties = {
  background: "white",
  padding: 16,
  borderRadius: 8,
  width: "min(94vw, 440px)",
  maxHeight: "92vh",
  overflowY: "auto",
  overflowX: "hidden",
  color: "#111",
  boxShadow: "0 8px 24px rgba(0,0,0,0.25)",
  display: "grid",
  gap: 8,
};

const fieldStyle: React.CSSProperties = {
  display: "grid",
  gap: 4,
  fontSize: 12,
};

const inputStyle: React.CSSProperties = {
  padding: "5px 8px",
  borderRadius: 4,
  border: "1px solid #d1d5db",
  fontSize: 12,
  fontFamily: "inherit",
  outline: "none",
  minWidth: 0,
};

const errStyle: React.CSSProperties = {
  fontSize: 11,
  color: "#b91c1c",
};

const saveBtn: React.CSSProperties = {
  padding: "5px 12px",
  borderRadius: 4,
  border: "1px solid #2563eb",
  background: "#2563eb",
  color: "white",
  fontSize: 12,
  cursor: "pointer",
};

const cancelBtn: React.CSSProperties = {
  padding: "5px 12px",
  borderRadius: 4,
  border: "1px solid #d1d5db",
  background: "white",
  fontSize: 12,
  cursor: "pointer",
};

const smallAddBtn: React.CSSProperties = {
  padding: "3px 10px",
  borderRadius: 4,
  border: "1px solid #2563eb",
  background: "white",
  color: "#2563eb",
  fontSize: 11,
  cursor: "pointer",
};

const probeBtn: React.CSSProperties = {
  padding: "3px 8px",
  borderRadius: 4,
  border: "1px solid #d1d5db",
  background: "#f9fafb",
  color: "#374151",
  fontSize: 11,
  cursor: "pointer",
};
