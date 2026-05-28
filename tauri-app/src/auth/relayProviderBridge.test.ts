// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * TDD T2 — relayProviderBridge：登录态 → 后端 LLM endpoint 桥接。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Provider } from "./types";

// Mock the IPC binding — the bridge must go through updateCloudConfig.
const updateCloudConfig =
  vi.fn<
    (secret: string, update: Record<string, unknown>) => Promise<{ ok: boolean }>
  >();
vi.mock("../bindings/config", () => ({
  updateCloudConfig: (secret: string, update: Record<string, unknown>) =>
    updateCloudConfig(secret, update),
}));

import { RelayProviderBridge, pickModel } from "./relayProviderBridge";

function makeProvider(over: Partial<Provider> = {}): Provider {
  return {
    id: "p1",
    name: "My Relay",
    base_url: "https://your-llm-relay.example.com/v1",
    api_key: "tsk_relay_abc",
    models: [{ id: "gpt-5.5" }, { id: "deepseek-v4-pro" }],
    openai_compatible: true,
    supports_streaming: true,
    ...over,
  };
}

beforeEach(() => {
  updateCloudConfig.mockReset();
  updateCloudConfig.mockResolvedValue({ ok: true });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("pickModel", () => {
  it("prefers PREFERRED_MODEL when present", () => {
    expect(pickModel(makeProvider())).toBe("gpt-5.5");
  });
  it("falls back to first model when preferred absent", () => {
    expect(pickModel(makeProvider({ models: [{ id: "claude-x" }] }))).toBe("claude-x");
  });
});

describe("T2-1 · apply pushes provider with persist_key=false", () => {
  it("calls updateCloudConfig once with correct payload", async () => {
    const bridge = new RelayProviderBridge();
    await bridge.apply([makeProvider()]);
    expect(updateCloudConfig).toHaveBeenCalledTimes(1);
    const [, update] = updateCloudConfig.mock.calls[0];
    expect(update).toMatchObject({
      base_url: "https://your-llm-relay.example.com/v1",
      model: "gpt-5.5",
      api_key: "tsk_relay_abc",
      persist_key: false,
    });
    expect(bridge.status()).toBe("ok");
  });
});

describe("T2-2 · empty provider list is a safe no-op", () => {
  it("does not call the backend, does not throw", async () => {
    const bridge = new RelayProviderBridge();
    await expect(bridge.apply([])).resolves.toBeUndefined();
    expect(updateCloudConfig).not.toHaveBeenCalled();
  });
});

describe("T2-3 · re-apply on key rotation", () => {
  it("pushes again when providers-updated fires a second time", async () => {
    const bridge = new RelayProviderBridge();
    await bridge.apply([makeProvider({ api_key: "tsk_old" })]);
    await bridge.apply([makeProvider({ api_key: "tsk_new" })]);
    expect(updateCloudConfig).toHaveBeenCalledTimes(2);
    const [, second] = updateCloudConfig.mock.calls[1];
    expect(second.api_key).toBe("tsk_new");
  });
});

describe("T2-4 · backend failure is caught", () => {
  it("does not throw; status becomes error", async () => {
    updateCloudConfig.mockRejectedValueOnce(new Error("backend down"));
    const bridge = new RelayProviderBridge();
    const seen: string[] = [];
    bridge.onStatus((s) => seen.push(s));
    await expect(bridge.apply([makeProvider()])).resolves.toBeUndefined();
    expect(bridge.status()).toBe("error");
    expect(seen).toContain("error");
  });
});

describe("T2-5 · concurrent triggers are serialized, last value wins", () => {
  it("serializes calls and applies the last provider", async () => {
    // First call is slow + controllable; later (coalesced) calls resolve
    // immediately so the drain loop can finish once released.
    let release!: () => void;
    const firstCall = new Promise<{ ok: boolean }>((resolve) => {
      release = () => resolve({ ok: true });
    });
    updateCloudConfig.mockResolvedValue({ ok: true });
    updateCloudConfig.mockReturnValueOnce(firstCall);
    const bridge = new RelayProviderBridge();

    // Fire three applies near-simultaneously while the first is inflight.
    const p1 = bridge.apply([makeProvider({ base_url: "https://a/v1" })]);
    bridge.apply([makeProvider({ base_url: "https://b/v1" })]);
    bridge.apply([makeProvider({ base_url: "https://c/v1" })]);

    // First call is inflight; release it so the drain loop continues.
    await Promise.resolve();
    expect(updateCloudConfig).toHaveBeenCalledTimes(1);
    release();
    // Let the drain loop pick up the pending (last) value.
    await p1;

    // Exactly two backend calls: the first (a) + the coalesced last (c).
    // b was overwritten by c while a was inflight.
    expect(updateCloudConfig).toHaveBeenCalledTimes(2);
    const urls = updateCloudConfig.mock.calls.map((c) => c[1].base_url);
    expect(urls[0]).toBe("https://a/v1");
    expect(urls[urls.length - 1]).toBe("https://c/v1");
  });
});

describe("T2-6 · recoverFromKeyInvalid closes the rotation loop", () => {
  it("re-fetches providers and re-pushes", async () => {
    const bridge = new RelayProviderBridge();
    const adapter = {
      listProviders: vi.fn().mockResolvedValue([makeProvider({ api_key: "tsk_rotated" })]),
    };
    const ok = await bridge.recoverFromKeyInvalid(adapter);
    expect(adapter.listProviders).toHaveBeenCalledTimes(1);
    expect(updateCloudConfig).toHaveBeenCalledTimes(1);
    expect(updateCloudConfig.mock.calls[0][1].api_key).toBe("tsk_rotated");
    expect(ok).toBe(true);
  });

  it("returns false when re-fetch fails", async () => {
    const bridge = new RelayProviderBridge();
    const adapter = {
      listProviders: vi.fn().mockRejectedValue(new Error("offline")),
    };
    expect(await bridge.recoverFromKeyInvalid(adapter)).toBe(false);
  });
});
