/**
 * P5-S1 D — Vitest configuration kept separate from vite.config.ts
 * because vitest@2 bundles its own Vite, which conflicts with the
 * project's vite@8 plugin types when imported into a single file.
 *
 * Tests are pure-logic (PetStateMachine, severity_score) — no DOM
 * dependency, so 'node' environment is enough.
 */
import { defineConfig } from 'vitest/config'

export default defineConfig({
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts', 'src/**/*.test.tsx'],
  },
})
