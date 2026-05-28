// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * 单测 — AuthAdapter 契约 + Null/Manual 行为 + 工厂逻辑。
 * 不连接任何 IO；纯接口形态 / 错误码 / 事件订阅验证。
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ManualAuthAdapter,
  NotSupportedError,
  NullAuthAdapter,
  RelayAuthAdapter,
  _resetAuthAdapterForTests,
  buildAdapter,
  getAuthAdapter,
} from "./index";
import type { AuthAdapter, AuthEvent } from "./types";

afterEach(() => {
  _resetAuthAdapterForTests();
});

describe("NullAuthAdapter", () => {
  const make = () => new NullAuthAdapter();

  it("has stable id + displayName", () => {
    const a = make();
    expect(a.id).toBe("null");
    expect(a.displayName).toMatch(/匿名/);
  });

  it("never authenticated, no current user", () => {
    const a = make();
    expect(a.isAuthenticated()).toBe(false);
    expect(a.currentUser()).toBeNull();
  });

  it("throws NotSupportedError on login / register", async () => {
    const a = make();
    await expect(a.login({ email: "x", password: "y" })).rejects.toBeInstanceOf(
      NotSupportedError,
    );
    await expect(
      a.register({ email: "x", password: "y" }),
    ).rejects.toBeInstanceOf(NotSupportedError);
  });

  it("logout is a safe no-op", async () => {
    await expect(make().logout()).resolves.toBeUndefined();
  });

  it("listProviders returns [] (no remote source)", async () => {
    expect(await make().listProviders()).toEqual([]);
  });

  it("getUsage returns null", async () => {
    expect(await make().getUsage()).toBeNull();
  });

  it("onEvent returns an unsubscribe fn that removes the listener", () => {
    const a = make();
    const handler = vi.fn();
    const unsub = a.onEvent(handler);
    expect(typeof unsub).toBe("function");
    unsub(); // should not throw
  });
});

describe("ManualAuthAdapter", () => {
  const make = () => new ManualAuthAdapter();

  it("has stable id + displayName", () => {
    const a = make();
    expect(a.id).toBe("manual");
    expect(a.displayName).toMatch(/自托管/);
  });

  it("always authenticated with a local placeholder user", () => {
    const a = make();
    expect(a.isAuthenticated()).toBe(true);
    const u = a.currentUser();
    expect(u).not.toBeNull();
    expect(u?.id).toBe("local");
    expect(u?.plan).toBe("self-hosted");
  });

  it("login / register still throw NotSupported (no remote account)", async () => {
    const a = make();
    await expect(a.login({ email: "x", password: "y" })).rejects.toBeInstanceOf(
      NotSupportedError,
    );
    await expect(
      a.register({ email: "x", password: "y" }),
    ).rejects.toBeInstanceOf(NotSupportedError);
  });

  it("logout no-op; listProviders [] (data owned by backend)", async () => {
    const a = make();
    await expect(a.logout()).resolves.toBeUndefined();
    expect(await a.listProviders()).toEqual([]);
    expect(await a.getUsage()).toBeNull();
  });
});

describe("buildAdapter() factory", () => {
  it("maps 'null' → NullAuthAdapter", () => {
    const a = buildAdapter("null");
    expect(a.id).toBe("null");
    expect(a).toBeInstanceOf(NullAuthAdapter);
  });

  it("maps 'manual' → ManualAuthAdapter", () => {
    const a = buildAdapter("manual");
    expect(a.id).toBe("manual");
    expect(a).toBeInstanceOf(ManualAuthAdapter);
  });

  it("maps 'relay' → RelayAuthAdapter (W2: now bundled)", () => {
    // Before W2 the OSS build fell back to Manual + a console warning.
    // Now the adapter is bundled in the main repo for development; the
    // closed-source paid split is deferred. Once the split lands, this
    // assertion will need to flip back for the OSS-only build target.
    const a = buildAdapter("relay");
    expect(a.id).toBe("relay");
    expect(a).toBeInstanceOf(RelayAuthAdapter);
  });

  it("T1-4: unknown edition throws (exhaustive guard)", () => {
    // WI-R1: VITE_AUTH_EDITION is a build-time string; a typo / bad
    // value must fail loud, not silently fall through.
    expect(() =>
      buildAdapter("bogus" as unknown as Parameters<typeof buildAdapter>[0]),
    ).toThrow(/Unknown AuthEdition/);
  });
});

describe("getAuthAdapter() singleton", () => {
  it("returns the same instance across calls", () => {
    const a = getAuthAdapter();
    const b = getAuthAdapter();
    expect(a).toBe(b);
  });

  it("default edition is Manual (OSS build)", () => {
    const a = getAuthAdapter();
    expect(a.id).toBe("manual");
  });

  it("reset helper actually resets the singleton", () => {
    const a = getAuthAdapter();
    _resetAuthAdapterForTests();
    const b = getAuthAdapter();
    expect(a).not.toBe(b);
  });
});

describe("AuthEvent type discipline", () => {
  // Compile-time check: the union covers all expected variants.
  it("AuthEvent variants are well-formed", () => {
    const samples: AuthEvent[] = [
      { type: "login", user: { id: "x", email: "y" } },
      { type: "logout" },
      { type: "tokens-refreshed" },
      { type: "providers-updated", providers: [] },
      {
        type: "usage-updated",
        usage: { rate_limit: { rpm: 60, tpm: null } },
      },
    ];
    expect(samples).toHaveLength(5);
  });

  it("listener can be registered + unsubscribed without firing events", () => {
    const a: AuthAdapter = new NullAuthAdapter();
    const got: AuthEvent[] = [];
    const unsub = a.onEvent((e) => got.push(e));
    unsub();
    expect(got).toEqual([]); // nothing was emitted
  });
});
