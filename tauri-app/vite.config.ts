// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'
import fs from 'node:fs'
import path from 'node:path'

// ---------------------------------------------------------------------------
// 桌宠形象动态清单：扫 public/assets/live2d/ 下每个子目录的 .model3.json，
// 生成 /assets/live2d/models.json 给前端 fetch（设置面板「桌宠形象」下拉）。
// dev：中间件实时扫（加/删模型后刷新即生效，无需重启 vite）。
// build：写一份 models.json 到产物里。
// ---------------------------------------------------------------------------
interface ScannedModel {
  id: string
  name: string
  modelPath: string
}

function slug(name: string): string {
  return (
    name
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '') || 'model'
  )
}

function findModel3(dir: string): string | null {
  let found: string | null = null
  const walk = (d: string, depth: number): void => {
    if (found || depth > 3) return
    let entries: fs.Dirent[]
    try {
      entries = fs.readdirSync(d, { withFileTypes: true })
    } catch {
      return
    }
    // 文件优先（同目录先找 .model3.json）
    for (const e of entries) {
      if (e.isFile() && e.name.endsWith('.model3.json')) {
        found = path.join(d, e.name)
        return
      }
    }
    for (const e of entries) {
      if (found) return
      if (e.isDirectory()) walk(path.join(d, e.name), depth + 1)
    }
  }
  walk(dir, 0)
  return found
}

function scanPetModels(publicDir: string): ScannedModel[] {
  const root = path.join(publicDir, 'assets', 'live2d')
  const models: ScannedModel[] = []
  let dirs: fs.Dirent[]
  try {
    dirs = fs.readdirSync(root, { withFileTypes: true })
  } catch {
    return models
  }
  for (const entry of dirs) {
    if (!entry.isDirectory()) continue
    const model3 = findModel3(path.join(root, entry.name))
    if (!model3) continue
    // 相对 public 的 URL，每段 encode（容忍空格 / # / 括号等）
    const rel = path.relative(publicDir, model3).split(path.sep).join('/')
    const modelPath = '/' + rel.split('/').map(encodeURIComponent).join('/')
    models.push({ id: slug(entry.name), name: entry.name, modelPath })
  }
  // 去重 id（不同目录 slug 撞了就加序号）
  const seen = new Set<string>()
  for (const m of models) {
    let id = m.id
    let n = 2
    while (seen.has(id)) id = `${m.id}-${n++}`
    seen.add(id)
    m.id = id
  }
  return models
}

function petModelsManifestPlugin(): Plugin {
  let publicDir = path.resolve('public')
  let outDir = path.resolve('dist')
  return {
    name: 'pet-models-manifest',
    configResolved(c) {
      publicDir = c.publicDir
      outDir = path.resolve(c.root, c.build.outDir)
    },
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const url = (req.url ?? '').split('?')[0]
        if (url === '/assets/live2d/models.json') {
          res.setHeader('Content-Type', 'application/json; charset=utf-8')
          res.setHeader('Cache-Control', 'no-store')
          res.end(JSON.stringify(scanPetModels(publicDir)))
          return
        }
        next()
      })
    },
    closeBundle() {
      try {
        const dest = path.join(outDir, 'assets', 'live2d')
        fs.mkdirSync(dest, { recursive: true })
        fs.writeFileSync(
          path.join(dest, 'models.json'),
          JSON.stringify(scanPetModels(publicDir)),
        )
      } catch {
        /* build-time manifest is best-effort */
      }
    },
  }
}

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  // WI-R1 (beta-100 付费版) — auth edition resolution.
  void mode
  const envEdition = process.env.VITE_AUTH_EDITION
  const define: Record<string, string> = {}
  if (envEdition) {
    define['import.meta.env.VITE_AUTH_EDITION'] = JSON.stringify(envEdition)
  }

  // Parallel-dev port isolation (git worktree support).
  const vitePort = Number(process.env.DESKPET_VITE_PORT) || 5173
  const backendPort = Number(process.env.DESKPET_BACKEND_PORT) || 8100
  define['import.meta.env.VITE_BACKEND_PORT'] = JSON.stringify(
    String(backendPort),
  )

  return {
    plugins: [react(), petModelsManifestPlugin()],
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
