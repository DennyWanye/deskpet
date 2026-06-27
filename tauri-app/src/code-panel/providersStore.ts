// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * multi-provider-management Phase 5 — Providers store.
 *
 * Tiny zustand slice holding the latest list of LLM providers the backend
 * has registered. The Settings panel (Phase 4) maintains the canonical
 * editing UI; this store is the read-only mirror used by the Code panel's
 * per-session dropdown.
 *
 * Source of truth: backend's `providers_changed` ws broadcast.
 * Population path: `ws.ts` dispatch case → `set_providers(list)`.
 *
 * Phase 4 may also write to this store from its own ws plumbing — if so,
 * the two writers should agree on the shape exported here.
 */
import { create } from "zustand";

/** Sanitized provider entry shipped over ws. `api_key` is intentionally
 * NOT carried client-side (backend redacts to "********" in list events).
 */
export interface ProviderEntry {
  id: string;
  name: string;
  base_url?: string;
  model?: string;
  priority?: number;
  enabled?: boolean;
}

interface ProvidersStore {
  /** Current provider list, ordered by backend priority (ascending). */
  providers: ProviderEntry[];
  /** Replace the entire list — called from ws.ts on `providers_changed`
   * and `settings_providers_list_response`. */
  set_providers(list: ProviderEntry[]): void;
}

export const useProvidersStore = create<ProvidersStore>((set) => ({
  providers: [],
  set_providers(list) {
    set({ providers: Array.isArray(list) ? list : [] });
  },
}));

// --------------------------------------------------------------------
// Pure selectors / helpers — exposed for unit tests without needing to
// instantiate the React tree.
// --------------------------------------------------------------------

/** Build the dropdown options for a code session card.
 *
 * Always prepends the "Global Chain" entry (value=null) so users can
 * un-pin a session. Disabled providers are filtered out — pinning to a
 * disabled provider would silently fail the chain selection.
 *
 * Returns `{ value, label }` pairs where `value === null` means "no
 * binding" (clear pin) and a string value is a provider_id.
 */
export interface ProviderDropdownOption {
  value: string | null;
  label: string;
}

export function build_provider_dropdown_options(
  providers: ProviderEntry[],
): ProviderDropdownOption[] {
  // value=null still means "unpinned / follow the global chain", but the
  // user wants to SEE the real provider name from Settings → LLM
  // Providers (e.g. "relay"), not an abstract "Global Chain". So when
  // a chain head exists we label the null option with that provider's
  // name; only the genuinely-empty case keeps the generic label.
  const enabled = providers.filter((p) => p.enabled !== false);
  const head = enabled[0];
  const opts: ProviderDropdownOption[] = [
    {
      value: null,
      label: head ? `${head.name || head.id}（默认）` : "Global Chain",
    },
  ];
  for (const p of enabled) {
    opts.push({ value: p.id, label: p.name || p.id });
  }
  return opts;
}

/** Display label for the currently-bound provider on a card.
 *
 * `provider_id == null` → "Global Chain" (no pin, no lock icon).
 * Otherwise, look up the provider's name; if not found (e.g. provider was
 * deleted but binding hasn't reconciled yet), fall back to the id.
 */
export function format_provider_label(
  provider_id: string | null | undefined,
  providers: ProviderEntry[],
): string {
  if (!provider_id) {
    // Unpinned → show the effective chain-head provider's real name
    // (matches Settings → LLM Providers) instead of "Global Chain".
    const head = providers.find((p) => p.enabled !== false);
    return head ? head.name || head.id : "Global Chain";
  }
  const match = providers.find((p) => p.id === provider_id);
  return match?.name || provider_id;
}
