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

// 60s rAF tick count + visual metrics + memory
console.log('--- PERF-01 (60s) start ---', new Date().toISOString())
await send('Runtime.evaluate', { expression: `
  window.__perf_ticks = 0;
  window.__perf_started = performance.now();
  const tick = () => { window.__perf_ticks++; window.__perf_h = requestAnimationFrame(tick); };
  if (window.__perf_h) cancelAnimationFrame(window.__perf_h);
  tick();
` })

const start = Date.now()
await new Promise(r => setTimeout(r, 60000))
const end = Date.now()

const ticks = await evalJson('window.__perf_ticks')
const elapsed = await evalJson('performance.now() - window.__perf_started')
const metrics = await evalJson('window.__deskpet_anim_metrics()')
const mem = await evalJson(`(function(){
  if (performance.memory) {
    return {
      usedJSHeapSize: performance.memory.usedJSHeapSize,
      totalJSHeapSize: performance.memory.totalJSHeapSize,
      jsHeapSizeLimit: performance.memory.jsHeapSizeLimit,
    }
  }
  return null;
})()`)

await send('Runtime.evaluate', { expression: 'if (window.__perf_h) cancelAnimationFrame(window.__perf_h);' })

console.log(JSON.stringify({
  wallclock_ms: end - start,
  rAF_ticks: ticks,
  elapsed_ms_in_page: elapsed,
  effective_fps: (ticks / (elapsed / 1000)).toFixed(2),
  visual_p50_ms: metrics?.visual?.p50,
  visual_p95_ms: metrics?.visual?.p95,
  visual_max_ms: metrics?.visual?.max,
  visual_samples_n: metrics?.visual?.samples?.length,
  memory: mem,
}, null, 2))

ws.close()
