// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
//
// P5-S1 D: vitest config moved to a sibling vitest.config.ts —
// vitest@2 bundles its own Vite copy, and importing 'vitest/config'
// here creates a type collision with @vitejs/plugin-react.
export default defineConfig(({ mode }) => {
  // WI-R1 (beta-100 付费版) — auth edition resolution.
  //
  // Two ways to select the relay edition:
  //  1. `vite --mode relay` → Vite auto-loads `.env.relay`, which sets
  //     `VITE_AUTH_EDITION=relay` (see `dev:relay` / `build:relay`).
  //  2. An OS env var `VITE_AUTH_EDITION=relay` — used when launching
  //     via `tauri dev` / `tauri build`, whose `beforeDevCommand`
  //     (`npm run dev`) can't pass `--mode` through. Injected via
  //     `define` so it wins regardless of Vite mode.
  //
  // OSS default (`npm run build`, no mode/env) → undefined →
  // `getAuthAdapter()` falls back to "manual". Zero behaviour change.
  void mode
  const envEdition = process.env.VITE_AUTH_EDITION
  const define: Record<string, string> = {}
  if (envEdition) {
    define['import.meta.env.VITE_AUTH_EDITION'] = JSON.stringify(envEdition)
  }

  // Parallel-dev port isolation (git worktree support).
  //  - DESKPET_VITE_PORT    → this dev server's port (default 5173)
  //  - DESKPET_BACKEND_PORT → the backend port; injected as
  //    import.meta.env.VITE_BACKEND_PORT so frontend WS/HTTP clients
  //    target the right backend. Default 8100.
  // A second checkout / worktree sets both to free ports and never
  // collides. Defaults preserve current behaviour exactly.
  const vitePort = Number(process.env.DESKPET_VITE_PORT) || 5173
  const backendPort = Number(process.env.DESKPET_BACKEND_PORT) || 8100
  define['import.meta.env.VITE_BACKEND_PORT'] = JSON.stringify(
    String(backendPort),
  )

  return {
    plugins: [react()],
    define,

    // Prevent vite from obscuring rust errors
    clearScreen: false,
    server: {
      port: vitePort,
      strictPort: true,
      watch: {
        // Tell vite to ignore watching `src-tauri`
        ignored: ['**/src-tauri/**'],
      },
    },
  }
})
