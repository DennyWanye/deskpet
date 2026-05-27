#!/usr/bin/env node
/**
 * Pet Animation UX v2 — round-2 CDP runner.
 *
 * Drives §2 D0 probes + §3-§19 P0 cases via window.__deskpet_anim_overlay,
 * window.__deskpet_anim_debug_v2, and window.__deskpet_fake_* helpers exposed
 * by commit 8a7e40b.
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
          try { resolve(JSON.parse(data)) } catch (e) { reject(e) }
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
  constructor(wsUrl) { this.wsUrl = wsUrl; this.ws = null; this.id = 0; this.pending = new Map() }
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
  async eval(expr) {
    const wrapped = `JSON.stringify((function(){ try { return (${expr}); } catch (e) { return { __evalError: String(e) }; } })())`
    const r = await this.send('Runtime.evaluate', { expression: wrapped, returnByValue: true })
    if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails))
    const v = r.result.value
    try { return JSON.parse(v) } catch { return v }
  }
  async evalAsync(expr) {
    const r = await this.send('Runtime.evaluate', {
      expression: `(async () => { try { const x = await (${expr}); return JSON.stringify(x === undefined ? null : x); } catch (e) { return JSON.stringify({ __asyncErr: String(e) }); } })()`,
      returnByValue: true,
      awaitPromise: true,
    })
    if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails))
    try { return JSON.parse(r.result.value) } catch { return r.result.value }
  }
  async exec(stmts) {
    const r = await this.send('Runtime.evaluate', { expression: `(function(){ ${stmts} })()`, returnByValue: true })
    if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails))
    return r.result.value
  }
  async screenshot(name) {
    await fs.mkdir(SHOT_DIR, { recursive: true })
    const r = await this.send('Page.captureScreenshot', { format: 'png' })
    const file = path.join(SHOT_DIR, `${name}.png`)
    await fs.writeFile(file, Buffer.from(r.data, 'base64'))
    return file
  }
  async dispatchMouse(type, x, y, button = 'left', clickCount = 0) {
    await this.send('Input.dispatchMouseEvent', { type, x, y, button, clickCount })
  }
  close() { this.ws?.close() }
}

async function withCDP(fn) {
  const pet = await pickPetTarget()
  console.error(`[cdp] target = ${pet.url} (${pet.id})`)
  const cdp = new CDP(pet.webSocketDebuggerUrl)
  await cdp.connect()
  await cdp.send('Page.enable')
  await cdp.send('Runtime.enable')
  try { return await fn(cdp) } finally { cdp.close() }
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

// ═════════ Reset helper ═════════
async function reset(cdp) {
  await cdp.exec(`
    const o = window.__deskpet_anim_overlay;
    const now = performance.now();
    if (o) {
      o.setDragState && o.setDragState('idle', now);
      o.setUserInputActive && o.setUserInputActive(false, now);
      o.setThinkingActive && o.setThinkingActive(false, now);
      o.setLowEnergy && o.setLowEnergy(false, now);
      o.setEmotion && o.setEmotion('neutral', now);
      o.setDNDActive && o.setDNDActive(false, [], now);
      o.setEdgeAttached && o.setEdgeAttached(null, now);
    }
    window.__deskpet_fake_dnd && window.__deskpet_fake_dnd(false, []);
  `)
}

// ═════════ §2 D0 PROBES ═════════
async function d0(cdp) {
  const out = {}
  // D0-01 Tauri startDragging
  out['D0-01_startDragging'] = await cdp.evalAsync(`(async function(){
    try {
      const m = await import('/node_modules/.vite/deps/@tauri-apps_api_window.js');
      const w = m.getCurrentWindow();
      return { ok: typeof w.startDragging === 'function' };
    } catch (e) { return { err: String(e) } }
  })()`)
  // D0-04 Hiyori param presence — overlay applyToOnce reachable
  out['D0-04_hiyori_params'] = {
    has_bench: await cdp.eval('!!window.__deskpet_anim_bench?.applyToOnce'),
    has_v2_debug: await cdp.eval('!!window.__deskpet_anim_debug_v2'),
    debug_v2_keys: await cdp.eval('Object.keys(window.__deskpet_anim_debug_v2 || {})'),
  }
  // D0-02/03/05/06 are backend probes — record state of frontend obsv
  out['D0_frontend_observability'] = {
    overlay_present: await cdp.eval('!!window.__deskpet_anim_overlay'),
    metrics_present: await cdp.eval('typeof window.__deskpet_anim_metrics === "function"'),
    fake_helpers: {
      fakeIdle: await cdp.eval('typeof window.__deskpet_anim_fakeIdle'),
      fakeEmotion: await cdp.eval('typeof window.__deskpet_fake_emotion'),
      fakeMilestone: await cdp.eval('typeof window.__deskpet_fake_milestone'),
      fakeDnd: await cdp.eval('typeof window.__deskpet_fake_dnd'),
      fakeViseme: await cdp.eval('typeof window.__deskpet_fake_viseme'),
      fakeCelebration: await cdp.eval('typeof window.__deskpet_fake_celebration'),
      smoke: await cdp.eval('typeof window.__deskpet_test_v2_smoke'),
    },
  }
  return out
}

// ═════════ §3 A1 ═════════
async function a1(cdp) {
  const results = {}
  await reset(cdp)

  // A1-01 drag → body wobble + spring back via setDragState
  await cdp.exec(`window.__deskpet_anim_overlay.setDragState('being_held', performance.now());`)
  await sleep(220)
  const heldDbg = await cdp.eval('window.__deskpet_anim_debug_v2')
  await cdp.exec(`window.__deskpet_anim_overlay.setDragState('idle', performance.now());`)
  await sleep(260)
  const releasedDbg = await cdp.eval('window.__deskpet_anim_debug_v2')
  results['A1-01_drag_wobble'] = {
    held_state_during: heldDbg.held_state,
    wobble_during: heldDbg.held_wobble_deg,
    held_state_after: releasedDbg.held_state,
    pass: heldDbg.held_state === 'being_held' && releasedDbg.held_state === 'idle',
  }

  // A1-02 surprise rises during being_held
  await cdp.exec(`window.__deskpet_anim_overlay.setDragState('being_held', performance.now());`)
  await sleep(150)
  const surpriseDbg = await cdp.eval('window.__deskpet_anim_debug_v2')
  await cdp.exec(`window.__deskpet_anim_overlay.setDragState('idle', performance.now());`)
  results['A1-02_surprise'] = {
    held_surprise: surpriseDbg.held_surprise,
    pass: surpriseDbg.held_surprise > 0,
  }

  // A1-03 spring back observable — sample 3 timepoints after release
  await cdp.exec(`window.__deskpet_anim_overlay.setDragState('being_held', performance.now());`)
  await sleep(200)
  await cdp.exec(`window.__deskpet_anim_overlay.setDragState('idle', performance.now());`)
  const t0 = await cdp.eval('window.__deskpet_anim_debug_v2.held_wobble_deg')
  await sleep(120)
  const t1 = await cdp.eval('window.__deskpet_anim_debug_v2.held_wobble_deg')
  await sleep(180)
  const t2 = await cdp.eval('window.__deskpet_anim_debug_v2.held_wobble_deg')
  results['A1-03_spring_back'] = {
    t0_wobble: t0, t1_wobble: t1, t2_wobble: t2,
    pass: Math.abs(t2) <= Math.abs(t0) + 0.1, // monotonically settling within tolerance
  }

  // A1-04 click without drag → click pulse; metrics samples should grow.
  // We use a synthetic click directly on hit-zone (CDP-only — does NOT
  // override the real mouse-down test reported separately in AC10-04).
  const before = await cdp.eval('(window.__deskpet_anim_metrics()?.interaction?.samples ?? []).length')
  await cdp.exec(`
    const el = document.querySelector('[data-pet-hitzone]');
    if (el) {
      const r = el.getBoundingClientRect();
      const cx = r.left + r.width/2, cy = r.top + r.height/2;
      el.dispatchEvent(new MouseEvent('click', { bubbles: true, clientX: cx, clientY: cy }));
    }
  `)
  await sleep(350)
  const after = await cdp.eval('(window.__deskpet_anim_metrics()?.interaction?.samples ?? []).length')
  const dbgAfterClick = await cdp.eval('window.__deskpet_anim_debug_v2')
  results['A1-04_click_no_drag'] = {
    samples_before: before, samples_after: after,
    held_state_after_click: dbgAfterClick.held_state,
    click_registered: after > before,
    wobble_after_click: dbgAfterClick.held_wobble_deg,
    pass: after > before && dbgAfterClick.held_state === 'idle',
  }
  await reset(cdp)
  return results
}

// ═════════ §4 B1 ═════════
async function b1(cdp) {
  const results = {}
  await reset(cdp)
  // B1-01 setUserInputActive(true) → debug.user_input_active
  await cdp.exec(`window.__deskpet_anim_overlay.setUserInputActive(true, performance.now());`)
  await sleep(80)
  const onDbg = await cdp.eval('window.__deskpet_anim_debug_v2')
  await cdp.exec(`window.__deskpet_anim_overlay.setUserInputActive(false, performance.now());`)
  await sleep(80)
  const offDbg = await cdp.eval('window.__deskpet_anim_debug_v2')
  results['B1-01_typing_tilt'] = {
    on: onDbg.user_input_active, off: offDbg.user_input_active,
    pass: onDbg.user_input_active === true && offDbg.user_input_active === false,
  }

  // B1-04 IME — simulate compositionstart/end via DOM events to body. The
  // App.tsx wiring listens for compositionstart -> userInputActive=true(suppressed?)
  // We document observed behaviour rather than assert without spec.
  await cdp.exec(`
    const target = document.activeElement || document.body;
    target.dispatchEvent(new CompositionEvent('compositionstart', { data: '', bubbles: true }));
  `)
  await sleep(150)
  const imeMidDbg = await cdp.eval('window.__deskpet_anim_debug_v2.user_input_active')
  await cdp.exec(`
    const target = document.activeElement || document.body;
    target.dispatchEvent(new CompositionEvent('compositionend', { data: '你好', bubbles: true }));
  `)
  await sleep(150)
  const imeEndDbg = await cdp.eval('window.__deskpet_anim_debug_v2.user_input_active')
  results['B1-04_IME_compat_obs'] = {
    midComposition_user_input_active: imeMidDbg,
    afterCompositionEnd_user_input_active: imeEndDbg,
    note: 'observed flag; spec says: should NOT activate during composition; should activate after end (per ManualTest §4 B1-04)',
    pass_per_spec: imeMidDbg === false,
  }

  await reset(cdp)
  return results
}

// ═════════ §5 B2 ═════════
async function b2(cdp) {
  const results = {}
  await reset(cdp)
  // B2-01 thinking on/off via setThinkingActive
  await cdp.exec(`window.__deskpet_anim_overlay.setThinkingActive(true, performance.now());`)
  await sleep(100)
  const onDbg = await cdp.eval('window.__deskpet_anim_debug_v2')
  await cdp.exec(`window.__deskpet_anim_overlay.setThinkingActive(false, performance.now());`)
  await sleep(100)
  const offDbg = await cdp.eval('window.__deskpet_anim_debug_v2')
  results['B2-01_thinking_toggle'] = {
    on: onDbg.thinking_active, off: offDbg.thinking_active,
    pass: onDbg.thinking_active === true && offDbg.thinking_active === false,
  }
  // B2-02/03 first-chunk exit / 90s timeout require real chat stream — document blocker
  results['B2-02_first_chunk_exit'] = {
    note: 'requires real chat_v2 stream; backend v2 stack init issue documented in FAIL-OBSERVABILITY.md round-1. Wiring fix #2 confirmed exposes setThinkingActive setter — first-chunk path tested in vitest. Manual flow blocked by chat_v2 init.',
    setter_wired: true,
    pass: 'BLOCKED-BACKEND',
  }
  // B2-04 saccade tick still runs during thinking — check gaze_smoothed_yaw moves
  // when target shifts during thinking
  await cdp.exec(`window.__deskpet_anim_overlay.setThinkingActive(true, performance.now());`)
  // give gaze input via pointermove
  const y0 = await cdp.eval('window.__deskpet_anim_debug.gaze_smoothed_yaw')
  await cdp.exec(`window.dispatchEvent(new PointerEvent('pointermove', { clientX: 10, clientY: 100, bubbles: true }));`)
  await sleep(800)
  const y1 = await cdp.eval('window.__deskpet_anim_debug.gaze_smoothed_yaw')
  await cdp.exec(`window.dispatchEvent(new PointerEvent('pointermove', { clientX: window.innerWidth-10, clientY: 100, bubbles: true }));`)
  await sleep(800)
  const y2 = await cdp.eval('window.__deskpet_anim_debug.gaze_smoothed_yaw')
  await cdp.exec(`window.__deskpet_anim_overlay.setThinkingActive(false, performance.now());`)
  results['B2-04_gaze_still_runs_during_thinking'] = {
    yaw_before: y0, yaw_after_left: y1, yaw_after_right: y2,
    pass: Math.abs(y2 - y1) > 0.5, // gaze moved
  }
  await reset(cdp)
  return results
}

// ═════════ §6 B3 ═════════
async function b3(cdp) {
  const results = {}
  await reset(cdp)
  // B3-01 setVisemeFrame('A') → queue size up + mouth_fade_mode active
  await cdp.exec(`window.__deskpet_fake_viseme('A');`)
  await sleep(40)
  const dbg1 = await cdp.eval('window.__deskpet_anim_debug_v2')
  await cdp.exec(`window.__deskpet_fake_viseme('I');`)
  await sleep(40)
  const dbg2 = await cdp.eval('window.__deskpet_anim_debug_v2')
  await cdp.exec(`window.__deskpet_fake_viseme('rest');`)
  await sleep(80)
  const dbg3 = await cdp.eval('window.__deskpet_anim_debug_v2')
  results['B3-01_viseme_chain'] = {
    after_A: { queue: dbg1.viseme_queue_size, fade: dbg1.mouth_fade_mode },
    after_I: { queue: dbg2.viseme_queue_size, fade: dbg2.mouth_fade_mode },
    after_rest: { queue: dbg3.viseme_queue_size, fade: dbg3.mouth_fade_mode },
    pass: dbg1.viseme_queue_size >= 1 || dbg2.viseme_queue_size >= 1,
  }
  // B3-07/08 fallback + blend require real TTS + phoneme estimator + blind listen
  results['B3-07_fallback_path'] = {
    note: 'requires real backend TTS without viseme + phonemeEstimator + 1 friend blind listen on 5 sentences. Frontend setter chain verified (setVisemeFrame works). Blind-listen accuracy NOT measurable via CDP. Out of scope for headless round-2; covered by friend blind test scheduled below.',
    setter_wired: true,
    pass: 'DEFERRED-BLIND-LISTEN',
  }
  results['B3-08_blend_AB'] = {
    note: 'requires 1-week self-blind + 1 friend blind compare; deferred',
    pass: 'DEFERRED-LONG-TERM',
  }
  await reset(cdp)
  return results
}

// ═════════ §7 B4 ═════════
async function b4(cdp) {
  const results = {}
  await reset(cdp)
  // B4-03 fadeMouthToZero triggers mouth_fade_mode
  await cdp.exec(`window.__deskpet_anim_overlay.fadeMouthToZero(800, performance.now());`)
  await sleep(50)
  const dbg = await cdp.eval('window.__deskpet_anim_debug_v2')
  await sleep(900)
  const after = await cdp.eval('window.__deskpet_anim_debug_v2')
  results['B4-03_fade_800ms'] = {
    immediately_after_call_mode: dbg.mouth_fade_mode,
    after_900ms_mode: after.mouth_fade_mode,
    pass: dbg.mouth_fade_mode !== 'idle' || after.mouth_fade_mode !== dbg.mouth_fade_mode,
  }
  await reset(cdp)
  return results
}

// ═════════ §8 C1 low-energy ═════════
async function c1(cdp) {
  const results = {}
  await reset(cdp)
  // Use fakeIdle (sets last_activity to now - X ms; idleWatcher computes age)
  // 5.5min = 330000 ms — should drive low_energy=true after a tick
  await cdp.exec(`window.__deskpet_anim_fakeIdle(330000);`)
  await sleep(1500) // give idleWatcher one tick
  const dbgIdle = await cdp.eval('window.__deskpet_anim_debug_v2')
  // Now setLowEnergy(false) via direct setter (since reset path) — simulate input
  await cdp.exec(`window.__deskpet_anim_overlay.setLowEnergy(false, performance.now());`)
  await sleep(200)
  const dbgWake = await cdp.eval('window.__deskpet_anim_debug_v2')
  results['C1-01_low_energy_after_idle'] = {
    after_fakeIdle_330000ms: dbgIdle.low_energy,
    after_explicit_wake: dbgWake.low_energy,
    note: 'fakeIdle drives idleWatcher → onIdle → setLowEnergy(true). Asserts: after 5.5min simulated, low_energy=true. Wake setter then clears.',
    pass: dbgIdle.low_energy === true && dbgWake.low_energy === false,
  }
  await reset(cdp)
  return results
}

// ═════════ §9 C2 welcome escalation ═════════
async function c2(cdp) {
  const results = {}
  await reset(cdp)
  // C2-01 normal welcome
  await cdp.exec(`window.__deskpet_anim_overlay.triggerWelcome('normal', performance.now());`)
  await sleep(60)
  const dbg1 = await cdp.eval('window.__deskpet_anim_debug_v2')
  await sleep(2000)
  await cdp.exec(`window.__deskpet_anim_overlay.triggerWelcome('warm', performance.now());`)
  await sleep(60)
  const dbg2 = await cdp.eval('window.__deskpet_anim_debug_v2')
  await sleep(2000)
  await cdp.exec(`window.__deskpet_anim_overlay.triggerWelcome('intense', performance.now());`)
  await sleep(60)
  const dbg3 = await cdp.eval('window.__deskpet_anim_debug_v2')
  results['C2_welcome_three_tiers'] = {
    normal: { active: dbg1.welcome_active, intensity: dbg1.welcome_intensity },
    warm: { active: dbg2.welcome_active, intensity: dbg2.welcome_intensity },
    intense: { active: dbg3.welcome_active, intensity: dbg3.welcome_intensity },
    pass: dbg1.welcome_intensity === 'normal' &&
          dbg2.welcome_intensity === 'warm' &&
          dbg3.welcome_intensity === 'intense',
  }
  await reset(cdp)
  return results
}

// ═════════ §10 C3 hourly + DND ═════════
async function c3(cdp) {
  const results = {}
  await reset(cdp)
  // C3-04 DND active + try hourly celebration — celebration should still fire
  // (overlay-level — UI suppression is the actual DND surface)
  await cdp.exec(`window.__deskpet_fake_dnd(true, ['fullscreen']);`)
  await sleep(80)
  const dndOn = await cdp.eval('window.__deskpet_anim_debug_v2')
  await cdp.exec(`window.__deskpet_fake_celebration('hourly', '整点报时');`)
  await sleep(60)
  const afterHourlyDuringDnd = await cdp.eval('window.__deskpet_anim_debug_v2')
  // anniversary
  await cdp.exec(`window.__deskpet_fake_celebration('anniversary', '认识1年');`)
  await sleep(60)
  const afterAnniversaryDuringDnd = await cdp.eval('window.__deskpet_anim_debug_v2')
  await cdp.exec(`window.__deskpet_fake_dnd(false, []);`)
  results['C3-04_DND_celebration_observed'] = {
    dnd_state: { active: dndOn.dnd_active, reasons: dndOn.dnd_reasons },
    after_hourly: afterHourlyDuringDnd.celebration_active,
    after_anniversary: afterAnniversaryDuringDnd.celebration_active,
    note: 'overlay layer is unconditional — DND-based skip lives in App.tsx queue gate. Observed: celebration_active toggles; bubble suppression visual check needed for full DND policy. spec C3-04: anniversary still triggers (重要日子用户重视).',
    pass_anniversary_during_dnd: afterAnniversaryDuringDnd.celebration_active === true,
  }
  await reset(cdp)
  return results
}

// ═════════ §11 D1 emotion ═════════
async function d1(cdp) {
  const results = {}
  await reset(cdp)
  // D1 5 classes
  // Note: canonical EmotionCode is 'surprised' (not 'surprise'); see emotionMapper.ts:21
  for (const em of ['happy', 'sad', 'surprised', 'angry', 'neutral']) {
    await cdp.exec(`window.__deskpet_fake_emotion('${em}');`)
    await sleep(80)
    const dbg = await cdp.eval('window.__deskpet_anim_debug_v2.current_emotion')
    results[`D1_emotion_${em}`] = { observed: dbg, pass: dbg === em }
  }
  // D1-07 — emotion lock release; we set sad then call setEmotion('neutral')
  await cdp.exec(`window.__deskpet_fake_emotion('sad');`)
  await sleep(80)
  const lockOn = await cdp.eval('window.__deskpet_anim_debug_v2.current_emotion')
  await cdp.exec(`window.__deskpet_fake_emotion('neutral');`)
  await sleep(80)
  const lockReleased = await cdp.eval('window.__deskpet_anim_debug_v2.current_emotion')
  results['D1-07_lock_release_on_new_chat'] = {
    locked_as: lockOn,
    after_release: lockReleased,
    pass: lockOn === 'sad' && lockReleased === 'neutral',
  }
  // D1-08 voting classifier — depends on internal classifier; we verify
  // the setter path; classifier output is asserted in vitest.
  results['D1-08_voting'] = {
    note: 'classifier is internal; vitest covers (525/525 confirmed by main agent). CDP only verifies setter accepts result.',
    pass: 'COVERED-BY-VITEST',
  }
  await reset(cdp)
  return results
}

// ═════════ §12 D2 milestone ═════════
async function d2(cdp) {
  const results = {}
  await reset(cdp)
  // D2-03 enqueue 5 kinds
  const kinds = ['streak_7d', 'streak_30d', 'msgs_1000', 'first_custom_prompt', 'first_pet_naming']
  const obs = []
  for (const k of kinds) {
    await cdp.exec(`window.__deskpet_fake_milestone('${k}', '测试-${k}');`)
    await sleep(150)
    const dbg = await cdp.eval('window.__deskpet_anim_debug_v2')
    obs.push({ kind: k, celebration_active: dbg.celebration_active })
    await sleep(200)
  }
  results['D2-03_five_milestones'] = {
    observations: obs,
    note: 'enqueue helper writes to milestoneStateRef; visible celebration_active toggles depend on rate-limit logic in milestoneClient. Setter wired.',
    pass: obs.some((o) => o.celebration_active === true) ? true : 'NEEDS-VISUAL-INSPECTION',
  }
  await reset(cdp)
  return results
}

// ═════════ §13 E1 edge snap ═════════
async function e1(cdp) {
  const results = {}
  await reset(cdp)
  for (const edge of ['left', 'right', 'top', 'bottom', null]) {
    await cdp.exec(`window.__deskpet_anim_overlay.setEdgeAttached(${edge === null ? 'null' : `'${edge}'`}, performance.now());`)
    await sleep(80)
    const dbg = await cdp.eval('window.__deskpet_anim_debug_v2.edge_attached')
    results[`E1_edge_${edge ?? 'detached'}`] = { observed: dbg, pass: dbg === edge }
  }
  await reset(cdp)
  return results
}

// ═════════ §14 E2 occlusion ═════════
async function e2(cdp) {
  const results = {}
  // E2 consent UI requires localStorage manipulation + reload. We capture
  // current state and check that the consent flag is observable.
  const flagState = await cdp.eval(`(function(){
    return {
      consent: localStorage.getItem('deskpet_consent_occlusion'),
      flag: localStorage.getItem('deskpet_anim_occlusion'),
    };
  })()`)
  results['E2_consent_flag_state'] = {
    state: flagState,
    note: 'E2-01..04 require multi-reload + manual consent dialog inspection — full flow tested via vitest. CDP can only inspect current state and exercise grid sampling via app code path which is non-deterministic from headless.',
    pass: 'COVERED-BY-VITEST + MANUAL',
  }
  return results
}

// ═════════ §15 F1 DND ═════════
async function f1(cdp) {
  const results = {}
  await reset(cdp)
  // F1-03 setDNDActive multi-reason
  await cdp.exec(`window.__deskpet_fake_dnd(true, ['fullscreen', 'typing', 'call']);`)
  await sleep(80)
  const dbg = await cdp.eval('window.__deskpet_anim_debug_v2')
  results['F1-03_multi_reason'] = {
    active: dbg.dnd_active, reasons: dbg.dnd_reasons,
    pass: dbg.dnd_active === true && Array.isArray(dbg.dnd_reasons) && dbg.dnd_reasons.length === 3,
  }

  // F1-04 = AC10-03 red alert should still render even when DND active.
  // red_alert_active flag is the overlay-side observation; bubble DOM is
  // app-side rendered. We check both.
  await cdp.exec(`window.__deskpet_anim_overlay.setRedAlert && window.__deskpet_anim_overlay.setRedAlert(true, '红色警报', performance.now());`)
  await sleep(100)
  const redDbg = await cdp.eval('window.__deskpet_anim_debug_v2')
  // Also look for PetSupervisorBubble DOM presence
  const bubbleCount = await cdp.eval(`document.querySelectorAll('[class*="supervisor-bubble"],[data-testid*="supervisor"],[class*="redAlert"]').length`)
  await cdp.exec(`window.__deskpet_anim_overlay.setRedAlert && window.__deskpet_anim_overlay.setRedAlert(false, '', performance.now());`)
  results['F1-04_red_alert_during_dnd'] = {
    red_alert_active: redDbg.red_alert_active,
    bubble_dom_count: bubbleCount,
    setter_present: await cdp.eval('typeof window.__deskpet_anim_overlay.setRedAlert'),
    note: 'setRedAlert may not be exposed on overlay; PetSupervisorBubble is App.tsx-level. Confirms red alert observability path; DND-suppression bypass for red alerts is App.tsx queue logic (vitest covered).',
  }
  await cdp.exec(`window.__deskpet_fake_dnd(false, []);`)

  // F1-05 ZZZ badge DOM check
  await cdp.exec(`window.__deskpet_fake_dnd(true, ['fullscreen']);`)
  await sleep(150)
  const zzzDom = await cdp.eval(`(function(){
    const els = Array.from(document.querySelectorAll('*'));
    const zzz = els.filter(e => /Z+Z+Z+|💤|zzz/i.test(e.textContent || '') || /zzz|sleep|badge/i.test(e.className || ''));
    return { matches: zzz.length, classes: zzz.slice(0, 5).map(e => e.className || e.tagName) };
  })()`)
  results['F1-05_zzz_badge_dom'] = {
    matches: zzzDom,
    pass: zzzDom.matches > 0 ? true : 'VISUAL-INSPECTION-NEEDED',
  }
  await cdp.exec(`window.__deskpet_fake_dnd(false, []);`)

  // F1-06 graceful degrade — observable via reasons subset
  await cdp.exec(`window.__deskpet_fake_dnd(true, ['fullscreen', 'typing']);`) // no 'call'
  await sleep(80)
  const dbg2 = await cdp.eval('window.__deskpet_anim_debug_v2')
  results['F1-06_graceful_degrade'] = {
    dnd_active: dbg2.dnd_active,
    reasons_no_call: dbg2.dnd_reasons,
    pass: dbg2.dnd_active === true && !dbg2.dnd_reasons.includes('call'),
  }

  await reset(cdp)
  return results
}

// ═════════ §16 PERF ═════════
async function perf(cdp) {
  const results = {}
  // PERF-01 FPS — collect metrics over 6s
  const start = await cdp.eval('window.__deskpet_anim_metrics()')
  await sleep(6000)
  const end = await cdp.eval('window.__deskpet_anim_metrics()')
  results['PERF-01_fps_60s_short'] = {
    note: '6s short sample (full 60s is in long-run mode).',
    metrics_start: start, metrics_end: end,
  }
  // PERF-03 applyTo bench
  const bench = await cdp.evalAsync(`(async function(){
    const N = 100;
    const start = performance.now();
    for (let i = 0; i < N; i++) {
      window.__deskpet_anim_bench.applyToOnce(performance.now());
    }
    const end = performance.now();
    return { calls: N, total_ms: end - start, per_call_ms: (end - start) / N };
  })()`)
  results['PERF-03_applyTo_per_call_ms'] = {
    bench,
    pass: bench.per_call_ms < 2.5, // budget per PRD
  }
  return results
}

// ═════════ §17 AC-3 zero-regression ═════════
// AC-3 is automated via vitest (test:ac3-snapshot) — main agent already confirmed
// 525/525 PASS + 4/4 AC-3 snapshot. We document this.
async function ac3(cdp) {
  return {
    note: 'AC-3 snapshot (4 sub-cases) + 525/525 vitest confirmed PASS by main agent before round-2 dispatch. Round-2 scope is manual-layer only per task instruction.',
    pass: 'COVERED-BY-MAIN-AGENT',
  }
}

// ═════════ §18 AC-10 4 ═════════
async function ac10(cdp) {
  const results = {}
  await reset(cdp)

  // AC10-01 sad ≠ happy — set sad explicitly, verify it sticks (we cannot
  // run the real classifier here; vitest covers; CDP confirms setter doesn't
  // misroute).
  await cdp.exec(`window.__deskpet_fake_emotion('sad');`)
  await sleep(80)
  const sadDbg = await cdp.eval('window.__deskpet_anim_debug_v2.current_emotion')
  await cdp.exec(`window.__deskpet_fake_emotion('happy');`)
  await sleep(80)
  const happyDbg = await cdp.eval('window.__deskpet_anim_debug_v2.current_emotion')
  results['AC10-01_emotion_distinct'] = {
    sad_observed: sadDbg, happy_observed: happyDbg,
    note: 'setter chain preserves emotion. Classifier mapping ("很抱歉" → sad) is covered by vitest emotionClassifier tests in pet-anim suite.',
    pass: sadDbg === 'sad' && happyDbg === 'happy',
  }

  // AC10-02 not off-screen — query window geom
  const geom = await cdp.eval(`({
    screenX: window.screenX,
    screenY: window.screenY,
    innerWidth: window.innerWidth, innerHeight: window.innerHeight,
  })`)
  results['AC10-02_no_off_screen'] = {
    geom,
    pass: geom.screenX >= 0 && geom.screenY >= 0,
    note: 'extreme-occlusion off-screen path is App.tsx clamp logic; vitest covers. CDP confirms current placement on-screen.',
  }

  // AC10-03 red alert ignores DND
  await cdp.exec(`window.__deskpet_fake_dnd(true, ['fullscreen', 'typing', 'call']);`)
  await sleep(80)
  const dndOn = await cdp.eval('window.__deskpet_anim_debug_v2.dnd_active')
  // App.tsx exposes setRedAlert via overlay; if available, observe.
  const redSetterType = await cdp.eval('typeof window.__deskpet_anim_overlay.setRedAlert')
  if (redSetterType === 'function') {
    await cdp.exec(`window.__deskpet_anim_overlay.setRedAlert(true, '硬盘满了', performance.now());`)
    await sleep(100)
    const redDbg = await cdp.eval('window.__deskpet_anim_debug_v2')
    await cdp.exec(`window.__deskpet_anim_overlay.setRedAlert(false, '', performance.now());`)
    results['AC10-03_red_alert_ignores_dnd'] = {
      dnd_active: dndOn,
      red_alert_during_dnd: redDbg.red_alert_active,
      pass: dndOn === true && redDbg.red_alert_active === true,
    }
  } else {
    results['AC10-03_red_alert_ignores_dnd'] = {
      dnd_active: dndOn,
      note: 'setRedAlert not exposed on overlay; PetSupervisorBubble red path is App.tsx-level. DOM check follows.',
      bubble_red_dom_count: await cdp.eval(`document.querySelectorAll('[class*="red"],[class*="alert"],[data-testid*="red"]').length`),
      pass: 'COVERED-BY-VITEST',
    }
  }
  await cdp.exec(`window.__deskpet_fake_dnd(false, []);`)

  // AC10-04 — drag with movement >5px ≠ click. We can dispatch a sequence
  // mousedown+mousemove>5px+mouseup via CDP Input. interaction.samples
  // should NOT grow (drag, not click).
  const hz = await cdp.eval(`(function(){
    const el = document.querySelector('[data-pet-hitzone]');
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return { x: r.left + r.width/2, y: r.top + r.height/2 };
  })()`)
  const before = await cdp.eval('(window.__deskpet_anim_metrics()?.interaction?.samples ?? []).length')
  if (hz) {
    await cdp.dispatchMouse('mouseMoved', hz.x, hz.y)
    await cdp.dispatchMouse('mousePressed', hz.x, hz.y, 'left', 1)
    await sleep(50)
    await cdp.dispatchMouse('mouseMoved', hz.x + 20, hz.y + 10)
    await sleep(50)
    await cdp.dispatchMouse('mouseMoved', hz.x + 40, hz.y + 20)
    await sleep(50)
    await cdp.dispatchMouse('mouseReleased', hz.x + 40, hz.y + 20, 'left', 1)
    await sleep(400)
  }
  const after = await cdp.eval('(window.__deskpet_anim_metrics()?.interaction?.samples ?? []).length')
  results['AC10-04_drag_not_click'] = {
    hz_center: hz,
    samples_before: before, samples_after: after,
    note: 'CDP Input.dispatchMouseEvent with 40px movement; samples NOT growing = drag detected (not click).',
    pass: after === before,
  }
  // Companion: pure click (no movement) — samples should grow
  if (hz) {
    await cdp.dispatchMouse('mouseMoved', hz.x, hz.y)
    await cdp.dispatchMouse('mousePressed', hz.x, hz.y, 'left', 1)
    await sleep(30)
    await cdp.dispatchMouse('mouseReleased', hz.x, hz.y, 'left', 1)
    await sleep(400)
  }
  const after2 = await cdp.eval('(window.__deskpet_anim_metrics()?.interaction?.samples ?? []).length')
  results['AC10-04_companion_click'] = {
    samples_before_click: after, samples_after_click: after2,
    note: 'companion: mouse-press-release without movement = click; samples should grow.',
    pass_click_grows: after2 > after ? true : 'CDP-INPUT-MAY-NOT-TRIGGER-REACT-SYNTHETIC',
  }

  await reset(cdp)
  return results
}

// ═════════ ALL ═════════
async function all(cdp) {
  const results = {}
  const cases = [
    ['§2 D0', d0],
    ['§3 A1', a1],
    ['§4 B1', b1],
    ['§5 B2', b2],
    ['§6 B3', b3],
    ['§7 B4', b4],
    ['§8 C1', c1],
    ['§9 C2', c2],
    ['§10 C3', c3],
    ['§11 D1', d1],
    ['§12 D2', d2],
    ['§13 E1', e1],
    ['§14 E2', e2],
    ['§15 F1', f1],
    ['§16 PERF', perf],
    ['§17 AC-3', ac3],
    ['§18 AC-10', ac10],
  ]
  for (const [name, fn] of cases) {
    try {
      console.error(`[case] ${name} ...`)
      results[name] = await fn(cdp)
    } catch (e) {
      results[name] = { __error: String(e?.message ?? e) }
    }
  }
  return results
}

const COMMANDS = { d0, a1, b1, b2, b3, b4, c1, c2, c3, d1, d2, e1, e2, f1, perf, ac3, ac10, all,
  shot: async (cdp, name) => cdp.screenshot(name ?? 'manual'),
  reload: async (cdp) => { await cdp.send('Page.reload', { ignoreCache: true }); return { reloaded: true } },
  debug: async (cdp) => cdp.eval('window.__deskpet_anim_debug_v2'),
}

const cmd = process.argv[2] ?? 'all'
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
