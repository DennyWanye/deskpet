/**
 * P5-S2 Phase 4 — AddProviderModal tests.
 *
 * Pure-helper coverage (no DOM) — see SettingsProviders.test.tsx for the
 * rationale matching the existing project convention.
 *
 * Each test name corresponds to Phase 4.3 tasks in
 * openspec/changes/multi-provider-management/tasks.md.
 */
import { describe, it, expect } from "vitest";

import {
  buildAddProviderMessage,
  buildUpdateProviderMessage,
  prefillFromProvider,
  validateProviderDraft,
  type ProviderDraft,
} from "./AddProviderModal";
import type { Provider } from "./SettingsProviders";

const valid_draft: ProviderDraft = {
  id: "chinzy-ds",
  name: "Chinzy DeepSeek",
  base_url: "https://chinzy.com/v1",
  model: "deepseek-chat",
  api_key: "sk-real-key",
};

// ---------------------------------------------------------------------------
// 4.8 test_add_modal_validates_required_fields_clientside
// ---------------------------------------------------------------------------

describe("validateProviderDraft (add mode)", () => {
  it("test_add_modal_validates_required_fields_clientside — valid draft passes", () => {
    const v = validateProviderDraft(valid_draft, { editing: false });
    expect(v.ok).toBe(true);
    expect(v.errors).toEqual({});
  });

  it("test_add_modal_validates_required_fields_clientside — missing id fails", () => {
    const v = validateProviderDraft({ ...valid_draft, id: "" }, { editing: false });
    expect(v.ok).toBe(false);
    expect(v.errors.id).toMatch(/不能为空/);
  });

  it("test_add_modal_validates_required_fields_clientside — non-kebab id fails", () => {
    const v = validateProviderDraft(
      { ...valid_draft, id: "Bad_ID" },
      { editing: false },
    );
    expect(v.ok).toBe(false);
    expect(v.errors.id).toMatch(/kebab/);
  });

  it("test_add_modal_validates_required_fields_clientside — empty name fails", () => {
    const v = validateProviderDraft({ ...valid_draft, name: "" }, { editing: false });
    expect(v.ok).toBe(false);
    expect(v.errors.name).toMatch(/不能为空/);
  });

  it("test_add_modal_validates_required_fields_clientside — base_url without http:// fails", () => {
    const v = validateProviderDraft(
      { ...valid_draft, base_url: "chinzy.com/v1" },
      { editing: false },
    );
    expect(v.ok).toBe(false);
    expect(v.errors.base_url).toMatch(/http/);
  });

  it("test_add_modal_validates_required_fields_clientside — empty model fails", () => {
    const v = validateProviderDraft({ ...valid_draft, model: "" }, { editing: false });
    expect(v.ok).toBe(false);
    expect(v.errors.model).toMatch(/不能为空/);
  });

  it("test_add_modal_validates_required_fields_clientside — add without api_key fails", () => {
    const v = validateProviderDraft(
      { ...valid_draft, api_key: "" },
      { editing: false },
    );
    expect(v.ok).toBe(false);
    expect(v.errors.api_key).toMatch(/api_key/);
  });
});

// ---------------------------------------------------------------------------
// 4.9 test_edit_modal_pre_fills_existing_values_except_api_key
// ---------------------------------------------------------------------------

describe("prefillFromProvider (edit mode)", () => {
  const existing: Provider = {
    id: "chinzy",
    name: "DeepSeek via Chinzy",
    base_url: "https://chinzy.com/v1",
    model: "deepseek-chat",
    api_key: "********",
    priority: 1,
    enabled: true,
  };

  it("test_edit_modal_pre_fills_existing_values_except_api_key — fields copied verbatim", () => {
    const draft = prefillFromProvider(existing);
    expect(draft.id).toBe("chinzy");
    expect(draft.name).toBe("DeepSeek via Chinzy");
    expect(draft.base_url).toBe("https://chinzy.com/v1");
    expect(draft.model).toBe("deepseek-chat");
  });

  it("test_edit_modal_pre_fills_existing_values_except_api_key — api_key blanked, never the redaction sentinel", () => {
    const draft = prefillFromProvider(existing);
    expect(draft.api_key).toBe("");
    expect(draft.api_key).not.toBe("********");
  });

  it("edit mode: validation allows empty api_key", () => {
    const draft = prefillFromProvider(existing);
    const v = validateProviderDraft(draft, { editing: true });
    expect(v.ok).toBe(true);
  });

  it("edit mode: validation skips id format check (legacy ids may pre-date the rule)", () => {
    const draft: ProviderDraft = {
      id: "Legacy_ID",
      name: "Legacy",
      base_url: "https://x.com/v1",
      model: "m",
      api_key: "",
    };
    const v = validateProviderDraft(draft, { editing: true });
    expect(v.errors.id).toBeUndefined();
  });
});

// ---------------------------------------------------------------------------
// 4.10 test_save_emits_correct_ws_message
// ---------------------------------------------------------------------------

describe("buildAddProviderMessage / buildUpdateProviderMessage", () => {
  it("test_save_emits_correct_ws_message — add emits settings_providers_add with full payload", () => {
    const msg = buildAddProviderMessage(valid_draft);
    expect(msg.type).toBe("settings_providers_add");
    expect(msg.payload).toEqual({
      id: "chinzy-ds",
      name: "Chinzy DeepSeek",
      base_url: "https://chinzy.com/v1",
      model: "deepseek-chat",
      api_key: "sk-real-key",
      enabled: true,
    });
  });

  it("test_save_emits_correct_ws_message — add trims whitespace on id/name/base_url/model", () => {
    const padded: ProviderDraft = {
      id: "  trimme  ",
      name: "  N  ",
      base_url: "  https://a/v1  ",
      model: "  m  ",
      api_key: "sk-x",
    };
    const msg = buildAddProviderMessage(padded);
    expect(msg.payload.id).toBe("trimme");
    expect(msg.payload.name).toBe("N");
    expect(msg.payload.base_url).toBe("https://a/v1");
    expect(msg.payload.model).toBe("m");
  });

  it("test_save_emits_correct_ws_message — update with new api_key includes patch.api_key", () => {
    const msg = buildUpdateProviderMessage("chinzy", {
      ...valid_draft,
      api_key: "sk-new",
    });
    expect(msg).toEqual({
      type: "settings_providers_update",
      payload: {
        id: "chinzy",
        patch: {
          name: "Chinzy DeepSeek",
          base_url: "https://chinzy.com/v1",
          model: "deepseek-chat",
          api_key: "sk-new",
        },
      },
    });
  });

  it("test_save_emits_correct_ws_message — update with empty api_key OMITS patch.api_key (keychain preserved)", () => {
    const msg = buildUpdateProviderMessage("chinzy", {
      ...valid_draft,
      api_key: "",
    });
    expect(msg.payload.patch).not.toHaveProperty("api_key");
    expect(msg.payload.patch.name).toBe("Chinzy DeepSeek");
  });
});
