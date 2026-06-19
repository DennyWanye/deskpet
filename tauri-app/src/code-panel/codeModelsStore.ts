// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * code-session-model-params — live model catalog store.
 *
 * The Cursor-style picker's model dropdown is data-driven, NOT a
 * hardcoded preset list. Backend pulls the relay's real catalog
 * (中转站 GET /models) and ships it via `code_models_list_response`
 * with a per-model capability map so the picker only renders the params
 * each model actually supports (gpt-5.x → reasoning_effort; claude
 * opus/sonnet → thinking; they differ).
 *
 * Population path: ws.ts requests `code_models_list` on connect and on
 * `code_models_list_response` calls `set_catalog`.
 */
import { create } from "zustand";

/** Which picker controls a model supports (backend family heuristic). */
export interface ModelCaps {
  thinking: boolean;
  fast: boolean;
  context: boolean;
  effort: boolean;
}

export interface CatalogModel {
  id: string;
  label: string;
  caps: ModelCaps;
  /** Real nominal context window (tokens), or null when genuinely
   * unknown → UI shows "由 provider 决定" instead of a fake number. */
  context_window?: number | null;
  /** 2026-06-12: 该型号支持的上下文档位(tokens)。长度 > 1 → 面板渲染
   * 可选下拉(如 gpt-5.5: 128K/400K/1M),用户选择经 model_context_set
   * 持久化到 backend 全局 override。空/缺失 → 单档只读。 */
  supported_windows?: number[] | null;
}

/** Look up a model's real context window from the catalog. */
export function contextWindowForModel(
  model_id: string | null | undefined,
  catalog: CatalogModel[],
): number | null {
  if (!model_id) return null;
  const hit = catalog.find((m) => m.id === model_id);
  return hit?.context_window ?? null;
}

/** 上下文长度 → 仅 K/M 单位的紧凑字符串(给模型按钮 + 参数弹框统一用)。
 *  128000→"128K"  400000→"400K"  1000000→"1M"  1500000→"1.5M"  null→""。 */
export function formatContextWindow(n: number | null | undefined): string {
  if (!n || !Number.isFinite(n) || n <= 0) return "";
  if (n >= 1_000_000) {
    const m = n / 1_000_000;
    return `${Number.isInteger(m) ? m : +m.toFixed(1)}M`;
  }
  return `${Math.round(n / 1000)}K`;
}

/** 该型号可选的上下文档位(>1 才渲染下拉)。 */
export function supportedWindowsForModel(
  model_id: string | null | undefined,
  catalog: CatalogModel[],
): number[] {
  if (!model_id) return [];
  const hit = catalog.find((m) => m.id === model_id);
  return (hit?.supported_windows ?? []).filter((w) => Number.isFinite(w) && w > 0);
}

interface CodeModelsStore {
  models: CatalogModel[];
  /** "live" (relay /models), "config" (registry fallback), "none". */
  source: string;
  loaded: boolean;
  set_catalog(models: CatalogModel[], source: string): void;
}

export const useCodeModelsStore = create<CodeModelsStore>((set) => ({
  models: [],
  source: "none",
  loaded: false,
  set_catalog(models, source) {
    set({
      models: Array.isArray(models) ? models : [],
      source: source || "none",
      loaded: true,
    });
  },
}));

// --------------------------------------------------------------------
// Pure helpers — exported for vitest without a React tree.
// --------------------------------------------------------------------

const _ALL_CAPS: ModelCaps = {
  thinking: true,
  fast: true,
  context: true,
  effort: true,
};

/** Caps for a model id from the catalog. Unknown id (custom / legacy
 * free-text binding) → permissive (show everything) so we never hide a
 * control the user might need. */
export function capsForModel(
  model_id: string | null | undefined,
  catalog: CatalogModel[],
): ModelCaps {
  if (!model_id) return _ALL_CAPS;
  const hit = catalog.find((m) => m.id === model_id);
  return hit ? hit.caps : _ALL_CAPS;
}

/** Dropdown options: a "follow provider default" sentinel ("") + the
 * live catalog, with the session's current model injected if it isn't
 * in the catalog (custom ids round-trip). Replaces the old hardcoded
 * CODE_MODEL_PRESETS path. */
export function buildModelOptionsFromCatalog(
  current_model: string | null | undefined,
  catalog: CatalogModel[],
): Array<{ value: string; label: string }> {
  const opts: Array<{ value: string; label: string }> = [
    { value: "", label: "跟随 provider 默认" },
  ];
  for (const m of catalog) opts.push({ value: m.id, label: m.label });
  const cur = (current_model ?? "").trim();
  if (cur && !opts.some((o) => o.value === cur)) {
    opts.push({ value: cur, label: `${cur}（自定义）` });
  }
  return opts;
}
