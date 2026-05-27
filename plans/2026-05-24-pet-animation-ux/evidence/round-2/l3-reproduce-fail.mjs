#!/usr/bin/env node
// 复现 obra/superpowers 安装失败 — 看 UI 真实错误展示
import http from 'node:http'
import { WebSocket } from 'ws'
import fs from 'node:fs/promises'
import path from 'node:path'

const SHOT_DIR = 'G:/projects/deskpet/plans/2026-05-25-pet-animation-ux-v2/evidence/round-l3-skill-ui'

async function listTargets() {
  return new Promise((resolve, reject) => {
    http.get({host:'127.0.0.1', port:9222, path:'/json'}, (res)=>{
      let data=''; res.on('data',c=>data+=c)
      res.on('end',()=>{try{resolve(JSON.parse(data))}catch(e){reject(e)}})
    }).on('error', reject)
  })
}
async function connect(wsUrl){
  const ws = new WebSocket(wsUrl)
  await new Promise(r=>ws.once('open',r))
  let id = 0; const pending = new Map()
  ws.on('message', raw=>{
    const m = JSON.parse(raw)
    if (m.id !== undefined && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id) }
  })
  return {
    send(method, params={}){
      const myId=++id
      return new Promise((res,rej)=>{
        pending.set(myId,m=>m.error?rej(new Error(JSON.stringify(m.error))):res(m.result))
        ws.send(JSON.stringify({id:myId,method,params}))
      })
    },
    close:()=>ws.close()
  }
}
async function evalJS(c, expr){
  const r = await c.send('Runtime.evaluate',{expression:'(async ()=>{'+expr+'})()',awaitPromise:true,returnByValue:true})
  if (r.exceptionDetails) throw new Error('eval failed: '+JSON.stringify(r.exceptionDetails))
  return r.result.value
}
async function shot(c, name){
  await c.send('Page.enable')
  const r = await c.send('Page.captureScreenshot',{format:'png'})
  await fs.writeFile(path.join(SHOT_DIR, name+'.png'), Buffer.from(r.data,'base64'))
}

async function main(){
  const ts = await listTargets()
  const main = ts.find(t=>t.url==='http://localhost:5173/') || ts[0]
  const c = await connect(main.webSocketDebuggerUrl)

  // 重置：关掉旧 panel 重新打开
  console.log('[fail] reset + open panel + add-url tab + 输入失败 URL')
  await evalJS(c, `
    // 先关，再开（toggle 是切换状态的）
    const t = document.querySelector('[data-testid="skill-store-toggle"]')
    if (t) t.click()
    await new Promise(r=>setTimeout(r,300))
    if (t) t.click()
    await new Promise(r=>setTimeout(r,500))
    // 切 add-url tab
    const tabs = Array.from(document.querySelectorAll('button'))
    const urlTab = tabs.find(b=>/通过.*URL/i.test(b.textContent||''))
    if (urlTab) urlTab.click()
    await new Promise(r=>setTimeout(r,400))
    // 输入失败 URL (用户实际输入)
    const input = document.querySelector('input[type="text"]')
    if (input) {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set
      setter.call(input, 'https://github.com/obra/superpowers')
      input.dispatchEvent(new Event('input', {bubbles: true}))
    }
    return {ok: !!input, urlValue: input?.value}
  `).then(r => console.log('  setup:', r))

  await shot(c, 'fail-01-bad-url-input')

  // 点 安装
  console.log('[fail] 点 安装 按钮')
  await evalJS(c, `
    const btns = Array.from(document.querySelectorAll('button'))
    const b = btns.find(x => /^安装$/.test((x.textContent||'').trim()))
    if (b) b.click()
    return {ok:!!b}
  `)

  // 等错误展示 (10s)
  let lastErr = null
  for (let i=0;i<15;i++){
    await new Promise(r=>setTimeout(r,1000))
    const state = await evalJS(c, `
      const text = document.body.innerText || ''
      // 看是否有错误展示
      const errRegex = /失败|错误|error|拒绝|reject|safety|SKILL\.md|manifest|cannot/i
      const m = text.match(/.{0,80}(失败|错误|error|reject|safety|SKILL\.md|manifest|cannot|没有).{0,80}/i)
      return {hasError: errRegex.test(text), snippet: m?.[0]}
    `)
    lastErr = state
    console.log('  poll', i+1, '→', state.hasError ? 'ERROR detected' : 'still loading...')
    if (state.hasError) break
  }
  console.log('\n=== FINAL ERROR MESSAGE ===')
  console.log(lastErr?.snippet || '(no error captured)')
  await shot(c, 'fail-02-error-shown')

  c.close()
}
main().catch(e=>{console.error('FAIL:',e); process.exit(1)})
