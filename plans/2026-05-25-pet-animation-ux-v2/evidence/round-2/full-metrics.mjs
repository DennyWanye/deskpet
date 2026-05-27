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

const evalJson = async (expr) => {
  const r = await send('Runtime.evaluate', { expression: `JSON.stringify((function(){ try { return (${expr}) } catch(e){ return { __err: String(e) } } })())`, returnByValue: true })
  try { return JSON.parse(r.result.value) } catch { return r.result.value }
}

// Sample FPS via overlay metrics over 4s.
console.log('--- FPS over 4s ---')
await send('Runtime.evaluate', { expression: `window.__fps_count = 0; window.__fps_handler = () => { window.__fps_count++; requestAnimationFrame(window.__fps_handler); }; requestAnimationFrame(window.__fps_handler);` })
await new Promise(r => setTimeout(r, 4000))
const count = await evalJson('window.__fps_count')
console.log(`rAF ticks in 4s: ${count} ≈ ${(count/4).toFixed(1)} FPS`)

// Long PERF sample (PRD §16 — 60s window with metrics ring)
console.log('\n--- Visual frame metrics ---')
const m = await evalJson('window.__deskpet_anim_metrics()')
console.log(JSON.stringify(m, null, 2))

// Live2D model loaded?
const live2d = await evalJson(`(function(){
  const cv = document.querySelector('canvas');
  if (!cv) return { canvas: false };
  const ctx = cv.getContext && cv.getContext('webgl2');
  return { canvas: true, width: cv.width, height: cv.height, hasWebGL2: !!ctx };
})()`)
console.log('\n--- Canvas/Live2D ---')
console.log(JSON.stringify(live2d, null, 2))

ws.close()
