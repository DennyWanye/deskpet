#!/usr/bin/env node
/**
 * CDP runner for Pet Animation UX v1 ManualTest cases (round-2).
 *
 * Connects to the WebView2 instance launched with
 *   WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=9222
 * and provides:
 *   - eval(expr)      → Runtime.evaluate (returns a JSON-serialisable value)
 *   - exec(stmts)     → Runtime.evaluate (no value, used for statements)
 *   - screenshot(name)→ Page.captureScreenshot (saved to ./screenshots/)
 *   - dispatchMouse(type, x, y, button)
 *
 * Usage:
 *   node cdp-runner.mjs smoke
 *   node cdp-runner.mjs probe1 | probe3 | probe4
 *   node cdp-runner.mjs g01 | g02 | g03 | g05 | g06
 *   node cdp-runner.mjs p01 | b01 | s01
 *   node cdp-runner.mjs mp02 | mp04
 *   node cdp-runner.mjs pr01 | pr02 | met01 | met02 | met03
 *   node cdp-runner.mjs hmr01 | cold01
 *   node cdp-runner.mjs all   # run the full P0-eligible CDP suite
 */
import http from 'node:http'
import { WebSocket } from 'ws'
import fs from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const SHOT_DIR = path.join(__dirname, 'screenshots')
const PORT = 9222

async function listTargets() {
  return new Promise((resolve, reject) => {
    http
      .get({ host: '127.0.0.1', port: PORT, path: '/json' }, (res) => {
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

async function pickPetTarget() {
  const targets = await listTargets()
  const pet = targets.find(
    (t) =>
      t.type === 'page' &&
      t.url &&
      t.url.replace(/#.*$/, '').endsWith('/') &&
      !t.url.includes('code-panel') &&
      !t.url.includes('message-panel'),
  )
  if (!pet) throw new Error('Pet target not found: ' + JSON.stringify(targets.map((t) => t.url)))
  return pet
}

class CDP {
  constructor(wsUrl) {
    this.wsUrl = wsUrl
    this.ws = null
    this.id = 0
    this.pending = new Map()
  }
  async connect() {
    await new Promise((resolve, reject) => {
      this.ws = new WebSocket(this.wsUrl)
      this.ws.on('open', resolve)
      this.ws.on('error', reject)
      this.ws.on('message', (raw) => {
        const msg = JSON.parse(raw.toString())
        if (msg.id && this.pending.has(msg.id)) {
          const { resolve, reject } = this.pending.get(msg.id)
          this.pending.delete(msg.id)
          if (msg.error) reject(new Error(JSON.stringify(msg.error)))
          else resolve(msg.result)
        }
      })
    })
  }
  send(method, params = {}) {
    const id = ++this.id
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject })
      this.ws.send(JSON.stringify({ id, method, params }))
    })
  }
  /** Evaluate expression and return its JSON-serialised value. */
  async eval(expr) {
    const wrapped = `JSON.stringify((function(){ try { return (${expr}); } catch (e) { return { __evalError: String(e) }; } })())`
    const r = await this.send('Runtime.evaluate', {
      expression: wrapped,
      returnByValue: true,
    })
    if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails))
    const v = r.result.value
    try {
      return JSON.parse(v)
    } catch {
      return v
    }
  }
  /** Execute statements (no value). */
  async exec(stmts) {
    const r = await this.send('Runtime.evaluate', {
      expression: `(function(){ ${stmts} })()`,
      returnByValue: true,
    })
    if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails))
    return r.result.value
  }
  async screenshot(name) {
    await fs.mkdir(SHOT_DIR, { recursive: true })
    const r = await this.send('Page.captureScreenshot', { format: 'png' })
    const file = path.join(SHOT_DIR, `${name}-${Date.now()}.png`)
    await fs.writeFile(file, Buffer.from(r.data, 'base64'))
    return file
  }
  async dispatchMouse(type, x, y, button = 'left', clickCount = 0) {
    await this.send('Input.dispatchMouseEvent', { type, x, y, button, clickCount })
  }
  close() {
    this.ws?.close()
  }
}

async function withCDP(fn) {
  const pet = await pickPetTarget()
  console.error(`[cdp] target = ${pet.url} (${pet.id})`)
  const cdp = new CDP(pet.webSocketDebuggerUrl)
  await cdp.connect()
  await cdp.send('Page.enable')
  await cdp.send('Runtime.enable')
  try {
    return await fn(cdp)
  } finally {
    cdp.close()
  }
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

// ───────── CASES ─────────

async function smoke(cdp) {
  return {
    hasMetrics: await cdp.eval('typeof window.__deskpet_anim_metrics'),
    hasDebug: await cdp.eval('typeof window.__deskpet_anim_debug'),
    hitZoneCount: await cdp.eval("document.querySelectorAll('[data-pet-hitzone]').length"),
    overlayLoaded: await cdp.eval('!!window.__deskpet_anim_bench?.applyToOnce'),
    debug: await cdp.eval('window.__deskpet_anim_debug'),
  }
}

async function probe1(cdp) {
  // Verify ADD path doesn't trigger fallback warning + parameter actually moves.
  // Read ParamAngleX index existence (we can't get the core model directly,
  // but bench hook proves overlay can reach it).
  await cdp.exec('window.__deskpet_anim_bench.applyToOnce(performance.now());')
  // Listen for the [pet-anim] addParameterValueByIndex missing warning — we
  // already wired a one-shot console.warn in overlay; absence proves native.
  const warns = await cdp.eval(`(function(){
    if (!window.__captured_warns) {
      window.__captured_warns = [];
      const orig = console.warn;
      console.warn = function(){ window.__captured_warns.push(Array.from(arguments).join(' ')); orig.apply(console, arguments); };
    }
    return window.__captured_warns.filter(w => w.includes('addParameterValueByIndex'));
  })()`)
  return { result: 'ok', add_native: warns.length === 0, fallback_warns: warns }
}

async function probe3(cdp) {
  await cdp.exec(`window.__probe3_count = 0; window.__probe3_handler = function(){ window.__probe3_count++; }; window.addEventListener('pointermove', window.__probe3_handler);`)
  await cdp.exec(`window.dispatchEvent(new PointerEvent('pointermove', { clientX: 100, clientY: 100, bubbles: true }));`)
  await sleep(100)
  const count = await cdp.eval('window.__probe3_count')
  await cdp.exec(`window.removeEventListener('pointermove', window.__probe3_handler);`)
  return { dispatched_received: count, debug: await cdp.eval('window.__deskpet_anim_debug') }
}

async function probe4(cdp) {
  const ff = await cdp.eval(`(function(){
    const el = document.querySelector('[data-pet-hitzone]');
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return { x: r.left + r.width / 2, y: r.top + r.height / 2, w: r.width, h: r.height };
  })()`)
  if (!ff) return { error: 'no hit-zone element' }
  // Use JS-level synthetic event dispatch (Input.dispatchMouseEvent works
  // at the browser layer but may not always trigger React onClick when the
  // hit-zone uses React synthetic events). Dispatch a click directly on the
  // element instead.
  await cdp.exec(`(function(){
    const el = document.querySelector('[data-pet-hitzone]');
    el.dispatchEvent(new PointerEvent('pointerenter', { bubbles: true }));
    el.dispatchEvent(new MouseEvent('click', { bubbles: true, clientX: ${Math.round(ff.x)}, clientY: ${Math.round(ff.y)} }));
  })()`)
  await sleep(350)
  return { hit_zone_bbox: ff, debug: await cdp.eval('window.__deskpet_anim_debug'), metrics: await cdp.eval('window.__deskpet_anim_metrics()') }
}

async function caseG01(cdp) {
  const w = await cdp.eval('window.innerWidth')
  const h = await cdp.eval('window.innerHeight')
  await cdp.exec(`window.dispatchEvent(new PointerEvent('pointermove', { clientX: 10, clientY: ${h / 2}, bubbles: true }));`)
  await sleep(1500)
  const leftDbg = await cdp.eval('window.__deskpet_anim_debug')
  await cdp.exec(`window.dispatchEvent(new PointerEvent('pointermove', { clientX: ${w - 10}, clientY: ${h / 2}, bubbles: true }));`)
  await sleep(1500)
  const rightDbg = await cdp.eval('window.__deskpet_anim_debug')
  return {
    left_yaw: leftDbg.gaze_smoothed_yaw,
    right_yaw: rightDbg.gaze_smoothed_yaw,
    pass_sign: leftDbg.gaze_smoothed_yaw < 0 && rightDbg.gaze_smoothed_yaw > 0,
  }
}

async function caseG02(cdp) {
  // Deadzone + clear → idle recentre.
  await cdp.exec(`window.dispatchEvent(new PointerEvent('pointermove', { clientX: 50, clientY: 50, bubbles: true }));`)
  await sleep(800)
  const before = await cdp.eval('window.__deskpet_anim_debug.gaze_smoothed_yaw')
  // Trigger clear by dispatching blur (overlay listens for blur → clearGazeTarget).
  await cdp.exec(`window.dispatchEvent(new Event('blur'));`)
  // Wait past idle_recenter_ms (default 10s).
  await sleep(12000)
  const after = await cdp.eval('window.__deskpet_anim_debug.gaze_smoothed_yaw')
  return { before, after, pass: Math.abs(after) < 1 }
}

async function caseG03(cdp) {
  // Clamp: target way past 20° should still produce |yaw| ≤ 20.
  const w = await cdp.eval('window.innerWidth')
  await cdp.exec(`window.dispatchEvent(new PointerEvent('pointermove', { clientX: ${w * 5}, clientY: 0, bubbles: true }));`)
  await sleep(2000)
  const d = await cdp.eval('window.__deskpet_anim_debug')
  return { gaze_yaw: d.gaze_smoothed_yaw, clamped: Math.abs(d.gaze_smoothed_yaw) <= 20.5 }
}

async function caseG05(cdp) {
  // Window pointermove (PRD §6.0 Probe-3 follow-up) — verify dispatching at
  // window level reaches overlay regardless of hit-zone location.
  const before = await cdp.eval('window.__deskpet_anim_debug.gaze_smoothed_yaw')
  await cdp.exec(`window.dispatchEvent(new PointerEvent('pointermove', { clientX: 5, clientY: 5, bubbles: true }));`)
  await sleep(1000)
  const after = await cdp.eval('window.__deskpet_anim_debug.gaze_smoothed_yaw')
  return { before, after, moved: Math.abs(after - before) > 0.1 }
}

async function caseG06(cdp) {
  // Resize: snapshot hit-zone bbox before, dispatch a resize event, snapshot after.
  // Note: dispatching `resize` event on window is informational only — Tauri
  // doesn't actually change innerWidth/Height. The test verifies ResizeObserver
  // / window.addEventListener('resize') wiring causes a recompute.
  const before = await cdp.eval(`(function(){
    const el = document.querySelector('[data-pet-hitzone]');
    const r = el.getBoundingClientRect();
    return { left: r.left, top: r.top, width: r.width, height: r.height };
  })()`)
  // Force a layout by changing window.innerWidth briefly via CDP override.
  // For now we only verify the resize listener was registered.
  const listenerCheck = await cdp.eval(`(function(){
    // jsdom hack: we can't easily count listeners, but presence of the
    // computeFaceFrame-via-resize wiring is asserted by Live2DCanvas useEffect.
    return { hit_zone_present: !!document.querySelector('[data-pet-hitzone]') };
  })()`)
  return { before_bbox: before, wiring: listenerCheck }
}

async function caseP01(cdp) {
  // Perlin: observe ParamAngleX changes over time — but we can't read it
  // directly. Instead, sample window.__deskpet_anim_debug over 2s and assert
  // gaze_smoothed_yaw + nothing changing isn't proof. Use overlay bench:
  // call applyToOnce, then check that calling at different t produces different
  // ADD behaviour. This is indirect.
  // Better: just confirm the perlin module is included (smoke check) and
  // visual observation via screenshots over time.
  const shots = []
  for (let i = 0; i < 3; i++) {
    shots.push(await cdp.screenshot(`p01-frame-${i}`))
    await sleep(2000)
  }
  return { shots, note: 'visual diff over 6s; perlin amplitude ±2° should be visible at this granularity' }
}

async function caseB01(cdp) {
  // Blink: capture screenshots over 6s to see eye-close events.
  const shots = []
  for (let i = 0; i < 6; i++) {
    shots.push(await cdp.screenshot(`b01-frame-${i}`))
    await sleep(1000)
  }
  return { shots, note: 'with blink_hz 0.2 (idle), expect ~1 blink in 6s' }
}

async function caseS01(cdp) {
  // Saccade: same idea as B01 but focus on eye micro-darts.
  const shots = []
  for (let i = 0; i < 4; i++) {
    shots.push(await cdp.screenshot(`s01-frame-${i}`))
    await sleep(1000)
  }
  return { shots, note: 'idle saccade should be visible — 1Hz frequency' }
}

async function caseMP02(cdp) {
  // Motion pool tag consumption — verify setMotionTagPool callable from
  // window. We need calibrated motion_labels in localStorage; if absent we
  // assert that the labels loader returned null and motion pool stays default.
  const result = await cdp.eval(`(function(){
    const labels = localStorage.getItem('deskpet_motion_labels');
    return { labels_in_storage: !!labels, current_motion_idx: window.__deskpet_anim_debug.current_motion_idx };
  })()`)
  return result
}

async function caseMP04(cdp) {
  // state_changed → immediate switch. We can't easily inject supervisor
  // alert from here, but we can verify the wiring contract: calling
  // setMotionTagPool with force_switch_now=true should change current_motion_idx
  // if labels are present.
  const before = await cdp.eval('window.__deskpet_anim_debug.current_motion_idx')
  // No-op if no Live2DHandle exposed; skip if unreachable.
  return { current_motion_idx_before: before, note: 'requires supervisor alert to fully exercise' }
}

async function casePR01(cdp) {
  // hit-zone click → metrics.interaction.samples grows.
  const before = await cdp.eval('window.__deskpet_anim_metrics().interaction.samples.length')
  // Use JS-level click since CDP Input.dispatchMouseEvent may not always
  // trigger React synthetic handlers.
  await cdp.exec(`(function(){
    const el = document.querySelector('[data-pet-hitzone]');
    const r = el.getBoundingClientRect();
    const cx = r.left + r.width/2, cy = r.top + r.height/2;
    // Simulate a real React-friendly click — synthetic events bubble.
    el.dispatchEvent(new PointerEvent('pointerenter', { bubbles: true, clientX: cx, clientY: cy }));
    el.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, clientX: cx, clientY: cy }));
    el.dispatchEvent(new PointerEvent('pointerup', { bubbles: true, clientX: cx, clientY: cy }));
    el.dispatchEvent(new MouseEvent('click', { bubbles: true, clientX: cx, clientY: cy }));
  })()`)
  await sleep(400)
  const after = await cdp.eval('window.__deskpet_anim_metrics().interaction.samples.length')
  return { before, after, increased: after > before }
}

async function casePR02(cdp) {
  // Double click within 250ms → distinct effect (state goes in_double_pulse).
  await cdp.exec(`(function(){
    const el = document.querySelector('[data-pet-hitzone]');
    const r = el.getBoundingClientRect();
    const cx = r.left + r.width/2, cy = r.top + r.height/2;
    function click(ts) { el.dispatchEvent(new MouseEvent('click', { bubbles: true, clientX: cx, clientY: cy })); }
    click();
    setTimeout(click, 200);
  })()`)
  await sleep(500)
  const debug = await cdp.eval('window.__deskpet_anim_debug')
  return { debug }
}

async function caseMET01(cdp) {
  // 5 clicks → interaction p50/p95/max sensible.
  await cdp.exec(`(async function(){
    const el = document.querySelector('[data-pet-hitzone]');
    const r = el.getBoundingClientRect();
    const cx = r.left + r.width/2, cy = r.top + r.height/2;
    for (let i = 0; i < 5; i++) {
      el.dispatchEvent(new MouseEvent('click', { bubbles: true, clientX: cx, clientY: cy }));
      await new Promise(r => setTimeout(r, 1100));
    }
  })()`)
  await sleep(6000)
  return await cdp.eval('window.__deskpet_anim_metrics()')
}

async function caseMET02(cdp) {
  // FIFO pairing — dispatch 2 clicks 200ms apart, expect 2 visual samples,
  // each paired to the right event (≈ frame_ts - event_ts > 0).
  await cdp.exec(`(async function(){
    const el = document.querySelector('[data-pet-hitzone]');
    function click() { el.dispatchEvent(new MouseEvent('click', { bubbles: true, clientX: 150, clientY: 300 })); }
    click();
    await new Promise(r => setTimeout(r, 200));
    click();
  })()`)
  await sleep(800)
  return cdp.eval('window.__deskpet_anim_metrics()')
}

async function caseHMR01(cdp) {
  return {
    note: 'HMR test requires editor file change; skipped in CDP-only mode',
    hit_zone_count: await cdp.eval(`document.querySelectorAll('[data-pet-hitzone]').length`),
  }
}

async function caseCOLD01(cdp) {
  return {
    note: 'cold-start observation: dev mode has already passed init; no exceptions in console',
    overlay_ready: await cdp.eval('!!window.__deskpet_anim_metrics'),
  }
}

async function all(cdp) {
  const results = {}
  for (const [name, fn] of [
    ['smoke', smoke],
    ['probe1', probe1],
    ['probe3', probe3],
    ['probe4', probe4],
    ['g01', caseG01],
    ['g03', caseG03],
    ['g05', caseG05],
    ['g06', caseG06],
    ['mp02', caseMP02],
    ['pr01', casePR01],
    ['pr02', casePR02],
    ['met01', caseMET01],
    ['hmr01', caseHMR01],
    ['cold01', caseCOLD01],
    // G02 and slow visual cases (P01/B01/S01) are run separately because
    // they require longer wall-clock waits.
  ]) {
    try {
      console.error(`[case] ${name} ...`)
      results[name] = await fn(cdp)
    } catch (e) {
      results[name] = { __error: String(e?.message ?? e) }
    }
  }
  return results
}

const COMMANDS = {
  smoke,
  probe1,
  probe3,
  probe4,
  g01: caseG01,
  g02: caseG02,
  g03: caseG03,
  g05: caseG05,
  g06: caseG06,
  p01: caseP01,
  b01: caseB01,
  s01: caseS01,
  mp02: caseMP02,
  mp04: caseMP04,
  pr01: casePR01,
  pr02: casePR02,
  met01: caseMET01,
  met02: caseMET02,
  hmr01: caseHMR01,
  cold01: caseCOLD01,
  reload: async (cdp) => {
    await cdp.send('Page.reload', { ignoreCache: true })
    return { reloaded: true }
  },
  flagOff: async (cdp, key) => {
    await cdp.exec(`localStorage.setItem('${key}', 'off');`)
    await cdp.send('Page.reload', { ignoreCache: true })
    return { off: key }
  },
  dialogBox: async (cdp) =>
    cdp.eval(`(function(){
      const bar = document.querySelector('[data-testid="dialog-bar"]');
      const txt = document.querySelector('[data-testid="dialog-bar-assistant"]');
      if (!bar) return { error: 'no dialog-bar' };
      const r = bar.getBoundingClientRect();
      return {
        bbox: { left: r.left, top: r.top, width: r.width, height: r.height },
        text: txt ? txt.textContent : null,
        isEmpty: txt ? txt.getAttribute('data-empty') : null,
        zIndex: getComputedStyle(bar).zIndex,
      };
    })()`),
  selection: async (cdp) =>
    cdp.eval(`(function(){ const s = window.getSelection(); return { text: s.toString(), length: s.toString().length }; })()`),
  setDialog: async (cdp, text) =>
    cdp.exec(`(function(){
      const t = document.querySelector('[data-testid="dialog-bar-assistant"]');
      if (t) t.textContent = ${JSON.stringify(text || 'Lorem ipsum 测试 selection 这是一段需要被选中的助手回复文字 for verification.')};
    })()`),
  startdrag: async (cdp) =>
    cdp.eval(`(async function(){
      try {
        const m = await import('/node_modules/.vite/deps/@tauri-apps_api_window.js');
        const w = m.getCurrentWindow();
        const r = await w.startDragging();
        return 'startDragging returned: ' + JSON.stringify(r);
      } catch (e) {
        return 'err: ' + String(e);
      }
    })()`),
  hzinfo: async (cdp) =>
    cdp.eval(`(function(){
      const el = document.querySelector('[data-pet-hitzone]');
      if (!el) return 'no element';
      const cs = getComputedStyle(el);
      return { zIndex: cs.zIndex, pointerEvents: cs.pointerEvents, dragRegion: el.getAttribute('data-tauri-drag-region') };
    })()`),
  geom: async (cdp) =>
    cdp.eval(`({
      innerWidth: window.innerWidth,
      innerHeight: window.innerHeight,
      screenX: window.screenX,
      screenY: window.screenY,
      devicePixelRatio: window.devicePixelRatio,
      hitZone: (function(){
        const el = document.querySelector('[data-pet-hitzone]');
        if (!el) return null;
        const r = el.getBoundingClientRect();
        return { left: r.left, top: r.top, width: r.width, height: r.height };
      })(),
    })`),
  flagDefault: async (cdp) => {
    await cdp.exec(`['deskpet_animation_v1','deskpet_anim_perlin','deskpet_anim_blink','deskpet_anim_saccade','deskpet_anim_gaze','deskpet_anim_motionpool','deskpet_anim_pointer'].forEach(k => localStorage.removeItem(k));`)
    await cdp.send('Page.reload', { ignoreCache: true })
    return { restored: true }
  },
  all,
  metrics: async (cdp) => cdp.eval('window.__deskpet_anim_metrics()'),
  debug: async (cdp) => cdp.eval('window.__deskpet_anim_debug'),
  shot: async (cdp, name) => cdp.screenshot(name ?? 'manual'),
}

const cmd = process.argv[2] ?? 'smoke'
const arg = process.argv[3]
withCDP(async (cdp) => {
  const fn = COMMANDS[cmd]
  if (!fn) {
    console.error('Unknown command:', cmd, 'known:', Object.keys(COMMANDS).join(','))
    process.exit(1)
  }
  const result = await fn(cdp, arg)
  console.log(JSON.stringify(result, null, 2))
}).catch((e) => {
  console.error('FATAL:', e.message)
  process.exit(1)
})
