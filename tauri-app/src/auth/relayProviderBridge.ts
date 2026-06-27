// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * relayProviderBridge — WI-R2 的"最后一公里"：把中转站登录后下发的
 * provider（base_url + model + 轮换的 tsk_xxx key）推给后端 LLM endpoint，
 * 让聊天和办公技能真正走中转站。
 *
 * 复用既有热重载入口：后端 `POST /config/cloud`（`update_cloud_config`）
 * 已能热替换 `local_llm` 且无需重启；前端 binding `updateCloudConfig()`
 * 经 Rust IPC 代理（避开 release 构建 https→http 混合内容拦截）。本桥
 * 只是把 relay provider 喂给它，并带上 `persist_key:false` —— 轮换的
 * `tsk_xxx` 设备 key **绝不明文落盘**。
 *
 * 并发收敛：登录会先后 emit `login` 与 `providers-updated`，restoreSession
 * 后也会触发一次；多个触发点可能近乎同时。本桥用单 inflight promise +
 * 末值 pending 把对 `updateCloudConfig` 的调用串行化，避免后端
 * `local_llm` 全局变量赋值竞争。
 *
 * 失败可感知：`updateCloudConfig` 抛错时不让登录流程崩，但把状态置为
 * "error" 并通知订阅者（`RelayEdition` 据此提示"模型配置失败，点此重试"），
 * 不静默。
 *
 * 归属：relay 闭源资产。
 */
import { updateCloudConfig } from "../bindings/config";
import type { Provider } from "./types";
import { PREFERRED_MODEL } from "./relayConfig";

export type BridgeStatus = "idle" | "applying" | "ok" | "error";

/** 从 provider 选定要用的 model 别名：优先 PREFERRED_MODEL，否则首个。 */
export function pickModel(provider: Provider): string {
  const ids = (provider.models ?? []).map((m) => m.id);
  if (ids.includes(PREFERRED_MODEL)) return PREFERRED_MODEL;
  return ids[0] ?? PREFERRED_MODEL;
}

export class RelayProviderBridge {
  private inflight: Promise<void> | null = null;
  private pending: Provider[] | null = null;
  private _status: BridgeStatus = "idle";
  private listeners = new Set<(s: BridgeStatus) => void>();

  status(): BridgeStatus {
    return this._status;
  }

  /** 订阅状态变化（idle/applying/ok/error）。返回退订函数。 */
  onStatus(fn: (s: BridgeStatus) => void): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  private setStatus(s: BridgeStatus): void {
    this._status = s;
    for (const fn of this.listeners) {
      try {
        fn(s);
      } catch {
        /* listener 抛错不影响桥本身 */
      }
    }
  }

  /**
   * 把 `providers` 首条 provider 推给后端 LLM endpoint。
   *
   * 串行化：调用进行中再次调用 → 记为 pending（末值生效），由当前
   * drain 循环接力处理；返回的 promise 在整轮 drain 结束时 resolve。
   */
  apply(providers: Provider[]): Promise<void> {
    this.pending = providers;
    if (this.inflight) return this.inflight;
    this.inflight = this.drain();
    return this.inflight;
  }

  private async drain(): Promise<void> {
    try {
      while (this.pending) {
        const providers = this.pending;
        this.pending = null;
        await this.applyOnce(providers);
      }
    } finally {
      this.inflight = null;
    }
  }

  private async applyOnce(providers: Provider[]): Promise<void> {
    const provider = providers[0];
    if (!provider) {
      // 空列表 —— 无可配置 provider，静默 no-op（不报错）。
      return;
    }
    this.setStatus("applying");
    try {
      await updateCloudConfig("", {
        base_url: provider.base_url,
        model: pickModel(provider),
        // null/空 → updateCloudConfig 保留后端当前 key（非轮换模式）。
        api_key: provider.api_key ?? undefined,
        // WI-R2：轮换的 tsk_xxx 绝不落明文盘。
        persist_key: false,
      });
      this.setStatus("ok");
    } catch (err) {
      // 不抛出 —— 登录流程不能因桥失败而中断；但状态置 error，
      // RelayEdition 据此给用户"模型配置失败，点此重试"。
      this.setStatus("error");
      console.warn("[relayProviderBridge] updateCloudConfig failed:", err);
    }
  }

  /**
   * key 失效（中转站对旧 tsk_xxx 返 401）后的恢复：拉新 provider
   * （rotate 模式发新 key）并重推后端。聊天层在收到 `relay_key_invalid`
   * 时调用本方法，成功后重发该聊天请求。
   *
   * @returns 重新配置是否成功（status === "ok"）。
   */
  async recoverFromKeyInvalid(adapter: {
    listProviders: () => Promise<Provider[]>;
  }): Promise<boolean> {
    try {
      const providers = await adapter.listProviders();
      await this.apply(providers);
    } catch (err) {
      this.setStatus("error");
      console.warn("[relayProviderBridge] key-invalid recovery failed:", err);
      return false;
    }
    return this._status === "ok";
  }
}

/** 进程内单例 —— RelayEdition / 聊天层共用同一座桥。 */
export const relayProviderBridge = new RelayProviderBridge();
