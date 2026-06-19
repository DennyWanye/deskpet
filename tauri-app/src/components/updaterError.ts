// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * 把 tauri-plugin-updater 抛出的底层错误翻译成给终端用户看的中文。
 *
 * 独立成模块是为了能在 vitest 里纯函数单测，而不必把整个 SettingsPanel
 * 的 React/Tauri import 图拉进测试环境。
 */
export function formatUpdaterError(err: unknown): string {
  const raw = err instanceof Error ? err.message : String(err ?? "");
  const lower = raw.toLowerCase();

  // 端点 404 / 无 release / 拉不到 latest.json —— 最常见（发布服务器还没
  // 上架对应版本，或 latest.json 缺失）。给一句人话，别甩英文堆栈。
  if (
    lower.includes("404") ||
    lower.includes("could not fetch") ||
    lower.includes("not found") ||
    lower.includes("no such") ||
    lower.includes("releasenotfound")
  ) {
    return "暂时连不上更新服务器（可能还没有可用的新版本）。请稍后再试。";
  }

  if (
    lower.includes("network") ||
    lower.includes("timeout") ||
    lower.includes("dns") ||
    lower.includes("connect") ||
    lower.includes("request")
  ) {
    return "网络连接失败，无法检查更新。请检查网络后重试。";
  }

  if (
    lower.includes("signature") ||
    lower.includes("verify") ||
    lower.includes("pubkey")
  ) {
    return "更新包签名校验失败，已为安全起见中止安装。请联系开发者。";
  }

  return raw ? `检查更新出错：${raw}` : "检查更新出错，请稍后重试。";
}
