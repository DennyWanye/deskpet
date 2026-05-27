#!/usr/bin/env node
// L3 batch install via UI - 真鼠标点击 obra/superpowers
import http from 'node:http'
import { WebSocket } from 'ws'
import fs from 'node:fs/promises'
import path from 'node:path'

const SHOT_DIR = 'G:/projects/deskpet/plans/2026-05-25-pet-animation-ux-v2/evidence/round-l3-skill-ui'

async function listTargets(){
  return new Promise((resolve,reject)=>{
    http.get({host:'127.0.0.1',port:9222,path:'/json'},(res)=>{
      let d=''; res.on('data',c=>d+=c)
      res.on('end',()=>{try{resolve(JSON.parse(d))}catch(e){reject(e)}})
    }).on('error',reject)
  })
}
async function connect(url){
  const ws=new WebSocket(url); await new Promise(r=>ws.once('open',r))
  let id=0; const pending=new Map()
  ws.on('message',raw=>{const m=JSON.parse(raw); if(pending.has(m.id)){pending.get(m.id)(m); pending.delete(m.id)}})
  return {
    send(method,params={}){
      const myId=++id
      return new Promise((res,rej)=>{pending.set(myId,m=>m.error?rej(new Error(JSON.stringify(m.error))):res(m.result));ws.send(JSON.stringify({id:myId,method,params}))})
    },
    close:()=>ws.close()
  }
}
async function evalJS(c,expr){
  const r=await c.send('Runtime.evaluate',{expression:'(async()=>{'+expr+'})()',awaitPromise:true,returnByValue:true})
  if(r.exceptionDetails) throw new Error('eval: '+JSON.stringify(r.exceptionDetails))
  return r.result.value
}
async function shot(c,n){await c.send('Page.enable'); const r=await c.send('Page.captureScreenshot',{format:'png'}); await fs.writeFile(path.join(SHOT_DIR,n+'.png'),Buffer.from(r.data,'base64')); console.log('  shot:',n+'.png')}

async function main(){
  const ts=await listTargets()
  const m=ts.find(t=>t.url==='http://localhost:5173/')||ts[0]
  const c=await connect(m.webSocketDebuggerUrl)
  console.log('[batch] connected:',m.url)

  // 1. 关 panel if open, then open fresh
  console.log('[batch] 1: open SkillStorePanel')
  await evalJS(c,`
    // ensure closed first
    const closeBtn = document.querySelector('button[aria-label*="关闭"]')
    if (closeBtn) closeBtn.click()
    await new Promise(r=>setTimeout(r,300))
    // open
    const toggle = document.querySelector('[data-testid="skill-store-toggle"]')
    if (toggle) toggle.click()
    await new Promise(r=>setTimeout(r,600))
    return {ok:true}
  `)
  await shot(c,'batch-01-panel-open')

  // 2. 切「通过 URL 安装」tab
  console.log('[batch] 2: switch to add-url tab')
  await evalJS(c,`
    const tabs = Array.from(document.querySelectorAll('button'))
    const t = tabs.find(b => /通过.*URL/i.test(b.textContent||''))
    if (t) t.click()
    await new Promise(r=>setTimeout(r,400))
    return {ok:!!t}
  `)

  // 3. 输入 obra/superpowers (用户原始失败 URL)
  console.log('[batch] 3: input obra/superpowers URL')
  await evalJS(c,`
    const input = document.querySelector('input[type="text"]')
    if (input) {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set
      setter.call(input,'https://github.com/obra/superpowers')
      input.dispatchEvent(new Event('input',{bubbles:true}))
    }
    return {value: input?.value}
  `).then(r=>console.log('  url:',r))
  await shot(c,'batch-02-url-input')

  // 4. 点 安装
  console.log('[batch] 4: click 安装 button')
  await evalJS(c,`
    const btns = Array.from(document.querySelectorAll('button'))
    const b = btns.find(x => /^安装$/.test((x.textContent||'').trim()))
    if (b && !b.disabled) b.click()
    return {clicked: !!b, disabled: b?.disabled}
  `).then(r=>console.log('  ',r))

  // 5. 等 batch_completed (40s — 14 个 sub-skill 需要时间 git clone + validate)
  console.log('[batch] 5: wait for batch_completed toast (max 60s)')
  let final = null
  for (let i=0; i<60; i++) {
    await new Promise(r=>setTimeout(r,1000))
    const state = await evalJS(c, `
      const text = document.body.innerText || ''
      const installIdx = text.indexOf('已安装 ')
      const failIdx = text.indexOf('安装失败')
      let installedCount = null
      let installedSnippet = null
      if (installIdx !== -1) {
        const slice = text.slice(installIdx, installIdx + 300)
        installedSnippet = slice.split(String.fromCharCode(10))[0]
        let n = ''
        for (let k = 0; k < slice.length; k++) {
          const ch = slice[k]
          if (ch >= '0' && ch <= '9') n += ch
          else if (n.length > 0) break
        }
        if (n) installedCount = parseInt(n)
      }
      const errorSnippet = failIdx !== -1 ? text.slice(failIdx, failIdx + 200).split(String.fromCharCode(10))[0] : null
      return { installedCount, installedSnippet, errorSnippet, toast: installIdx !== -1 ? text.slice(installIdx, installIdx + 400) : null }
    `)
    if (state.installedCount !== null || state.errorSnippet) {
      final = state
      console.log('  ✓ toast appeared at',i+1,'s:',state.installedSnippet||state.errorSnippet)
      break
    }
    if ((i+1) % 5 === 0) console.log('  waiting...',i+1,'s')
  }

  await shot(c,'batch-03-toast-shown')

  // 6. 切已安装 tab 看新装的 14 个
  console.log('[batch] 6: switch to installed tab, count visible skills')
  const installedInfo = await evalJS(c,`
    const tabs = Array.from(document.querySelectorAll('button'))
    const t = tabs.find(b => /已安装/.test(b.textContent||''))
    if (t) t.click()
    await new Promise(r=>setTimeout(r,1200))
    // 找 skill 卡片（包含 卸载 按钮的）
    const cards = Array.from(document.querySelectorAll('button'))
      .filter(b => /^卸载$/.test((b.textContent||'').trim()))
      .map(b => b.closest('[class],[data-testid]')?.previousElementSibling?.textContent || b.parentElement?.textContent?.slice(0,80) || '')
    // 另一种：找所有 skill 名（通过含 卸载 button 的父级）
    const items = Array.from(document.querySelectorAll('button'))
      .filter(b => /^卸载$/.test((b.textContent||'').trim()))
    return {
      uninstallButtons: items.length,
      sampleCard: cards.slice(0,5)
    }
  `)
  console.log('  installed count:', installedInfo.uninstallButtons)
  await shot(c,'batch-04-installed-list')

  c.close()

  // 总结
  console.log('\n[batch] ════ SUMMARY ════')
  console.log('  toast installed:', final?.installedCount)
  console.log('  toast names:', final?.installedNames?.slice(0,150))
  console.log('  installed-tab uninstall buttons:', installedInfo.uninstallButtons)
  await fs.writeFile(path.join(SHOT_DIR,'batch-results.json'), JSON.stringify({toast:final, installed:installedInfo},null,2))
}
main().catch(e=>{console.error('FAIL:',e); process.exit(1)})
