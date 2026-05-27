#!/usr/bin/env node
// L3 SkillStorePanel UI 真测 - 通过 CDP 9222 注入 JS + 截图
import http from 'node:http'
import { WebSocket } from 'ws'
import fs from 'node:fs/promises'
import path from 'node:path'

const PORT = 9222
const SHOT_DIR = '/g/projects/deskpet/plans/2026-05-25-pet-animation-ux-v2/evidence/round-l3-skill-ui'

async function listTargets() {
  return new Promise((resolve, reject) => {
    http.get({host:'127.0.0.1', port:PORT, path:'/json'}, (res)=>{
      let data=''; res.on('data',c=>data+=c)
      res.on('end',()=>{try{resolve(JSON.parse(data))}catch(e){reject(e)}})
    }).on('error', reject)
  })
}

async function connect(wsUrl){
  const ws = new WebSocket(wsUrl)
  await new Promise(r=>ws.once('open',r))
  let id = 0
  const pending = new Map()
  ws.on('message', (raw)=>{
    const m = JSON.parse(raw)
    if (m.id !== undefined && pending.has(m.id)) {
      pending.get(m.id)(m); pending.delete(m.id)
    }
  })
  async function send(method, params={}) {
    const myId = ++id
    return new Promise((res, rej)=>{
      pending.set(myId, (m)=>{
        if (m.error) rej(new Error(JSON.stringify(m.error)))
        else res(m.result)
      })
      ws.send(JSON.stringify({id:myId, method, params}))
    })
  }
  return {send, close: ()=>ws.close()}
}

async function evalJS(client, expr){
  const r = await client.send('Runtime.evaluate', {
    expression: expr,
    awaitPromise: true,
    returnByValue: true,
  })
  if (r.exceptionDetails) {
    throw new Error('eval failed: '+JSON.stringify(r.exceptionDetails))
  }
  return r.result.value
}

async function screenshot(client, name){
  await client.send('Page.enable')
  const r = await client.send('Page.captureScreenshot', {format:'png'})
  const p = path.join(SHOT_DIR, name+'.png')
  await fs.writeFile(p, Buffer.from(r.data, 'base64'))
  return p
}

async function main(){
  const targets = await listTargets()
  // 找主 pet 窗口 (url 是根 /)
  const mainTarget = targets.find(t => t.type==='page' && t.url.endsWith('/index.html#/') || t.url==='http://localhost:5173/' || t.url==='http://localhost:5173/index.html')
    || targets.find(t => t.type==='page' && !t.url.includes('#/'))
    || targets[0]
  console.log('[l3] connecting to:', mainTarget.url, mainTarget.id)
  const client = await connect(mainTarget.webSocketDebuggerUrl)

  // 1. baseline screenshot
  console.log('[l3] step 1: baseline screenshot')
  await screenshot(client, '01-baseline')

  // 2. SkillStorePanel 入口 — Toolbar 上的图标
  // 找所有按钮上 aria-label/title 含 skill 的
  const candidates = await evalJS(client, `
    (() => {
      const btns = Array.from(document.querySelectorAll('button, [role="button"]'))
      return btns
        .map(b => ({
          text: (b.textContent || '').trim().slice(0, 30),
          title: b.title || '',
          aria: b.getAttribute('aria-label') || '',
          testid: b.getAttribute('data-testid') || '',
          rect: b.getBoundingClientRect(),
        }))
        .filter(b => /skill|商店|技能|store/i.test(b.text + b.title + b.aria + b.testid))
        .slice(0, 10)
    })()
  `)
  console.log('[l3] skill-related buttons found:', JSON.stringify(candidates, null, 2))

  // 3. 看 ControlChannel 状态
  const chState = await evalJS(client, `
    (() => {
      // 找 backend port
      const w = window
      return {
        url: location.href,
        wsState: (w.__deskpetControlChannel && w.__deskpetControlChannel.state) || 'unknown',
        backendPort: (import.meta && import.meta.env && import.meta.env.VITE_BACKEND_PORT) || 'unknown-importmeta',
      }
    })()
  `)
  console.log('[l3] page state:', chState)

  // 4. 直接通过 fetch 或 invoke 调 Tauri 看 skill 列表
  const installedList = await evalJS(client, `
    (async () => {
      try {
        // 直接连 ws 发 skill_list_installed
        const port = 8600
        return await new Promise((resolve, reject) => {
          const ws = new WebSocket('ws://127.0.0.1:' + port + '/ws/control')
          ws.onopen = () => ws.send(JSON.stringify({type: 'skill_list_installed'}))
          ws.onmessage = (ev) => {
            try {
              const m = JSON.parse(ev.data)
              if (m.type === 'skill_list_installed_response') {
                ws.close()
                resolve({ok: true, count: (m.payload && m.payload.skills || []).length, sample: (m.payload && m.payload.skills || []).slice(0, 3)})
              }
            } catch(e) {}
          }
          ws.onerror = (e) => { resolve({ok: false, error: 'ws error'}) }
          setTimeout(() => { ws.close(); resolve({ok: false, error: 'timeout 8s'}) }, 8000)
        })
      } catch(e) {
        return {ok: false, error: e.message}
      }
    })()
  `)
  console.log('[l3] direct ws skill_list_installed:', installedList)

  client.close()
  console.log('[l3] DONE')
}

main().catch(e => {console.error('[l3] FAILED:', e); process.exit(1)})
