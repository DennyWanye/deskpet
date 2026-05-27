#!/usr/bin/env node
/**
 * v2 round-1 probe runner. Reuses ws from v1 round-2.
 * Usage:
 *   node probe-runner.mjs d0-all
 *   node probe-runner.mjs eval "<expression>"
 *   node probe-runner.mjs screenshot <name>
 *   node probe-runner.mjs mouse <type> <x> <y> [button]
 */
import http from 'node:http'
import { WebSocket } from 'file:///G:/projects/deskpet/plans/2026-05-24-pet-animation-ux/evidence/round-2/node_modules/ws/wrapper.mjs'
import fs from 'node:fs/promises'
import path from 'node:path'

const PORT = 9222
const SHOT_DIR = 'G:/projects/deskpet/plans/2026-05-25-pet-animation-ux-v2/evidence/round-1'

function listTargets() {
  return new Promise((res, rej) => {
    http.get({ host: '127.0.0.1', port: PORT, path: '/json' }, (r) => {
      let d = ''
      r.on('data', (c) => (d += c))
      r.on('end', () => { try { res(JSON.parse(d)) } catch (e) { rej(e) } })
    }).on('error', rej)
  })
}

async function pickPetTarget() {
  const ts = await listTargets()
  const p = ts.find((t) => t.type === 'page' && t.url && t.url.replace(/#.*$/, '').endsWith('/') && !t.url.includes('code-panel') && !t.url.includes('message-panel'))
  if (!p) throw new Error('pet target not found: ' + JSON.stringify(ts.map((t) => t.url)))
  return p
}

class CDP {
  constructor(wsUrl) { this.wsUrl = wsUrl; this.id = 0; this.pending = new Map() }
  connect() {
    return new Promise((res, rej) => {
      this.ws = new WebSocket(this.wsUrl)
      this.ws.on('open', res)
      this.ws.on('error', rej)
      this.ws.on('message', (raw) => {
        const m = JSON.parse(raw.toString())
        if (m.id && this.pending.has(m.id)) {
          const { res: r, rej: j } = this.pending.get(m.id)
          this.pending.delete(m.id)
          if (m.error) j(new Error(JSON.stringify(m.error))); else r(m.result)
        }
      })
    })
  }
  send(method, params) {
    const id = ++this.id
    return new Promise((res, rej) => {
      this.pending.set(id, { res, rej })
      this.ws.send(JSON.stringify({ id, method, params: params || {} }))
    })
  }
  async eval(expr, returnByValue = true) {
    const r = await this.send('Runtime.evaluate', { expression: expr, returnByValue, awaitPromise: true })
    if (r.exceptionDetails) throw new Error('eval err: ' + r.exceptionDetails.text + ' :: ' + JSON.stringify(r.exceptionDetails.exception?.description || ''))
    return r.result.value
  }
  async screenshot(name) {
    const r = await this.send('Page.captureScreenshot', { format: 'png' })
    const out = path.join(SHOT_DIR, name + '.png')
    await fs.writeFile(out, Buffer.from(r.data, 'base64'))
    return out
  }
  async mouse(type, x, y, button = 'left') {
    return this.send('Input.dispatchMouseEvent', { type, x, y, button, clickCount: type === 'mousePressed' ? 1 : 0 })
  }
}

async function main() {
  const cmd = process.argv[2]
  if (!cmd) throw new Error('usage: probe-runner.mjs <cmd>')
  const target = await pickPetTarget()
  const cdp = new CDP(target.webSocketDebuggerUrl)
  await cdp.connect()
  await cdp.send('Page.enable')
  await cdp.send('Runtime.enable')

  const results = {}

  async function probe(name, code) {
    try {
      const v = await cdp.eval(code)
      results[name] = { ok: true, value: v }
      console.log(`[${name}] OK`, JSON.stringify(v).slice(0, 240))
    } catch (e) {
      results[name] = { ok: false, error: String(e).slice(0, 600) }
      console.log(`[${name}] FAIL`, String(e).slice(0, 240))
    }
  }

  if (cmd === 'd0-all') {
    // D0-01 Tauri startDragging
    await probe('D0-01-tauri-invoke', `(typeof window.__TAURI__ === 'object' && !!window.__TAURI__) ? Object.keys(window.__TAURI__) : 'no-tauri'`)
    await probe('D0-01-startDragging', `(async()=>{try{const inv=window.__TAURI__?.core?.invoke||window.__TAURI__?.invoke; if(!inv) return 'no-invoke'; return 'has-invoke '+typeof inv;}catch(e){return 'ERR '+e.message}})()`)

    // D0-02 viseme backend (no backend support — should be missing)
    await probe('D0-02-viseme-backend', `(async()=>{try{const r=await fetch('http://127.0.0.1:8100/health'); const j=await r.json(); return {status:r.status, has_viseme:'viseme' in (j||{})}}catch(e){return 'ERR '+e.message}})()`)

    // D0-03 phonemeEstimator
    await probe('D0-03-phoneme-export', `(()=>{try{const m=window.__deskpet_anim_modules||{}; return {has_phoneme:!!m.phonemeEstimator, has_viseme:!!m.visemeLipsync, keys:Object.keys(m).slice(0,30)}}catch(e){return 'ERR '+e.message}})()`)

    // D0-04 Hiyori 10 params via overlay
    await probe('D0-04-overlay-params', `(()=>{try{const ov=window.__deskpet_anim_overlay; if(!ov) return 'no-overlay'; return {has_overlay:true, keys:Object.keys(ov)}}catch(e){return 'ERR '+e.message}})()`)

    // D0-05 LLM emotion (backend)
    await probe('D0-05-emotion-route', `(async()=>{try{const r=await fetch('http://127.0.0.1:8100/health'); return {status:r.status}}catch(e){return 'ERR '+e.message}})()`)

    // D0-06 audio session enum
    await probe('D0-06-audio-cmd', `(async()=>{try{const inv=window.__TAURI__?.core?.invoke||window.__TAURI__?.invoke; if(!inv) return 'no-invoke'; try{const r=await inv('is_any_audio_capture_active'); return {ok:true, value:r}}catch(e){return {ok:false, err:String(e).slice(0,200)}}}catch(e){return 'ERR '+e.message}})()`)

    // Flag check — v2 flags exist?
    await probe('D0-X-flags', `(()=>{const keys=['deskpet_animation_v2','deskpet_anim_held','deskpet_anim_userInput','deskpet_anim_thinking','deskpet_anim_viseme','deskpet_anim_mouthFade','deskpet_anim_lowEnergy','deskpet_anim_welcome','deskpet_anim_timeCele','deskpet_anim_emotion','deskpet_anim_milestone','deskpet_anim_edge','deskpet_anim_occlusion','deskpet_anim_dnd','deskpet_anim_dnd_fullscreen','deskpet_anim_dnd_typing','deskpet_anim_dnd_call']; return keys.map(k=>({k, v:localStorage.getItem(k)}))})()`)

    // Debug API
    await probe('D0-X-debug-api', `(()=>{const d=window.__deskpet_anim_debug; return d?{has:true, keys:Object.keys(d).slice(0,40)}:'no-debug'})()`)

    // Test DOM contains pet
    await probe('D0-X-dom', `({url:location.href, title:document.title, has_pet:!!document.querySelector('[data-pet-root],[data-pet],.pet,#pet'), bodyChildren: Array.from(document.body.children).map(e=>e.tagName+'#'+e.id+'.'+e.className.split(' ').slice(0,3).join('.')).slice(0,10)})`)
  } else if (cmd === 'eval') {
    await probe('eval', process.argv[3])
  } else if (cmd === 'screenshot') {
    const p = await cdp.screenshot(process.argv[3] || 'shot')
    console.log('saved', p)
  } else if (cmd === 'mouse') {
    await cdp.mouse(process.argv[3], Number(process.argv[4]), Number(process.argv[5]), process.argv[6])
  }

  await fs.writeFile(path.join(SHOT_DIR, 'd0-probes-raw.json'), JSON.stringify(results, null, 2))
  cdp.ws.close()
}

main().catch((e) => { console.error('FATAL', e); process.exit(1) })
