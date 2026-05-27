#!/usr/bin/env node
// Minimal CDP probe to verify round-2 observability bridge is wired.
import http from 'node:http'
import { WebSocket } from 'ws'

async function listTargets() {
  return new Promise((resolve, reject) => {
    http
      .get({ host: '127.0.0.1', port: 9222, path: '/json' }, (res) => {
        let data = ''
        res.on('data', (c) => (data += c))
        res.on('end', () => {
          try {
            resolve(JSON.parse(data))
          } catch (e) {
            reject(e)
          }
        })
      })
      .on('error', reject)
  })
}

async function main() {
  const targets = await listTargets()
  const pet = targets.find(
    (t) =>
      t.type === 'page' &&
      t.url &&
      t.url.replace(/#.*$/, '').endsWith('/') &&
      !t.url.includes('code-panel') &&
      !t.url.includes('message-panel'),
  )
  if (!pet) throw new Error('No pet target')

  const ws = new WebSocket(pet.webSocketDebuggerUrl)
  await new Promise((r, j) => {
    ws.on('open', r)
    ws.on('error', j)
  })
  let id = 0
  const pending = new Map()
  ws.on('message', (raw) => {
    const m = JSON.parse(raw.toString())
    if (m.id && pending.has(m.id)) {
      const { resolve, reject } = pending.get(m.id)
      pending.delete(m.id)
      if (m.error) reject(new Error(JSON.stringify(m.error)))
      else resolve(m.result)
    }
  })
  function send(method, params = {}) {
    const i = ++id
    return new Promise((resolve, reject) => {
      pending.set(i, { resolve, reject })
      ws.send(JSON.stringify({ id: i, method, params }))
    })
  }
  await send('Runtime.enable')

  async function jsEval(expr) {
    const wrapped = `JSON.stringify((function(){ try { return (${expr}); } catch (e) { return { __evalError: String(e) }; } })())`
    const r = await send('Runtime.evaluate', { expression: wrapped, returnByValue: true })
    if (r.exceptionDetails) return { __cdpExc: r.exceptionDetails.text || JSON.stringify(r.exceptionDetails) }
    try { return JSON.parse(r.result.value) } catch { return r.result.value }
  }

  const probes = {
    isDev: await jsEval('!!import.meta?.env?.DEV'),
    typeof_overlay: await jsEval('typeof window.__deskpet_anim_overlay'),
    overlay_truthy: await jsEval('!!window.__deskpet_anim_overlay'),
    typeof_debug_v2: await jsEval('typeof window.__deskpet_anim_debug_v2'),
    debug_v2_keys: await jsEval('window.__deskpet_anim_debug_v2 ? Object.keys(window.__deskpet_anim_debug_v2) : "null-debug-v2"'),
    debug_v2: await jsEval('window.__deskpet_anim_debug_v2 || null'),
    typeof_fakeIdle: await jsEval('typeof window.__deskpet_anim_fakeIdle'),
    typeof_fakeEmotion: await jsEval('typeof window.__deskpet_fake_emotion'),
    typeof_fakeMilestone: await jsEval('typeof window.__deskpet_fake_milestone'),
    typeof_fakeDnd: await jsEval('typeof window.__deskpet_fake_dnd'),
    typeof_fakeViseme: await jsEval('typeof window.__deskpet_fake_viseme'),
    typeof_fakeCelebration: await jsEval('typeof window.__deskpet_fake_celebration'),
    typeof_smoke: await jsEval('typeof window.__deskpet_test_v2_smoke'),
  }
  console.log('--- PROBE RESULTS ---')
  console.log(JSON.stringify(probes, null, 2))

  if (probes.typeof_smoke === 'function') {
    console.log('\n--- RUNNING __deskpet_test_v2_smoke() ---')
    const smokeRes = await jsEval('(async () => { const r = await window.__deskpet_test_v2_smoke(); return r; })()')
    // jsEval wraps in IIFE — but await within function context is needed; do direct evaluate
    const r2 = await send('Runtime.evaluate', {
      expression: '(async () => { try { const r = await window.__deskpet_test_v2_smoke(); return JSON.stringify(r); } catch (e) { return "ERR:" + String(e); } })()',
      returnByValue: true,
      awaitPromise: true,
    })
    console.log('smoke-await:', r2.result?.value)
  } else {
    console.log('\nSMOKE HELPER MISSING — bridge not wired')
  }

  ws.close()
}
main().catch((e) => {
  console.error('FATAL', e)
  process.exit(1)
})
