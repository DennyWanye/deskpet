import http from 'node:http'
import { WebSocket } from 'ws'

const targets = await new Promise((r,j)=>http.get({host:'127.0.0.1',port:9222,path:'/json'},(res)=>{let d='';res.on('data',c=>d+=c);res.on('end',()=>r(JSON.parse(d)))}).on('error',j))
const pet = targets.find(t => t.type==='page' && t.url.replace(/#.*$/,'').endsWith('/') && !t.url.includes('panel'))
const ws = new WebSocket(pet.webSocketDebuggerUrl)
await new Promise((r,j)=>{ws.on('open',r);ws.on('error',j)})
let id=0; const pending=new Map()
ws.on('message',(raw)=>{const m=JSON.parse(raw.toString()); if(m.id&&pending.has(m.id)){const {resolve,reject}=pending.get(m.id);pending.delete(m.id);if(m.error)reject(new Error(JSON.stringify(m.error)));else resolve(m.result)}})
const send=(method,params={})=>{const i=++id;return new Promise((r,j)=>{pending.set(i,{resolve:r,reject:j});ws.send(JSON.stringify({id:i,method,params}))})}
await send('Runtime.enable')

// Find 重试 button via DOM and click it
const r1 = await send('Runtime.evaluate', {
  expression: `(function(){
    const btns = Array.from(document.querySelectorAll('button'));
    const retry = btns.find(b => /重试|retry/i.test(b.textContent || ''));
    if (!retry) return { ok: false, found: btns.map(b => (b.textContent||'').trim()).slice(0,10) };
    const r = retry.getBoundingClientRect();
    retry.click();
    return { ok: true, clicked: retry.textContent.trim(), bbox: { left: r.left, top: r.top, w: r.width, h: r.height } };
  })()`,
  returnByValue: true,
})
console.log(JSON.stringify(r1.result?.value, null, 2))
ws.close()
