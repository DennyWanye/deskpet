/**
 * W5 (R17) — silent self-update check on startup.
 *
 * Uses the built-in Tauri updater dialog. If the updater plugin is
 * missing (dev-browser build, or user disabled auto-updates later via
 * config), the import resolves to null and we quietly do nothing.
 * Never throws — an update-check failure must not break the app.
 */
import { useEffect } from "react";

export function useUpdateChecker() {
  useEffect(() => {
    let cancelled = false;
    async function run() {
      // Lazy-load: keeps the browser preview build working without Tauri.
      const mod = await import("@tauri-apps/plugin-updater").catch(() => null);
      if (!mod || cancelled) return;
      try {
        const update = await mod.check();
        if (cancelled || !update) return;
        // The plugin config has `dialog: true`, so downloadAndInstall triggers
        // the system prompt. Call it only if there's actually an update.
        await update.downloadAndInstall();
      } catch (err) {
        // Updater endpoint not reachable / bad pubkey / no release yet —
        // all of these are non-fatal. Log to console for devs; don't
        // pester the user.
        console.info("[updater] check skipped:", err);
      }
    }
    void run();
    return () => {
      cancelled = true;
    };
  }, []);
}
