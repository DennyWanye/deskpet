/**
 * W5 (R17) — autostart toggle backed by @tauri-apps/plugin-autostart.
 *
 * Returns `{ enabled, toggle, ready }`. `ready` is false until we've
 * confirmed the plugin is reachable (dev-browser / plugin-disabled
 * environments keep `ready=false`, so callers can hide the toggle).
 */
import { useCallback, useEffect, useState } from "react";

export function useAutostart() {
  const [enabled, setEnabled] = useState(false);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let cancelled = false;
    async function init() {
      const mod = await import("@tauri-apps/plugin-autostart").catch(() => null);
      if (!mod || cancelled) return;
      try {
        const current = await mod.isEnabled();
        if (cancelled) return;
        setEnabled(current);
        setReady(true);
      } catch (err) {
        console.info("[autostart] probe failed:", err);
      }
    }
    void init();
    return () => {
      cancelled = true;
    };
  }, []);

  const toggle = useCallback(async () => {
    const mod = await import("@tauri-apps/plugin-autostart").catch(() => null);
    if (!mod) return;
    try {
      if (enabled) {
        await mod.disable();
        setEnabled(false);
      } else {
        await mod.enable();
        setEnabled(true);
      }
    } catch (err) {
      console.warn("[autostart] toggle failed:", err);
    }
  }, [enabled]);

  return { enabled, toggle, ready };
}
