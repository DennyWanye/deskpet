#!/usr/bin/env node
// Capture screenshots for each animation state for round-2 evidence.
import http from 'node:http'
import { WebSocket } from 'ws'
import fs from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const SHOT_DIR = path.join(__dirname, 'screenshots')

async function listTargets() {
  return new Promise((resolve, reject) => {
    http.get({ host: '127.0.0.1', port: 9222, path: '/json' }, (res) => {
      let data = ''
      res.on('data', (c) => (data += c))
      res.on('end', () => { try { resolve(JSON.parse(data)) } catch (e) { reject(e) } })
    }).on('error', reject)
  })
}

async function main() {
  const targets = await listTargets()
  const pet = targets.find(
    (t) => t.type === 'page' && t.url && t.url.replace(/#.*$/, '').endsWith('/') &&
           !t.url.includes('code-panel') && !t.url.includes('message-panel'),
  )
  const ws = new WebSocket(pet.webSocketDebuggerUrl)
  await new Promise((r, j) => { ws.on('open', r); ws.on('error', j) })
  let id = 0; const pending = new Map()
  ws.on('message', (raw) => {
    const m = JSON.parse(raw.toString())
    if (m.id && pending.has(m.id)) {
      const { resolve, reject } = pending.get(m.id); pending.delete(m.id)
      if (m.error) reject(new Error(JSON.stringify(m.error))); else resolve(m.result)
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
  await send('Page.enable')

  async function exec(stmts) {
    await send('Runtime.evaluate', { expression: `(function(){ ${stmts} })()`, returnByValue: true })
  }
  async function shot(name) {
    await fs.mkdir(SHOT_DIR, { recursive: true })
    const r = await send('Page.captureScreenshot', { format: 'png' })
    const f = path.join(SHOT_DIR, `case-${name}.png`)
    await fs.writeFile(f, Buffer.from(r.data, 'base64'))
    console.log(`saved ${name} → ${f}`)
  }
  async function reset() {
    await exec(`
      const o = window.__deskpet_anim_overlay; const now = performance.now();
      o.setDragState('idle', now); o.setUserInputActive(false, now);
      o.setThinkingActive(false, now); o.setLowEnergy(false, now);
      o.setEmotion('neutral', now); o.setDNDActive(false, [], now);
      o.setEdgeAttached(null, now);
      window.__deskpet_fake_dnd && window.__deskpet_fake_dnd(false, []);
    `)
  }
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

  await reset(); await sleep(300); await shot('00-baseline-rest')

  await exec(`window.__deskpet_anim_overlay.setDragState('being_held', performance.now());`)
  await sleep(200); await shot('a1-01-being-held-wobble')

  await exec(`window.__deskpet_anim_overlay.setDragState('idle', performance.now());`)
  await sleep(120); await shot('a1-03-spring-back-mid')

  await reset(); await sleep(200)
  await exec(`window.__deskpet_anim_overlay.setUserInputActive(true, performance.now());`)
  await sleep(120); await shot('b1-01-user-input-tilt')

  await reset(); await sleep(200)
  await exec(`window.__deskpet_anim_overlay.setThinkingActive(true, performance.now());`)
  await sleep(200); await shot('b2-01-thinking-on')

  await reset(); await sleep(200)
  await exec(`window.__deskpet_anim_overlay.setVisemeFrame({ v: 'A', t_ms: performance.now() });`)
  await sleep(60); await shot('b3-01-viseme-A')
  await exec(`window.__deskpet_anim_overlay.setVisemeFrame({ v: 'I', t_ms: performance.now() });`)
  await sleep(60); await shot('b3-01-viseme-I')

  await reset(); await sleep(200)
  for (const em of ['happy', 'sad', 'angry', 'surprised']) {
    await exec(`window.__deskpet_fake_emotion('${em}');`)
    await sleep(150); await shot(`d1-emotion-${em}`)
  }

  await reset(); await sleep(200)
  await exec(`window.__deskpet_anim_overlay.setLowEnergy(true, performance.now());`)
  await sleep(200); await shot('c1-low-energy')

  await reset(); await sleep(200)
  await exec(`window.__deskpet_anim_overlay.triggerWelcome('intense', performance.now());`)
  await sleep(100); await shot('c2-welcome-intense')

  await reset(); await sleep(200)
  await exec(`window.__deskpet_anim_overlay.setEdgeAttached('right', performance.now());`)
  await sleep(120); await shot('e1-edge-right')

  await reset(); await sleep(200)
  await exec(`window.__deskpet_fake_dnd(true, ['fullscreen']);`)
  await sleep(200); await shot('f1-dnd-active-zzz')
  await reset()

  ws.close()
}
main().catch((e) => { console.error('FATAL', e); process.exit(1) })
