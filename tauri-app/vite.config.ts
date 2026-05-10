import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
//
// P5-S1 D: vitest config moved to a sibling vitest.config.ts —
// vitest@2 bundles its own Vite copy, and importing 'vitest/config'
// here creates a type collision with @vitejs/plugin-react.
export default defineConfig({
  plugins: [react()],

  // Prevent vite from obscuring rust errors
  clearScreen: false,
  server: {
    port: 5173,
    strictPort: true,
    watch: {
      // Tell vite to ignore watching `src-tauri`
      ignored: ['**/src-tauri/**'],
    },
  },
})
