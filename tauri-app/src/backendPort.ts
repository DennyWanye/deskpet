/**
 * Backend port — single source of truth for the frontend.
 *
 * Default 8100. `vite.config.ts` injects `import.meta.env.VITE_BACKEND_PORT`
 * from the `DESKPET_BACKEND_PORT` env var, so a second checkout / git
 * worktree's frontend talks to ITS OWN backend instead of colliding on
 * 8100 (parallel-dev isolation). When the env var is unset the value is
 * 8100 — identical to the previous hard-coded behaviour.
 */
export const BACKEND_PORT: number =
  Number(
    (import.meta as ImportMeta & { env?: Record<string, string> }).env
      ?.VITE_BACKEND_PORT,
  ) || 8100;
