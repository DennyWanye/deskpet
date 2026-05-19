/**
 * multi-provider-management Phase 5 — SessionGridView per-session provider
 * dropdown tests.
 *
 * Vitest runs under the `node` environment (see vitest.config.ts) — no DOM,
 * no React renderer. We therefore test the *behavior* of the dropdown by:
 *
 *   1. Pure-function helpers exported from SessionGridView.tsx / providersStore.ts
 *      (option building, label formatting, message construction).
 *   2. ws.ts dispatch → sessionsStore + providersStore state transitions
 *      that the dropdown subscribes to.
 *
 * Each "test_*" name is the verbatim task pointer from the lead-agent brief.
 */
import { describe, it, expect, beforeEach } from "vitest";

import {
  buildSetProviderMessage,
  buildSetModelMessage,
  resolveCardDropdownDisplay,
  pickProviderRemovedFallback,
} from "./SessionGridView";
import {
  useCodeModelsStore,
  capsForModel,
  buildModelOptionsFromCatalog,
  type CatalogModel,
} from "./codeModelsStore";
import {
  build_provider_dropdown_options,
  format_provider_label,
  useProvidersStore,
  type ProviderEntry,
} from "./providersStore";
import { __test_dispatch } from "./ws";
import { useSessionsStore } from "../stores/sessionsStore";

function resetStores() {
  useSessionsStore.setState((s) => ({
    ...s,
    sessions: {
      default: {
        ...s.sessions.default,
        provider_id: null,
        preferred_model: null,
        messages: [],
      },
    },
    active_sid: "default",
  }));
  useProvidersStore.setState({ providers: [] });
}

const sample_providers: ProviderEntry[] = [
  { id: "chinzy", name: "Chinzy", enabled: true, priority: 1 },
  { id: "openrouter-claude", name: "OpenRouter Claude", enabled: true, priority: 2 },
  { id: "ollama-disabled", name: "Local Ollama", enabled: false, priority: 9 },
];

// ----- 5.1 card renders provider dropdown ----------------------------------

describe("test_card_renders_provider_dropdown", () => {
  beforeEach(resetStores);

  it("builds dropdown options with Global Chain first then enabled providers", () => {
    const opts = build_provider_dropdown_options(sample_providers);
    expect(opts[0]).toEqual({ value: null, label: "Global Chain" });
    expect(opts.map((o) => o.value)).toEqual([null, "chinzy", "openrouter-claude"]);
    // disabled provider is dropped
    expect(opts.find((o) => o.value === "ollama-disabled")).toBeUndefined();
  });

  it("returns just Global Chain when providers list is empty", () => {
    const opts = build_provider_dropdown_options([]);
    expect(opts).toEqual([{ value: null, label: "Global Chain" }]);
  });
});

// ----- 5.2 default dropdown value is Global Chain --------------------------

describe("test_default_dropdown_value_is_global_chain", () => {
  beforeEach(resetStores);

  it("unbound session (provider_id == null) → display label = Global Chain", () => {
    const display = resolveCardDropdownDisplay(null, sample_providers);
    expect(display.label).toBe("Global Chain");
    expect(display.locked).toBe(false);
  });

  it("unbound session (provider_id == undefined) → display label = Global Chain", () => {
    const display = resolveCardDropdownDisplay(undefined, sample_providers);
    expect(display.label).toBe("Global Chain");
    expect(display.locked).toBe(false);
  });

  it("blank store session has provider_id null by default", () => {
    useSessionsStore.getState().ensure("fresh-sid");
    const sess = useSessionsStore.getState().sessions["fresh-sid"];
    expect(sess?.provider_id ?? null).toBeNull();
  });
});

// ----- 5.3 select provider emits ws set message ----------------------------

describe("test_select_provider_emits_ws_set_message", () => {
  beforeEach(resetStores);

  it("buildSetProviderMessage produces correct shape for pin-to-provider", () => {
    const msg = buildSetProviderMessage("session-x", "openrouter-claude");
    expect(msg).toEqual({
      type: "code_session_set_provider",
      payload: { session_id: "session-x", provider_id: "openrouter-claude" },
    });
  });

  it("buildSetProviderMessage with null provider_id clears the binding", () => {
    const msg = buildSetProviderMessage("session-x", null);
    expect(msg).toEqual({
      type: "code_session_set_provider",
      payload: { session_id: "session-x", provider_id: null },
    });
  });
});

// ----- 5.4 pinned session shows lock icon ----------------------------------

describe("test_pinned_session_shows_lock_icon", () => {
  beforeEach(resetStores);

  it("provider_id != null → display.locked = true + lock indicator in label", () => {
    const display = resolveCardDropdownDisplay("chinzy", sample_providers);
    expect(display.locked).toBe(true);
    expect(display.label).toBe("Chinzy");
    expect(display.icon).toBe("🔒");
  });

  it("provider_id == null → no lock icon", () => {
    const display = resolveCardDropdownDisplay(null, sample_providers);
    expect(display.locked).toBe(false);
    expect(display.icon).toBe("");
  });

  it("format_provider_label falls back to provider id when name not in list", () => {
    const label = format_provider_label("ghost-provider", sample_providers);
    expect(label).toBe("ghost-provider");
  });
});

// ----- 5.5 provider removed → card falls back to global chain + toast ------

describe("test_provider_removed_falls_card_to_global_with_toast", () => {
  beforeEach(resetStores);

  it("pickProviderRemovedFallback returns null provider_id + toast text", () => {
    const fallback = pickProviderRemovedFallback("vanished-provider");
    expect(fallback.provider_id).toBeNull();
    expect(fallback.toast).toContain("vanished-provider");
    expect(fallback.toast).toContain("全局链");
  });

  it("providers_changed ws event removes binding for sessions pinned to deleted provider", () => {
    // Seed: session pinned to chinzy
    useSessionsStore.getState().ensure("s-pinned", {
      provider_id: "chinzy",
      preferred_model: null,
    });
    useProvidersStore.getState().set_providers(sample_providers);
    expect(useSessionsStore.getState().sessions["s-pinned"]?.provider_id).toBe(
      "chinzy",
    );

    // Backend drops chinzy and broadcasts the new list (only openrouter remains)
    __test_dispatch({
      type: "providers_changed",
      payload: {
        providers: [{ id: "openrouter-claude", name: "OpenRouter Claude", enabled: true }],
      },
    });

    // providersStore reflects the new list
    expect(useProvidersStore.getState().providers.map((p) => p.id)).toEqual([
      "openrouter-claude",
    ]);
    // session that was pinned to chinzy falls back to global chain
    expect(useSessionsStore.getState().sessions["s-pinned"]?.provider_id).toBeNull();
  });

  it("providers_changed leaves still-valid bindings intact", () => {
    useSessionsStore.getState().ensure("s-still-valid", {
      provider_id: "openrouter-claude",
    });
    __test_dispatch({
      type: "providers_changed",
      payload: { providers: sample_providers },
    });
    expect(
      useSessionsStore.getState().sessions["s-still-valid"]?.provider_id,
    ).toBe("openrouter-claude");
  });
});

// ----- 5.7 change-model modal --------------------------------------------

describe("test_change_model_modal_works", () => {
  beforeEach(resetStores);

  it("buildSetModelMessage emits code_session_set_model with model string", () => {
    const msg = buildSetModelMessage("session-z", "gpt-5.4-turbo");
    expect(msg).toEqual({
      type: "code_session_set_model",
      payload: { session_id: "session-z", model: "gpt-5.4-turbo" },
    });
  });

  it("buildSetModelMessage with empty string sends null (clears preferred_model)", () => {
    const msg = buildSetModelMessage("session-z", "");
    expect(msg).toEqual({
      type: "code_session_set_model",
      payload: { session_id: "session-z", model: null },
    });
  });

  it("buildSetModelMessage with null sends null", () => {
    const msg = buildSetModelMessage("session-z", null);
    expect(msg).toEqual({
      type: "code_session_set_model",
      payload: { session_id: "session-z", model: null },
    });
  });
});

// ----- 5.9 session state includes provider binding fields ------------------

describe("test_session_state_includes_provider_binding_fields", () => {
  beforeEach(resetStores);

  it("blank_session has provider_id + preferred_model fields", () => {
    useSessionsStore.getState().ensure("new-sid");
    const s = useSessionsStore.getState().sessions["new-sid"]!;
    expect(s).toHaveProperty("provider_id");
    expect(s).toHaveProperty("preferred_model");
    expect(s.provider_id).toBeNull();
    expect(s.preferred_model).toBeNull();
  });

  it("upsert can write provider_id + preferred_model", () => {
    useSessionsStore.getState().ensure("sid-2");
    useSessionsStore.getState().upsert("sid-2", {
      provider_id: "chinzy",
      preferred_model: "claude-4.7",
    });
    const s = useSessionsStore.getState().sessions["sid-2"]!;
    expect(s.provider_id).toBe("chinzy");
    expect(s.preferred_model).toBe("claude-4.7");
  });
});

// ----- 5.11 code_sessions_list_response populates binding fields -----------

describe("test_code_sessions_list_response_populates_binding_fields", () => {
  beforeEach(resetStores);

  it("ws response with per-session provider_id + preferred_model lands in store", () => {
    __test_dispatch({
      type: "code_sessions_list_response",
      payload: {
        items: [
          {
            base_session_id: "vpn-tunnel",
            code_session_id: "code-vpn",
            project_root: "/tmp/vpn",
            project_name: "vpn-tunnel",
            provider_id: "chinzy",
            preferred_model: null,
          },
          {
            base_session_id: "research",
            code_session_id: "code-research",
            project_root: "/tmp/research",
            project_name: "research",
            provider_id: null,
            preferred_model: "gpt-5.4",
          },
        ],
      },
    });

    const sessions = useSessionsStore.getState().sessions;
    expect(sessions["vpn-tunnel"]?.provider_id).toBe("chinzy");
    expect(sessions["vpn-tunnel"]?.preferred_model).toBeNull();
    expect(sessions["research"]?.provider_id).toBeNull();
    expect(sessions["research"]?.preferred_model).toBe("gpt-5.4");
  });

  it("code_session_provider_set ws ack writes provider_id + preferred_model", () => {
    useSessionsStore.getState().ensure("sid-ack");
    __test_dispatch({
      type: "code_session_provider_set",
      payload: {
        session_id: "sid-ack",
        provider_id: "openrouter-claude",
        preferred_model: null,
      },
    });
    const s = useSessionsStore.getState().sessions["sid-ack"]!;
    expect(s.provider_id).toBe("openrouter-claude");
    expect(s.preferred_model).toBeNull();
  });

  it("code_session_model_set ws ack writes preferred_model", () => {
    useSessionsStore.getState().ensure("sid-mdl");
    __test_dispatch({
      type: "code_session_model_set",
      payload: {
        session_id: "sid-mdl",
        provider_id: null,
        preferred_model: "claude-4.7",
      },
    });
    const s = useSessionsStore.getState().sessions["sid-mdl"]!;
    expect(s.preferred_model).toBe("claude-4.7");
  });
});

// ===========================================================================
// code-session-model-params S2 — Cursor-style picker (model + params)
// ===========================================================================

// ----- 5.1 picker payload: new structured shape + legacy back-compat ------

describe("test_set_model_message_carries_structured_params", () => {
  beforeEach(resetStores);

  it("attaches params dict when the picker passes one (new shape)", () => {
    const params = {
      thinking: true,
      fast: false,
      context: "1m" as const,
      effort: "high" as const,
    };
    const msg = buildSetModelMessage("sess-a", "gpt-5.5", params);
    expect(msg).toEqual({
      type: "code_session_set_model",
      payload: { session_id: "sess-a", model: "gpt-5.5", params },
    });
  });

  it("omits params entirely when not supplied (legacy {session_id,model})", () => {
    const msg = buildSetModelMessage("sess-a", "gpt-5.5");
    expect(msg.payload).not.toHaveProperty("params");
    expect(msg).toEqual({
      type: "code_session_set_model",
      payload: { session_id: "sess-a", model: "gpt-5.5" },
    });
  });

  it("clear-binding shape (null model, no params) is preserved", () => {
    const msg = buildSetModelMessage("sess-a", null);
    expect(msg.payload).not.toHaveProperty("params");
    expect(msg).toEqual({
      type: "code_session_set_model",
      payload: { session_id: "sess-a", model: null },
    });
  });

  it("explicit null params is treated as 'omit' (clear semantics)", () => {
    const msg = buildSetModelMessage("sess-a", "gpt-5.5", null);
    expect(msg.payload).not.toHaveProperty("params");
  });
});

// ----- 5.2 model dropdown options are catalog-driven (NOT hardcoded) ------

const sample_catalog: CatalogModel[] = [
  {
    id: "gpt-5.5",
    label: "gpt-5.5 · OpenAI",
    caps: { thinking: true, fast: true, context: true, effort: true },
  },
  {
    id: "claude-opus-4.5",
    label: "claude-opus-4.5 · Anthropic",
    caps: { thinking: true, fast: false, context: true, effort: false },
  },
  {
    id: "text-embedding-3-small",
    label: "text-embedding-3-small",
    caps: { thinking: false, fast: false, context: false, effort: false },
  },
];

describe("test_build_model_options_from_catalog", () => {
  it("default sentinel + the live catalog (no hardcoded presets)", () => {
    const opts = buildModelOptionsFromCatalog(null, sample_catalog);
    expect(opts[0]).toEqual({ value: "", label: "跟随 provider 默认" });
    expect(opts.map((o) => o.value)).toEqual([
      "",
      "gpt-5.5",
      "claude-opus-4.5",
      "text-embedding-3-small",
    ]);
  });

  it("empty catalog → just the follow-default sentinel", () => {
    const opts = buildModelOptionsFromCatalog(null, []);
    expect(opts).toEqual([{ value: "", label: "跟随 provider 默认" }]);
  });

  it("injects a non-catalog current model so legacy bindings pre-select", () => {
    const opts = buildModelOptionsFromCatalog("custom-legacy-x", sample_catalog);
    const injected = opts.find((o) => o.value === "custom-legacy-x");
    expect(injected).toBeDefined();
    expect(injected?.label).toContain("自定义");
  });

  it("does not duplicate a current model already in the catalog", () => {
    const opts = buildModelOptionsFromCatalog("gpt-5.5", sample_catalog);
    expect(opts.filter((o) => o.value === "gpt-5.5").length).toBe(1);
  });
});

// ----- 5.3 per-model capability map (gpt vs claude differ) ----------------

describe("test_caps_for_model", () => {
  it("gpt-5.5 supports effort (OpenAI reasoning_effort family)", () => {
    expect(capsForModel("gpt-5.5", sample_catalog)).toEqual({
      thinking: true,
      fast: true,
      context: true,
      effort: true,
    });
  });

  it("claude-opus-4.5 supports thinking but NOT effort", () => {
    const c = capsForModel("claude-opus-4.5", sample_catalog);
    expect(c.thinking).toBe(true);
    expect(c.effort).toBe(false);
  });

  it("embedding model exposes no tunable params", () => {
    expect(capsForModel("text-embedding-3-small", sample_catalog)).toEqual({
      thinking: false,
      fast: false,
      context: false,
      effort: false,
    });
  });

  it("unknown/custom model id → permissive (show everything)", () => {
    expect(capsForModel("totally-unknown", sample_catalog)).toEqual({
      thinking: true,
      fast: true,
      context: true,
      effort: true,
    });
  });

  it("null model id (follow default) → permissive", () => {
    expect(capsForModel(null, sample_catalog).thinking).toBe(true);
  });
});

// ----- 5.3b code_models_list_response populates the catalog store --------

describe("test_code_models_list_response_populates_store", () => {
  beforeEach(() => {
    useCodeModelsStore.setState({ models: [], source: "none", loaded: false });
  });

  it("ws dispatch writes the catalog + source", () => {
    __test_dispatch({
      type: "code_models_list_response",
      payload: { models: sample_catalog, source: "live" },
    });
    const st = useCodeModelsStore.getState();
    expect(st.loaded).toBe(true);
    expect(st.source).toBe("live");
    expect(st.models.map((m) => m.id)).toEqual([
      "gpt-5.5",
      "claude-opus-4.5",
      "text-embedding-3-small",
    ]);
  });

  it("non-array payload degrades to empty (never throws)", () => {
    __test_dispatch({
      type: "code_models_list_response",
      payload: { models: null, source: undefined },
    });
    expect(useCodeModelsStore.getState().models).toEqual([]);
  });
});

// ----- 5.4 ws acks round-trip model_params -------------------------------

describe("test_model_params_round_trip_via_ws", () => {
  beforeEach(resetStores);

  it("code_session_model_set ack writes model_params dict", () => {
    useSessionsStore.getState().ensure("sid-mp");
    __test_dispatch({
      type: "code_session_model_set",
      payload: {
        session_id: "sid-mp",
        provider_id: null,
        preferred_model: "gpt-5.5",
        model_params: { thinking: true, effort: "high", context: "1m", fast: false },
      },
    });
    const s = useSessionsStore.getState().sessions["sid-mp"]!;
    expect(s.preferred_model).toBe("gpt-5.5");
    expect(s.model_params).toEqual({
      thinking: true,
      effort: "high",
      context: "1m",
      fast: false,
    });
  });

  it("code_session_model_set ack with model_params null clears it", () => {
    useSessionsStore.getState().ensure("sid-mp2", {
      model_params: { effort: "max" },
    });
    __test_dispatch({
      type: "code_session_model_set",
      payload: {
        session_id: "sid-mp2",
        provider_id: null,
        preferred_model: null,
        model_params: null,
      },
    });
    expect(
      useSessionsStore.getState().sessions["sid-mp2"]?.model_params,
    ).toBeNull();
  });

  it("code_sessions_list_response (omits model_params) does NOT clobber it", () => {
    useSessionsStore.getState().ensure("vpn-tunnel", {
      model_params: { effort: "high", thinking: true },
    });
    __test_dispatch({
      type: "code_sessions_list_response",
      payload: {
        items: [
          {
            base_session_id: "vpn-tunnel",
            code_session_id: "code-vpn",
            project_root: "/tmp/vpn",
            project_name: "vpn-tunnel",
            provider_id: "chinzy",
            preferred_model: null,
            // NOTE: backend list response does NOT include model_params
          },
        ],
      },
    });
    const s = useSessionsStore.getState().sessions["vpn-tunnel"]!;
    expect(s.provider_id).toBe("chinzy");
    // optimistic picker write survives the list refresh
    expect(s.model_params).toEqual({ effort: "high", thinking: true });
  });

  it("code_session_provider_set ack preserves echoed model_params", () => {
    useSessionsStore.getState().ensure("sid-pp");
    __test_dispatch({
      type: "code_session_provider_set",
      payload: {
        session_id: "sid-pp",
        provider_id: "openrouter-claude",
        preferred_model: "opus-4.7",
        model_params: { effort: "medium" },
      },
    });
    const s = useSessionsStore.getState().sessions["sid-pp"]!;
    expect(s.provider_id).toBe("openrouter-claude");
    expect(s.model_params).toEqual({ effort: "medium" });
  });
});

// ----- 5.5 session state includes model_params field ---------------------

describe("test_session_state_includes_model_params", () => {
  beforeEach(resetStores);

  it("blank session has model_params field defaulting to null", () => {
    useSessionsStore.getState().ensure("sid-blank-mp");
    const s = useSessionsStore.getState().sessions["sid-blank-mp"]!;
    expect(s).toHaveProperty("model_params");
    expect(s.model_params).toBeNull();
  });
});
