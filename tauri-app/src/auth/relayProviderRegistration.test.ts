// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * WI-3 — relayProviderRegistration mirrors the stable relay device key into
 * the backend provider registry through the injected control channel.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { RelayAuthAdapter } from "./RelayAuthAdapter";
import type { Provider, User } from "./types";
import { RelayProviderRegistration } from "./relayProviderRegistration";

type RegistrationAdapter = Pick<
  RelayAuthAdapter,
  "currentUser" | "syncDeviceKey" | "fetchRelayProviderMeta"
>;

function makeUser(over: Partial<User> = {}): User {
  return {
    id: "acct-1",
    email: "acct-1@example.com",
    ...over,
  };
}

function makeProvider(over: Partial<Provider> = {}): Provider {
  return {
    id: "relay-cloud",
    name: "Relay",
    base_url: "https://relay.example.com/v1",
    models: [{ id: "gpt-5.5" }, { id: "deepseek-v4-pro" }],
    openai_compatible: true,
    supports_streaming: true,
    ...over,
  };
}

function makeAdapter(user: User | null = makeUser()): RegistrationAdapter {
  return {
    currentUser: vi.fn(() => user),
    syncDeviceKey: vi.fn(async () => ({ key: "tsk_stable", prefix: "tsk_stable_1" })),
    fetchRelayProviderMeta: vi.fn(async () => makeProvider()),
  } as RegistrationAdapter;
}

beforeEach(() => {
  vi.spyOn(console, "warn").mockImplementation(() => undefined);
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe("RelayProviderRegistration.ensure", () => {
  it("ensure absent -> mints and sends settings_providers_ensure", async () => {
    const registration = new RelayProviderRegistration();
    const adapter = makeAdapter();
    const channel = { send: vi.fn() };
    registration.attach(() => channel, vi.fn());

    await registration.ensure(adapter);

    expect(adapter.syncDeviceKey).toHaveBeenCalledWith({ force: true });
    expect(channel.send).toHaveBeenCalledTimes(1);
    expect(channel.send).toHaveBeenCalledWith({
      type: "settings_providers_ensure",
      payload: expect.objectContaining({
        id: "relay-cloud",
        source: "relay",
        account_ref: "acct-1",
        api_key: "tsk_stable",
      }),
    });
  });

  it("restore cache hit does not touch relay or send", async () => {
    const registration = new RelayProviderRegistration();
    const adapter = makeAdapter();
    const channel = { send: vi.fn() };
    registration.attach(() => channel, vi.fn());

    await registration.ensure(adapter);
    vi.mocked(adapter.syncDeviceKey).mockClear();
    vi.mocked(adapter.fetchRelayProviderMeta).mockClear();
    channel.send.mockClear();

    await registration.ensure(adapter, "restore");

    expect(adapter.syncDeviceKey).not.toHaveBeenCalled();
    expect(adapter.fetchRelayProviderMeta).not.toHaveBeenCalled();
    expect(channel.send).not.toHaveBeenCalled();
  });

  it("account switch forces device key recast", async () => {
    const registration = new RelayProviderRegistration();
    let user = makeUser({ id: "acct-1" });
    const adapter = makeAdapter(user);
    vi.mocked(adapter.currentUser).mockImplementation(() => user);
    const channel = { send: vi.fn() };
    registration.attach(() => channel, vi.fn());

    await registration.ensure(adapter);
    vi.mocked(adapter.syncDeviceKey).mockClear();
    channel.send.mockClear();
    user = makeUser({ id: "acct-2" });

    await registration.ensure(adapter, "restore");

    expect(adapter.syncDeviceKey).toHaveBeenCalledWith({ force: true });
    expect(channel.send).toHaveBeenCalledWith({
      type: "settings_providers_ensure",
      payload: expect.objectContaining({
        account_ref: "acct-2",
      }),
    });
  });

  it("back-to-back restore ensure reads local cache and sends once", async () => {
    const registration = new RelayProviderRegistration();
    const adapter = makeAdapter();
    const channel = { send: vi.fn() };
    registration.attach(() => channel, vi.fn());

    await registration.ensure(adapter, "restore");
    await registration.ensure(adapter, "restore");

    expect(adapter.syncDeviceKey).toHaveBeenCalledTimes(1);
    expect(channel.send).toHaveBeenCalledTimes(1);
  });

  it("empty models aborts without send", async () => {
    const registration = new RelayProviderRegistration();
    const adapter = makeAdapter();
    vi.mocked(adapter.fetchRelayProviderMeta).mockResolvedValueOnce(
      makeProvider({ models: [] }),
    );
    const channel = { send: vi.fn() };
    registration.attach(() => channel, vi.fn());

    await registration.ensure(adapter);

    expect(channel.send).not.toHaveBeenCalled();
    expect(console.warn).toHaveBeenCalledWith("[reg] empty models");
  });

  it("missing channel warns and does not send", async () => {
    const registration = new RelayProviderRegistration();
    const adapter = makeAdapter();
    registration.attach(() => null, vi.fn());

    await registration.ensure(adapter);

    expect(console.warn).toHaveBeenCalledWith(
      "[reg] no channel (will retry on ws connect)",
    );
    // Cold-start fix: channel checked BEFORE syncDeviceKey, so no wasted
    // device-key rotation when the ws isn't connected yet.
    expect(adapter.syncDeviceKey).not.toHaveBeenCalled();
  });

  it("serializes concurrent ensure calls (2nd syncDeviceKey waits for 1st)", async () => {
    // G2: the inflight chain must serialize ensures so two near-simultaneous
    // login events never run syncDeviceKey (→ /v1/providers) in parallel and
    // rotate each other's key away.
    const registration = new RelayProviderRegistration();
    const adapter = makeAdapter();
    const deferred: Array<(v: { key: string; prefix: string }) => void> = [];
    vi.mocked(adapter.syncDeviceKey).mockImplementation(
      () =>
        new Promise<{ key: string; prefix: string }>((resolve) => {
          deferred.push(resolve);
        }),
    );
    const channel = { send: vi.fn() };
    registration.attach(() => channel, vi.fn());

    const flush = async () => {
      for (let i = 0; i < 5; i++) await Promise.resolve();
    };

    const p1 = registration.ensure(adapter, "login");
    const p2 = registration.ensure(adapter, "login");

    await flush();
    // Serialized: only ensure#1 has reached its (blocked) syncDeviceKey;
    // ensure#2 is still queued behind the inflight chain.
    expect(deferred).toHaveLength(1);

    deferred[0]({ key: "tsk_stable", prefix: "tsk_stable_1" });
    await flush();
    // ensure#1 finished (sent) → ensure#2 now starts its own syncDeviceKey.
    expect(deferred).toHaveLength(2);

    deferred[1]({ key: "tsk_stable", prefix: "tsk_stable_1" });
    await Promise.all([p1, p2]);
    expect(channel.send).toHaveBeenCalledTimes(2);
  });
});

describe("RelayProviderRegistration.recover", () => {
  it("trips the breaker after two failed recover attempts within 60s", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-06-26T00:00:00.000Z"));
    const registration = new RelayProviderRegistration();
    const adapter = makeAdapter();
    vi.mocked(adapter.syncDeviceKey).mockResolvedValue(null);
    const onFatal = vi.fn();
    registration.attach(() => ({ send: vi.fn() }), onFatal);

    await registration.recover(adapter);
    await registration.recover(adapter);
    await registration.recover(adapter);

    expect(adapter.syncDeviceKey).toHaveBeenCalledTimes(2);
    expect(onFatal).toHaveBeenCalledTimes(1);
    expect(onFatal).toHaveBeenCalledWith(
      "中转站 key 反复失效，请重新登录或检查余额",
    );
  });
});
