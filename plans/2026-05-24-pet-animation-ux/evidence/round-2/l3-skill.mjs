#!/usr/bin/env node
// L3 SkillStorePanel UI 真测 via CDP 9222
import http from 'node:http'
import { WebSocket } from 'ws'
import fs from 'node:fs/promises'
import path from 'node:path'

const PORT = 9222
const SHOT_DIR = 'G:/projects/deskpet/plans/2026-05-25-pet-animation-ux-v2/evidence/round-l3-skill-ui'

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
    expression: '(async () => { return await (async () => { ' + expr + ' })() })()',
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
  console.log('  shot:', p)
  return p
}

async function main(){
  const targets = await listTargets()
  const mainTarget = targets.find(t => t.type==='page' && t.url==='http://localhost:5173/') || targets[0]
  console.log('[l3] connecting to:', mainTarget.url)
  const client = await connect(mainTarget.webSocketDebuggerUrl)
  const results = []

  // === 1. baseline ===
  console.log('[l3] CASE-01: baseline screenshot')
  await screenshot(client, '01-baseline')
  results.push({case:'01-baseline', pass: true})

  // === 2. click skill-store-toggle ===
  console.log('[l3] CASE-02: open SkillStorePanel via toolbar button')
  const opened = await evalJS(client, `
    const btn = document.querySelector('[data-testid="skill-store-toggle"]')
    if (!btn) return {ok: false, error: 'toggle not found'}
    btn.click()
    await new Promise(r => setTimeout(r, 500))
    const panel = document.querySelector('[data-testid="skill-store-panel"], .skill-store-panel')
      || Array.from(document.querySelectorAll('*')).find(el => /technique|技能商店|skill.?store/i.test(el.textContent || '') && el.children.length > 3)
    return {ok: !!panel, panelTag: panel ? panel.tagName : null, html: panel ? panel.outerHTML.slice(0, 200) : null}
  `)
  console.log('  →', opened)
  await screenshot(client, '02-panel-opened')
  results.push({case:'02-panel-opened', pass: opened.ok, detail: opened})

  // === 3. installed tab — list ===
  console.log('[l3] CASE-03: installed tab list')
  const installedTab = await evalJS(client, `
    // 找含 "已安装" 文本的 button/element 并 click
    const els = Array.from(document.querySelectorAll('button, [role="tab"], .tab'))
    const t = els.find(e => /已安装|installed/i.test(e.textContent || ''))
    if (t) t.click()
    await new Promise(r => setTimeout(r, 800))
    // 查 installed skill 列表
    const items = Array.from(document.querySelectorAll('[data-testid*="skill-item"], .skill-card, [data-skill-name]'))
    const names = items.map(el => el.textContent.trim().slice(0, 50)).slice(0, 20)
    return {tabFound: !!t, itemCount: items.length, sampleNames: names}
  `)
  console.log('  →', installedTab)
  await screenshot(client, '03-installed-tab')
  results.push({case:'03-installed-tab', pass: installedTab.itemCount > 0 || installedTab.tabFound, detail: installedTab})

  // === 4. add-url tab + 输入 URL ===
  console.log('[l3] CASE-04: switch to add-url tab + input GitHub URL')
  const addUrlState = await evalJS(client, `
    const els = Array.from(document.querySelectorAll('button, [role="tab"], .tab'))
    const t = els.find(e => /添加|add.?url|url/i.test(e.textContent || ''))
    if (t) t.click()
    await new Promise(r => setTimeout(r, 600))
    // 找 url input
    const input = document.querySelector('input[type="text"], input[type="url"], input[placeholder*="url"i], input[placeholder*="GitHub"i]')
    if (input) {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set
      setter.call(input, 'github:anthropics/skills/tree/main/skills/algorithmic-art')
      input.dispatchEvent(new Event('input', {bubbles: true}))
      input.dispatchEvent(new Event('change', {bubbles: true}))
    }
    return {tabFound: !!t, inputFound: !!input, inputValue: input ? input.value : null}
  `)
  console.log('  →', addUrlState)
  await screenshot(client, '04-add-url')
  results.push({case:'04-add-url', pass: addUrlState.tabFound && addUrlState.inputFound, detail: addUrlState})

  // === 5. 直接发 ws skill_list_installed 验证 backend 通畅 ===
  console.log('[l3] CASE-05: direct ws skill_list_installed via page WebSocket')
  const wsResult = await evalJS(client, `
    return await new Promise((resolve) => {
      try {
        const ws = new WebSocket('ws://127.0.0.1:8600/ws/control')
        const timeout = setTimeout(() => { ws.close(); resolve({ok: false, error: 'timeout 5s'}) }, 5000)
        ws.onopen = () => ws.send(JSON.stringify({type: 'skill_list_installed'}))
        ws.onmessage = (ev) => {
          try {
            const m = JSON.parse(ev.data)
            if (m.type === 'skill_list_installed_response') {
              clearTimeout(timeout)
              ws.close()
              const skills = (m.payload && m.payload.skills) || []
              resolve({ok: true, count: skills.length, names: skills.map(s => s.name || s).slice(0, 15)})
            }
          } catch(e) {}
        }
        ws.onerror = () => { clearTimeout(timeout); resolve({ok: false, error: 'ws onerror'}) }
      } catch(e) {
        resolve({ok: false, error: e.message})
      }
    })
  `)
  console.log('  →', wsResult)
  results.push({case:'05-ws-installed', pass: wsResult.ok, detail: wsResult})

  // === Final ===
  console.log('\n[l3] ════════ SUMMARY ════════')
  for (const r of results) {
    console.log(`  ${r.pass ? '✅' : '❌'} ${r.case}`)
  }
  const passed = results.filter(r => r.pass).length
  console.log(`\n[l3] ${passed}/${results.length} PASS`)
  await fs.writeFile(path.join(SHOT_DIR, 'results.json'), JSON.stringify(results, null, 2))

  client.close()
}

main().catch(e => {console.error('[l3] FAILED:', e); process.exit(1)})
