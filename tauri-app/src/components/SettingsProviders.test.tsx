/**
 * P5-S2 Phase 4 — Settings Providers tests.
 *
 * The project's vitest environment is `node` (see vitest.config.ts), so
 * we test pure render-related helpers + ws dispatch integration rather
 * than mounting the React tree. This matches the existing convention
 * in AutoResumeBanner.test.tsx / SettingsToggle.test.tsx.
 *
 * Each test name maps 1-to-1 to the task in
 * openspec/changes/multi-provider-management/tasks.md Phase 4.
 */
import { describe, it, expect, beforeEach } from "vitest";

import {
  REDACTED_API_KEY,
  applyKeyboardReorder,
  buildListRequestMessage,
  buildRemoveMessage,
  buildReorderMessage,
  buildToggleEnabledMessage,
  displayApiKey,
  isRedactedApiKey,
  sortProvidersForDisplay,
  useProvidersStore,
  __test_dispatch_provider_event,
  type Provider,
} from "./SettingsProviders";

// ---------------------------------------------------------------------------
// 4.2 test_renders_provider_list — sorting helper drives the render.
// 4.3 test_redacted_api_key_shown_as_stars — UI never shows real key.
// ---------------------------------------------------------------------------

const sample_providers: Provider[] = [
  {
    id: "the relay",
    name: "DeepSeek via Chinzy",
    base_url: "https://your-llm-relay.example.com/v1",
    models: ["deepseek-chat"],
    default_model: "deepseek-chat",
    model: "deepseek-chat",
    api_key: "********",
    priority: 1,
    enabled: true,
  },
  {
    id: "ollama",
    name: "Local Ollama",
    base_url: "http://localhost:11434/v1",
    models: ["qwen2:7b"],
    default_model: "qwen2:7b",
    model: "qwen2:7b",
    api_key: "********",
    priority: 2,
    enabled: true,
  },
];

describe("SettingsProviders — render helpers", () => {
  it("test_renders_provider_list — sorts providers by priority for display", () => {
    const ordered = sortProvidersForDisplay([
      sample_providers[1],
      sample_providers[0],
    ]);
    expect(ordered.map((p) => p.id)).toEqual(["the relay", "ollama"]);
    expect(ordered).toHaveLength(2);
  });

  it("test_renders_provider_list — empty list renders nothing", () => {
    expect(sortProvidersForDisplay([])).toEqual([]);
  });

  it("test_redacted_api_key_shown_as_stars — displayApiKey always returns 8 stars", () => {
    expect(displayApiKey(sample_providers[0])).toBe("********");
    // Even if backend ever leaked a real key, the UI helper still
    // returns the sentinel.
    expect(displayApiKey({ api_key: "sk-leaked-12345" })).toBe("********");
    expect(REDACTED_API_KEY).toBe("********");
  });

  it("test_redacted_api_key_shown_as_stars — isRedactedApiKey recognises the sentinel", () => {
    expect(isRedactedApiKey("********")).toBe(true);
    expect(isRedactedApiKey("sk-real")).toBe(false);
    expect(isRedactedApiKey(undefined)).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// 4.5 test_drag_reorder_emits_ws_message — drag end → buildReorderMessage.
// 4.6 test_keyboard_reorder_works — applyKeyboardReorder moves rows.
// ---------------------------------------------------------------------------

describe("SettingsProviders — reorder messaging", () => {
  it("test_drag_reorder_emits_ws_message — buildReorderMessage wraps ordered_ids", () => {
    const msg = buildReorderMessage(["ollama", "the relay"]);
    expect(msg).toEqual({
      type: "settings_providers_reorder",
      payload: { ordered_ids: ["ollama", "the relay"] },
    });
  });

  it("test_keyboard_reorder_works — applyKeyboardReorder up swaps with previous", () => {
    const next = applyKeyboardReorder(["a", "b", "c"], "b", "up");
    expect(next).toEqual(["b", "a", "c"]);
  });

  it("test_keyboard_reorder_works — applyKeyboardReorder down swaps with next", () => {
    const next = applyKeyboardReorder(["a", "b", "c"], "b", "down");
    expect(next).toEqual(["a", "c", "b"]);
  });

  it("test_keyboard_reorder_works — top item ↑ is a no-op", () => {
    expect(applyKeyboardReorder(["a", "b", "c"], "a", "up")).toEqual(["a", "b", "c"]);
  });

  it("test_keyboard_reorder_works — bottom item ↓ is a no-op", () => {
    expect(applyKeyboardReorder(["a", "b", "c"], "c", "down")).toEqual(["a", "b", "c"]);
  });
});

// ---------------------------------------------------------------------------
// Misc ws builders — buildToggleEnabledMessage / buildRemoveMessage /
// buildListRequestMessage. Cheap shape tests; lock the wire format.
// ---------------------------------------------------------------------------

describe("SettingsProviders — wire format", () => {
  it("buildToggleEnabledMessage emits update with enabled patch", () => {
    expect(buildToggleEnabledMessage("the relay", false)).toEqual({
      type: "settings_providers_update",
      payload: { id: "the relay", patch: { enabled: false } },
    });
  });

  it("buildRemoveMessage emits remove with id", () => {
    expect(buildRemoveMessage("ollama")).toEqual({
      type: "settings_providers_remove",
      payload: { id: "ollama" },
    });
  });

  it("buildListRequestMessage has no payload", () => {
    expect(buildListRequestMessage()).toEqual({
      type: "settings_providers_list_request",
    });
  });
});

// ---------------------------------------------------------------------------
// 4.13 test_providers_changed_event_re_renders_settings — store update.
// 4.14 ws.ts dispatches the 4 new events into providersStore.
// ---------------------------------------------------------------------------

function resetProvidersStore() {
  useProvidersStore.setState({ providers: [], error: null });
}

describe("ws.dispatch provider events → providersStore", () => {
  beforeEach(() => {
    resetProvidersStore();
  });

  it("test_providers_changed_event_re_renders_settings — providers_changed updates store", () => {
    __test_dispatch_provider_event({
      type: "providers_changed",
      payload: { providers: sample_providers },
    });
    expect(useProvidersStore.getState().providers).toHaveLength(2);
    expect(useProvidersStore.getState().providers[0].id).toBe("the relay");
  });

  it("settings_providers_list_response populates store", () => {
    __test_dispatch_provider_event({
      type: "settings_providers_list_response",
      payload: { providers: sample_providers },
    });
    expect(useProvidersStore.getState().providers).toHaveLength(2);
    expect(useProvidersStore.getState().error).toBeNull();
  });

  it("settings_providers_error sets the error field, leaves list alone", () => {
    __test_dispatch_provider_event({
      type: "providers_changed",
      payload: { providers: sample_providers },
    });
    __test_dispatch_provider_event({
      type: "settings_providers_error",
      payload: { reason: "duplicate_id", detail: "id 'the relay' exists" },
    });
    expect(useProvidersStore.getState().error).toContain("duplicate_id");
    // List preserved so the UI doesn't blink to empty on a transient error.
    expect(useProvidersStore.getState().providers).toHaveLength(2);
  });

  it("settings_providers_added wires the new provider into the list", () => {
    __test_dispatch_provider_event({
      type: "providers_changed",
      payload: { providers: [sample_providers[0]] },
    });
    __test_dispatch_provider_event({
      type: "settings_providers_added",
      payload: { provider: sample_providers[1] },
    });
    expect(useProvidersStore.getState().providers).toHaveLength(2);
    expect(useProvidersStore.getState().providers.map((p) => p.id)).toContain("ollama");
  });
});
