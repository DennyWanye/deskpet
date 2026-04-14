/**
 * S12 — listens for the Rust-side supervisor's lifecycle events so the
 * React tree can refresh its shared secret (and thus reconnect WS)
 * after a crash + respawn.
 *
 * Events emitted by process_manager.rs:
 *   - `backend-crashed` (payload: string) — child process died.
 *   - `backend-restarted` (payload: secret) — new child is up.
 *   - `backend-dead` (payload: reason) — supervisor gave up.
 *
 * The hook takes a `refresh` callback that the caller can use to
 * re-trigger secret polling / UI state reset. We deliberately don't
 * read the restarted-secret payload here and write it directly into
 * the secret state — the existing pollSecret loop is the single source
 * of truth, and calling it again is idempotent.
 */
import { useEffect } from "react";

type Lifecycle = "crashed" | "restarted" | "dead";

export function useBackendLifecycle(
  onEvent: (kind: Lifecycle, payload: string) => void,
) {
  useEffect(() => {
    let unlistenAll: Array<() => void> = [];
    let cancelled = false;

    async function wire() {
      const mod = await import("@tauri-apps/api/event").catch(() => null);
      if (!mod || cancelled) return;
      const events: Array<[string, Lifecycle]> = [
        ["backend-crashed", "crashed"],
        ["backend-restarted", "restarted"],
        ["backend-dead", "dead"],
      ];
      for (const [name, kind] of events) {
        const unlisten = await mod.listen<string>(name, (e) => {
          onEvent(kind, e.payload ?? "");
        });
        unlistenAll.push(unlisten);
      }
    }
    void wire();
    return () => {
      cancelled = true;
      unlistenAll.forEach((fn) => fn());
      unlistenAll = [];
    };
  }, [onEvent]);
}
