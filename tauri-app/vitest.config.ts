/**
 * Vitest configuration.
 *
 * Pet-animation v1 (2026-05-24) introduces a `pet-anim/*` module suite whose
 * unit tests need a DOM environment (Window, PointerEvent, localStorage,
 * ResizeObserver shim). We switched from 'node' → 'jsdom' globally because
 * the existing pet-state tests are pure-logic and unaffected by the heavier
 * environment.
 *
 * Coverage thresholds gate the pet-anim Sprint per TDD §5.4 (lines ≥ 80%,
 * branches ≥ 70%). `include` deliberately scopes coverage to pet-anim +
 * pet-state so legacy untested modules don't drag the metric down.
 *
 * Kept separate from vite.config.ts because vitest@2 bundles its own Vite
 * which conflicts with the project's vite@8 plugin types when imported
 * into a single file.
 */
import { defineConfig } from 'vitest/config'

export default defineConfig({
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.ts', 'src/**/*.test.tsx'],
    setupFiles: ['./src/pet-anim/__tests__/_setup.ts'],
    coverage: {
      provider: 'v8',
      include: ['src/pet-anim/**', 'src/pet-state/**'],
      exclude: [
        'src/pet-anim/__tests__/**',
        'src/pet-anim/_probe_constants.ts',
      ],
      thresholds: {
        lines: 80,
        branches: 70,
        functions: 80,
        statements: 80,
      },
      reporter: ['text', 'json-summary'],
    },
  },
})
