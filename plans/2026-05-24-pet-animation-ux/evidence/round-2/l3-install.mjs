#!/usr/bin/env node
// L3 Step 2: 真点 "安装" 按钮 + 验证 pending bubble 出现
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
  return {
    send(method, params={}) {
      const myId = ++id
      return new Promise((res, rej)=>{
        pending.set(myId, (m)=>{
          if (m.error) rej(new Error(JSON.stringify(m.error)))
          else res(m.result)
        })
        ws.send(JSON.stringify({id:myId, method, params}))
      })
    },
    close: ()=>ws.close()
  }
}

async function evalJS(client, expr){
  const r = await client.send('Runtime.evaluate', {
    expression: '(async () => { '+expr+' })()',
    awaitPromise: true,
    returnByValue: true,
  })
  if (r.exceptionDetails) throw new Error('eval failed: '+JSON.stringify(r.exceptionDetails))
  return r.result.value
}

async function screenshot(client, name){
  await client.send('Page.enable')
  const r = await client.send('Page.captureScreenshot', {format:'png'})
  await fs.writeFile(path.join(SHOT_DIR, name+'.png'), Buffer.from(r.data, 'base64'))
}

async function main(){
  const targets = await listTargets()
  const mainTarget = targets.find(t => t.url==='http://localhost:5173/') || targets[0]
  console.log('[l3b] connecting:', mainTarget.url)
  const client = await connect(mainTarget.webSocketDebuggerUrl)

  // 重新打开 panel + 进 add-url tab + 输 URL
  console.log('[l3b] setup: open panel + add-url tab + input URL')
  await evalJS(client, `
    // 关 panel 如果开着 (idempotent)
    const closeBtn = document.querySelector('[aria-label*="关闭"], button[title*="关闭"]')
    // 直接重 open
    const toggle = document.querySelector('[data-testid="skill-store-toggle"]')
    if (toggle) toggle.click()
    await new Promise(r => setTimeout(r, 600))
    // tab
    const tabs = Array.from(document.querySelectorAll('button'))
    const urlTab = tabs.find(b => /通过.*URL|add.?url/i.test(b.textContent || ''))
    if (urlTab) urlTab.click()
    await new Promise(r => setTimeout(r, 400))
    // input URL
    const input = document.querySelector('input[type="text"]')
    if (input) {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set
      setter.call(input, 'github:anthropics/skills/tree/main/skills/algorithmic-art')
      input.dispatchEvent(new Event('input', {bubbles: true}))
    }
    return {ok: !!input}
  `)

  // === CASE-06: 点 "安装" 按钮 ===
  console.log('[l3b] CASE-06: click 安装 button')
  const installClick = await evalJS(client, `
    const btns = Array.from(document.querySelectorAll('button'))
    const installBtn = btns.find(b => /^安装$|^install$/i.test((b.textContent || '').trim()))
    if (!installBtn) return {ok: false, error: 'install button not found'}
    if (installBtn.disabled) return {ok: false, error: 'install button disabled'}
    installBtn.click()
    return {ok: true, text: installBtn.textContent.trim()}
  `)
  console.log('  →', installClick)

  // === CASE-07: 等 10s 看 pending 提示出现 ===
  console.log('[l3b] CASE-07: wait for pending UI (10s)')
  let pendingState = null
  for (let i = 0; i < 10; i++) {
    await new Promise(r => setTimeout(r, 1000))
    pendingState = await evalJS(client, `
      const el = document.body
      const text = el.innerText || el.textContent || ''
      return {
        hasPending: /staging|pending|安装中|准备|确认/i.test(text),
        hasConfirm: /确认安装|approve|confirm/i.test(text),
        hasError: /失败|失败|error|拒绝/i.test(text),
        textSnippet: text.match(/.{0,50}(staging|pending|安装中|准备|确认|失败).{0,50}/i)?.[0] || null
      }
    `)
    console.log('  poll', i+1, '→', pendingState)
    if (pendingState.hasPending || pendingState.hasConfirm) break
  }

  await screenshot(client, '05-after-install-click')

  // === CASE-08: 如果有 confirm 按钮，点它 ===
  console.log('[l3b] CASE-08: click confirm if present')
  const confirmResult = await evalJS(client, `
    const btns = Array.from(document.querySelectorAll('button'))
    const confirm = btns.find(b => /确认安装|确认|approve|confirm/i.test((b.textContent || '').trim()))
    if (!confirm) return {ok: false, error: 'no confirm button visible'}
    confirm.click()
    await new Promise(r => setTimeout(r, 2000))
    return {ok: true}
  `)
  console.log('  →', confirmResult)

  // === CASE-09: 再回 installed tab 看是否有 algorithmic-art ===
  console.log('[l3b] CASE-09: switch back to installed tab')
  await evalJS(client, `
    const tabs = Array.from(document.querySelectorAll('button'))
    const t = tabs.find(b => /已安装/i.test(b.textContent || ''))
    if (t) t.click()
    await new Promise(r => setTimeout(r, 800))
    return {ok: true}
  `)
  await screenshot(client, '06-installed-after-install')

  const finalState = await evalJS(client, `
    const text = document.body.innerText || ''
    return {
      hasAlgorithmicArt: /algorithmic[ -]?art/i.test(text),
      hasEmptyMsg: /暂无已安装|no skills installed/i.test(text),
      textSnippet: text.slice(0, 500)
    }
  `)
  console.log('  final →', finalState)

  client.close()

  // === SUMMARY ===
  const results = [
    {case: '06-click-install', pass: installClick.ok, detail: installClick},
    {case: '07-pending-shown', pass: pendingState?.hasPending || pendingState?.hasConfirm, detail: pendingState},
    {case: '08-click-confirm', pass: confirmResult.ok, detail: confirmResult},
    {case: '09-installed-visible', pass: finalState.hasAlgorithmicArt, detail: finalState},
  ]
  console.log('\n[l3b] ════ SUMMARY ════')
  for (const r of results) console.log(`  ${r.pass ? '✅' : '❌'} ${r.case}`)
  const passed = results.filter(r => r.pass).length
  console.log(`[l3b] ${passed}/${results.length} PASS`)
  await fs.writeFile(path.join(SHOT_DIR, 'results-install.json'), JSON.stringify(results, null, 2))
}
main().catch(e => {console.error('[l3b] FAILED:', e); process.exit(1)})
