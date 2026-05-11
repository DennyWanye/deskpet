/**
 * P5-S2 Phase 4 — Add / Edit Provider modal.
 *
 * UI is dumb: parent owns the editing target, parent handles save
 * (sending ws). The modal collects + validates the draft client-side
 * before letting parent commit it.
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
  model: string;
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
  if (!draft.model || !draft.model.trim()) {
    errors.model = "model 不能为空";
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
  return {
    id: p.id,
    name: p.name,
    base_url: p.base_url,
    model: p.model,
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
    model: string;
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
      model: draft.model.trim(),
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
    model: draft.model.trim(),
  };
  if (draft.api_key && draft.api_key.trim().length > 0) {
    patch.api_key = draft.api_key.trim();
  }
  return {
    type: "settings_providers_update",
    payload: { id, patch },
  };
}

// ---- Component -----------------------------------------------------------

interface AddProviderModalProps {
  editing: Provider | null;
  onClose(): void;
  onSave(draft: ProviderDraft): void;
}

const blank_draft: ProviderDraft = {
  id: "",
  name: "",
  base_url: "",
  model: "",
  api_key: "",
};

export function AddProviderModal({ editing, onClose, onSave }: AddProviderModalProps) {
  const [draft, setDraft] = useState<ProviderDraft>(() =>
    editing ? prefillFromProvider(editing) : blank_draft,
  );
  const [submitted, setSubmitted] = useState(false);

  // If the editing target changes (rare; UI usually re-mounts), reset draft.
  useEffect(() => {
    setDraft(editing ? prefillFromProvider(editing) : blank_draft);
    setSubmitted(false);
  }, [editing]);

  const isEditing = editing !== null;
  const validation = useMemo(
    () => validateProviderDraft(draft, { editing: isEditing }),
    [draft, isEditing],
  );

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitted(true);
    if (!validation.ok) return;
    onSave(draft);
  };

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
            placeholder="chinzy-deepseek"
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
            placeholder="https://chinzy.com/v1"
            style={inputStyle}
          />
          {submitted && validation.errors.base_url && (
            <span style={errStyle}>{validation.errors.base_url}</span>
          )}
        </label>

        <label style={fieldStyle}>
          <span>model</span>
          <input
            data-testid="provider-model-input"
            value={draft.model}
            onChange={(e) => setDraft({ ...draft, model: e.target.value })}
            placeholder="deepseek-chat"
            style={inputStyle}
          />
          {submitted && validation.errors.model && (
            <span style={errStyle}>{validation.errors.model}</span>
          )}
        </label>

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
