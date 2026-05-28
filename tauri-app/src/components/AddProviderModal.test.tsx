// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * P5-S2 Phase 4 — AddProviderModal tests.
 *
 * Pure-helper coverage (no DOM) — see SettingsProviders.test.tsx for the
 * rationale matching the existing project convention.
 *
 * Each test name corresponds to Phase 4.3 tasks in
 * openspec/changes/multi-provider-management/tasks.md. v2 multi-model
 * schema additions are marked inline.
 */
import { describe, it, expect } from "vitest";

import {
  buildAddProviderMessage,
  buildUpdateProviderMessage,
  buildProbeModelsMessage,
  prefillFromProvider,
  validateProviderDraft,
  type ProviderDraft,
} from "./AddProviderModal";
import type { Provider } from "./SettingsProviders";

const valid_draft: ProviderDraft = {
  id: "the relay-ds",
  name: "Chinzy DeepSeek",
  base_url: "https://your-llm-relay.example.com/v1",
  models: ["deepseek-chat"],
  default_model: "deepseek-chat",
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
      { ...valid_draft, base_url: "your-llm-relay.example.com/v1" },
      { editing: false },
    );
    expect(v.ok).toBe(false);
    expect(v.errors.base_url).toMatch(/http/);
  });

  it("test_add_modal_validates_required_fields_clientside — empty models array fails", () => {
    const v = validateProviderDraft({ ...valid_draft, models: [], default_model: "" }, { editing: false });
    expect(v.ok).toBe(false);
    expect(v.errors.models).toMatch(/至少/);
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
    id: "the relay",
    name: "DeepSeek via Chinzy",
    base_url: "https://your-llm-relay.example.com/v1",
    models: ["deepseek-chat", "gpt-4o"],
    default_model: "deepseek-chat",
    api_key: "********",
    priority: 1,
    enabled: true,
  };

  it("test_edit_modal_pre_fills_existing_values_except_api_key — fields copied verbatim", () => {
    const draft = prefillFromProvider(existing);
    expect(draft.id).toBe("the relay");
    expect(draft.name).toBe("DeepSeek via Chinzy");
    expect(draft.base_url).toBe("https://your-llm-relay.example.com/v1");
    expect(draft.models).toEqual(["deepseek-chat", "gpt-4o"]);
    expect(draft.default_model).toBe("deepseek-chat");
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
      models: ["m"],
      default_model: "m",
      api_key: "",
    };
    const v = validateProviderDraft(draft, { editing: true });
    expect(v.errors.id).toBeUndefined();
  });

  it("legacy provider with only `model` field (no `models` array) backfills correctly", () => {
    const legacy_existing: Provider = {
      id: "old",
      name: "Old",
      base_url: "https://o/v1",
      models: [],
      model: "old-model",
      api_key: "********",
      priority: 1,
      enabled: true,
    } as Provider;
    const draft = prefillFromProvider(legacy_existing);
    expect(draft.models).toEqual(["old-model"]);
    expect(draft.default_model).toBe("old-model");
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
      id: "the relay-ds",
      name: "Chinzy DeepSeek",
      base_url: "https://your-llm-relay.example.com/v1",
      models: ["deepseek-chat"],
      default_model: "deepseek-chat",
      api_key: "sk-real-key",
      enabled: true,
    });
  });

  it("test_save_emits_correct_ws_message — add trims whitespace on id/name/base_url and each model", () => {
    const padded: ProviderDraft = {
      id: "  trimme  ",
      name: "  N  ",
      base_url: "  https://a/v1  ",
      models: ["  m1  ", "  m2  "],
      default_model: "  m1  ",
      api_key: "sk-x",
    };
    const msg = buildAddProviderMessage(padded);
    expect(msg.payload.id).toBe("trimme");
    expect(msg.payload.name).toBe("N");
    expect(msg.payload.base_url).toBe("https://a/v1");
    expect(msg.payload.models).toEqual(["m1", "m2"]);
    expect(msg.payload.default_model).toBe("m1");
  });

  it("test_save_emits_correct_ws_message — multi-model draft preserves order and default", () => {
    const multi: ProviderDraft = {
      ...valid_draft,
      models: ["a", "b", "c"],
      default_model: "b",
    };
    const msg = buildAddProviderMessage(multi);
    expect(msg.payload.models).toEqual(["a", "b", "c"]);
    expect(msg.payload.default_model).toBe("b");
  });

  it("test_save_emits_correct_ws_message — update with new api_key includes patch.api_key + models", () => {
    const msg = buildUpdateProviderMessage("the relay", {
      ...valid_draft,
      api_key: "sk-new",
    });
    expect(msg).toEqual({
      type: "settings_providers_update",
      payload: {
        id: "the relay",
        patch: {
          name: "Chinzy DeepSeek",
          base_url: "https://your-llm-relay.example.com/v1",
          models: ["deepseek-chat"],
          default_model: "deepseek-chat",
          api_key: "sk-new",
        },
      },
    });
  });

  it("test_save_emits_correct_ws_message — update with empty api_key OMITS patch.api_key (keychain preserved)", () => {
    const msg = buildUpdateProviderMessage("the relay", {
      ...valid_draft,
      api_key: "",
    });
    expect(msg.payload.patch).not.toHaveProperty("api_key");
    expect(msg.payload.patch.name).toBe("Chinzy DeepSeek");
    expect(msg.payload.patch.models).toEqual(["deepseek-chat"]);
  });
});

// ---------------------------------------------------------------------------
// v2: probe-models ws message shape
// ---------------------------------------------------------------------------

describe("buildProbeModelsMessage", () => {
  it("emits settings_providers_probe_models with trimmed base_url + api_key passthrough", () => {
    const m = buildProbeModelsMessage("  https://x.com/v1  ", "sk-real");
    expect(m.type).toBe("settings_providers_probe_models");
    expect(m.payload.base_url).toBe("https://x.com/v1");
    expect(m.payload.api_key).toBe("sk-real");
  });
});
