# ManualTest — Pet Animation UX v2

| 项 | 值 |
|---|---|
| 关联 PRD | `PRD.md` v2 |
| 关联 TDD | `TDD.md` v2 |
| 版本 | **v2**（应用 round-1 评审）|
| 环境 | Windows 11 + Tauri dev + WebView2 CDP 9222 |
| 全套耗时 | ~120 min |
| Evidence | `evidence/round-N/` |
| 复用 v1 | cdp-runner.mjs + Win32 mouse_event wrapper |

---

## 0. 前置准备

### 0.1 清残留 + 启动（同 v1 round-3）

```powershell
Get-Process -Name deskpet, node -ErrorAction SilentlyContinue | Stop-Process -Force
cd G:\projects\deskpet\backend; .\.venv\Scripts\Activate.ps1; python -m deskpet_backend.server
cd G:\projects\deskpet\tauri-app
$env:WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS = '--remote-debugging-port=9222'
npm run tauri:dev
```

### 0.2 DevTools helper

同 v1 round-3 + 加 v2 FLAG_KEYS（v2_all, held, userInput, thinking, viseme, mouthFade, lowEnergy, welcome, timeCele, emotion, milestone, edge, occlusion, dnd, + 子 dnd_fullscreen/typing/call）+ helpers:

```js
window.testV2Smoke = async () => {
  // 13 FR mini smoke 各跑 1 个最小 case
  console.log('held: drag pet...');
  // ...
};
window.fastForwardIdle = (minutes = 5) => { window.__deskpet_anim_fakeIdle?.(minutes * 60 * 1000); };
window.fakeEmotion = (em) => { window.__deskpet_anim_overlay?.setEmotion(em, performance.now()); };
window.fakeMilestone = (kind, msg) => {
  // 模拟后端推送 pet_milestone
  const ev = { type: 'pet_milestone', payload: { kind, message: msg, achieved_at: Date.now() } };
  // 喂给 control channel mock
};
```

### 0.3 Evidence

```
mkdir -Force plans\2026-05-25-pet-animation-ux-v2\evidence\round-0
mkdir -Force plans\2026-05-25-pet-animation-ux-v2\evidence\round-1
mkdir -Force plans\2026-05-25-pet-animation-ux-v2\evidence\blind-test
```

---

## 1. 矩阵

| Case ID | FR | 类型 | 耗时 |
|---|---|---|---|
| **CASE-D0-01..06** | Day-0 6 探针（v2 扩） | Console + 后端 dry-run | 20min |
| CASE-A1-01..04 | A1 拖 held + wobble + spring | windows-mcp + 录屏 | 6min |
| CASE-B1-01..04 | B1 user input + IME 兼容 | 真键盘 | 5min |
| CASE-B2-01..04 | B2 first-chunk 退出 (M-1) | 真 chat 流 | 5min |
| CASE-B3-01..08 | B3 viseme 主 + fallback + 完整 phoneme + blend A/B | TTS 真句录屏 + 朋友盲听 | 18min |
| CASE-B4-01..03 | B4 fade + 800ms 兜底 (M-4) | TTS + mock 漏 tts_end | 4min |
| CASE-C1-01..04 | C1 low-energy + visibility event (M-5) | fastForwardIdle + 切窗 | 8min |
| CASE-C2-01..03 | C2 welcome + escalation (M-7) | 三档 idle 时长测 | 7min |
| CASE-C3-01..04 | C3 整点/纪念日 + DND 抑制 (M-9) | clock 注入 + DND state | 5min |
| CASE-D1-01..08 | D1 5 类 + 投票 + 锁释放 + 后端字段 | 真句子 + 后端 mock | 14min |
| CASE-D2-01..03 | D2 milestone 5 规则 | 后端 mock pet_milestone | 5min |
| CASE-E1-01..04 | E1 4 边 snap + 无 preview | 拖窗 | 6min |
| CASE-E2-01..04 | E2 默认 on + consent + grid sampling + graceful degrade | 浏览器覆盖 + 拒 consent | 8min |
| CASE-F1-01..06 | F1 fullscreen + 250 KPM + 通用 audio session + ZZZ badge | 全屏视频 + 真打字 + Teams/Discord | 12min |
| CASE-PERF-01..04 | NFR-1 + 1.1 (各项预算) | 任务管理器 + 单独 perf ring | 10min |
| **CASE-AC3-01..04** | **v1 零回归 snapshot 4 条 (§6.11)** | 自动化 + 27/27 v1 手测 | 30min |
| CASE-AC10-01..04 | 4 个一票否决 | 专项 | 6min |
| CASE-BLIND-v2-01 | v1 vs v2 1+1 盲选 | 录屏 | 15min |

---

## 2. CASE-D0：Day-0 6 探针（v2 扩）

### CASE-D0-01: Tauri startDragging（同 v1）

### CASE-D0-02: viseme 后端能力

```bash
cd backend && python -m deskpet_backend.dry_run_tts "妈妈骑马慢"
```

输出含 `viseme: {v, t_ms}` → PASS；否则 → 走 fallback 路径 + Probe-D0-03

### CASE-D0-03: phoneme estimator viable (B3 fallback **必做**)

- 跑 phonemeEstimator 估计 "妈妈骑马慢" → 输出 VisemeFrame[]
- 接入 visemeLipsync → 真渲染
- 朋友盲听判定准确度 ≥ 70%
- PASS → fallback 路径完备；FAIL → BLOCKER 报告

### CASE-D0-04: Hiyori 表情参数实测（10 参数）

跑 TDD §0 Probe-D1 代码；每参数确认视觉变化。

### CASE-D0-05: 后端 LLM emotion 字段

```bash
cd backend && python -c "from llm.chinzy import chat; r = chat('请简短回答：你今天开心吗？'); print(r.get('emotion'))"
```

PASS → 主路径 ready；FAIL → S2 修后端 system prompt

### CASE-D0-06: F1 通用 audio session 枚举

开 Teams/Zoom/Discord/Slack/Wechat/Lark 任一 + 进 audio capture；invoke `is_any_audio_capture_active`；6 个 app 各测一次 PASS。

---

## 3. CASE-A1：拖 held + wobble + spring

### CASE-A1-01: 拖动 body wobble 可见

- windows-mcp 真鼠标在 hit-zone mousedown + 移动 200px + mouseup
- 预期：drag 200ms 内 body wobble；mouseup → spring back 250ms
- PASS：录屏可见

### CASE-A1-02: 表情 surprise

- A1-01 期间放大脸看 ParamMouthForm/EyeOpen/Brow
- PASS：肉眼惊讶感

### CASE-A1-03: spring back ease

- 240fps 录 mouseup 后 250ms
- PASS：ease-out 立方过渡

### CASE-A1-04: **drag 不破 v1 click**（AC-10-04 一票否决）

- mousedown + 立即 mouseup（无移动）→ 触发 click pulse
- PASS：interaction.samples + 1，无 wobble

---

## 4. CASE-B1：用户输入歪头

CASE-B1-01..03 同 v1 草稿（focus + 敲 → 歪头；停手 1500ms 复位；blur 立即复位）

### CASE-B1-04: **IME 中文输入兼容**

- 切到中文输入法；输入"你好"过程中 keydown 触发但 composition 期不算 active
- PASS：debug.user_input_active 仅在 composition 完成后 active

---

## 5. CASE-B2：first-chunk 退出（M-1）

### CASE-B2-01: sendChatV2 → thinking

CASE-B2-02: **first chunk 到达即退出**（不是 chat_v2_final）

- chat 发送；首个 token 到 → query debug
- 预期：thinking_active=false（v2 改）

### CASE-B2-03: 90s 超时（v2 调强）

mock 不回 90s+ → thinking 强制 false

### CASE-B2-04: thinking 期 saccade 仍跑

- B2-01 期间 query debug.saccade 仍有 micro-movement
- PASS：eye 不僵

---

## 6. CASE-B3：viseme 双路径

### CASE-B3-01..06: 主路径（同 v1 草稿）

CASE-B3-01 "妈妈" 持续 A；CASE-B3-02 多元音过渡；CASE-B3-03 silent；CASE-B3-04 flagOff('viseme') 退 amplitude；CASE-B3-05 朋友盲听准确度；CASE-B3-06 emotion 冲突 viseme 优先

### CASE-B3-07: **fallback 路径准确度 (B-2)**

- mock 后端不发 viseme；让前端 phonemeEstimator 自动接管
- 念 5 句不同复杂度："你好" / "妈妈骑马" / "他骑马太慢妈妈骂他" / 全 silent "..." / 含拼音字母 "K 歌"
- 朋友盲听判定每句准确度
- PASS：5/5 准确度 ≥ 70%（朋友说嘴形对得上字）

### CASE-B3-08: **blend_ms A/B 调参 (OQ-B3-2)**

- 60ms vs 100ms 各录一段 30s 句子
- 1 周后自盲选 + 1 朋友盲选选哪个自然
- 数据写入 evidence；2/2 选某档 → 默认改

---

## 7. CASE-B4：fade + 800ms 兜底

### CASE-B4-01..02: 同 v1 草稿（fade ease；新 viseme 取消 fade）

### CASE-B4-03: **800ms timeout 兜底 (M-4)**

- mock 后端漏发 tts_end（chunk 流停止但不发 end）
- 等 800ms
- 预期：mouthFader 自动触发 fade
- PASS：mouthOpenY 800ms 后开始 fade

---

## 8. CASE-C1：low-energy + visibility

CASE-C1-01..03 同 v1 草稿（fastForwardIdle 5.5 → low_energy；yawn 30-60s 触发；input 退出）

### CASE-C1-04: **visibility/blur 也算 reset (M-5)**

- low_energy 后切到其他应用（window blur）
- 等 5min
- 切回 → 立即 wakeup（不需要鼠标动）
- PASS：onWakeup 触发

---

## 9. CASE-C2：welcome escalation (M-7)

### CASE-C2-01: 5-15min 普通 welcome

- fastForwardIdle 10 min → input
- 预期：TapBody + happy 1.5s
- PASS：录屏

### CASE-C2-02: 15min-1h bubble welcome

- fastForwardIdle 30min → input
- 预期：上 + bubble "好久不见~"
- PASS：bubble DOM

### CASE-C2-03: >1h intense welcome

- fastForwardIdle 65min → input
- 预期：双 TapBody + bubble "想你了，欢迎回来~" + happy 3s
- PASS：录屏

---

## 10. CASE-C3：整点 + DND 抑制

CASE-C3-01..03 同 v1 草稿（整点；纪念日；非整点不触发）

### CASE-C3-04: **DND active 时 hourly 抑制 (M-9)**

- mock dnd_active=true（如全屏视频）
- mock clock 12:00
- 预期：整点 silent skip（debug.last_celebration_skip=true，无 bubble）
- 纪念日仍触发（重要日子用户重视）
- PASS：debug 字段 + 无 hourly bubble + anniversary 仍触

---

## 11. CASE-D1：5 类 + 投票 + 锁释放 + 后端字段

### CASE-D1-01..05: 5 类各一句（同 v1）

### CASE-D1-06: TTS 期间 emotion 锁定

### CASE-D1-07: **用户打断 TTS → 锁释放 (M-11)**

- 发"很抱歉" → TTS 开始念
- 念 1 秒后用户立即发新 chat
- 预期：emotion 立即释放为 neutral
- PASS：debug.current_emotion=neutral 

### CASE-D1-08: **投票分类器（M-12）**

- 测 "抱歉，没问题" → emotionClassifier 投票
- 预期：sad 1票 + happy 1票 → 平票 → 默认 sad（顺序优先）
- 测 "好的好的好" → happy 3票
- PASS：classifier 输出符合预期

---

## 12. CASE-D2：milestone 5 规则

### CASE-D2-01..02: 同 v1 草稿（后端推 + 排队）

### CASE-D2-03: **5 条规则各测 (M-14)**

- mock 后端 streak_7d / streak_30d / msgs_1000 / first_custom_prompt / first_pet_naming 各推一次
- 预期：5 次庆祝 + 各自 bubble 文案符合
- PASS：5/5

---

## 13. CASE-E1：4 边 snap

CASE-E1-01..03 同 v1 草稿（右/左/上/下）

### CASE-E1-04: **不做 snap preview (M-15)**

- 慢拖窗口逐渐接近右边
- 预期：未松手前不预览（无 ghost）；松手到边 100px 内才 snap
- PASS：无预览闪烁

---

## 14. CASE-E2：默认 on + consent

### CASE-E2-01: **首次启用弹 consent (B-6)**

- 删 localStorage `deskpet_consent_occlusion`
- 重启 dev
- 预期：SettingsPanel 或对话框弹 consent UI
- PASS：consent dialog DOM 出现

### CASE-E2-02: 拒 consent → flag auto off

- 点拒绝
- 预期：flag deskpet_anim_occlusion 自动 off + UI 显"已禁用：需用户允许"
- PASS：flag + UI

### CASE-E2-03: 允许 → 正常工作

CASE-E2-04: **grid sampling find safe spot (M-17)**

- 4 角全占 + 4 边中点全占（不可能但 mock）
- 预期：找到中区某空闲点（48 候选 sampling）
- PASS：safe spot ∈ grid

---

## 15. CASE-F1：DND 三 trigger + 通用 audio + ZZZ

### CASE-F1-01..02: fullscreen + 250 KPM (M-19)

- 真打 250+ KPM 持续 3min → DND
- 200 KPM 不触发

### CASE-F1-03: **通用 audio session (M-20)**

- 测 Teams / Zoom / Discord / Slack / Wechat / Lark 任一开 audio capture → DND
- 6 app 各测一次 PASS

### CASE-F1-04: red alert 仍弹 (AC-10-03)

CASE-F1-05: **ZZZ badge UI 显示 (m-5)**

- DND active → badge 在 pet 右上角 (+8, -4)，14×14, opacity 0.4
- PASS：DOM 检查

### CASE-F1-06: F1 graceful degrade（**不砍 FR**）

- mock audio session API 失败 → call detection 自动 off
- 但 fullscreen + typing 仍工作
- PASS：reasons 不含 call，dnd 仍能由其他 trigger 激活

---

## 16. CASE-PERF：性能（含 NFR-1.1 分配）

CASE-PERF-01..03 同 v1（FPS / CPU/RAM 增量 / applyTo bench）

### CASE-PERF-04: **NFR-1.1 各项预算 (M-24)**

- 单独 perf ring 测：Perlin / blink / saccade / gaze / emotion / viseme / held / occlusion 各自 ms/call
- 预期：每项 ≤ §4.1.1 分配预算
- PASS：每项独立达标

---

## 17. CASE-AC3：**v1 零回归 snapshot 4 条 (§6.11) — v2 ship 准入硬条件**

### CASE-AC3-01: v2_all=off → 386 单测

- `localStorage.setItem('deskpet_animation_v2', 'off')`
- 跑 `npm run test:anim`
- PASS：386/386 不增不减

### CASE-AC3-02: v2_all=off → 27/27 v1 OS 手测

- 同上 localStorage 设置
- 跑 v1 ManualTest §16 全 P0 case
- PASS：27/27

### CASE-AC3-03: **v2_all=on + 13 FR all off → snapshot diff = 0**

- 跑 `pnpm test:ac3-snapshot`
- 固定 input → applyTo 100 帧 → param write sequence dump
- 与 v1 baseline.json compare
- PASS：diff === 0

### CASE-AC3-04: **单 FR on → 仅该 FR 相关 param 变 (13 sub-tests)**

- 逐 FR 开 → diff 仅含该 FR 应写 param
- PASS：13/13 sub-test

---

## 18. CASE-AC10：4 个一票否决

### AC10-01: D1 sad 不误归 happy

- "很抱歉，没办法" → emotion 必须 sad（不能 happy）
- PASS：debug.current_emotion='sad'

### AC10-02: E2 pet 不超屏

- 极端 mock：所有屏区被覆盖
- 预期：pet 不被推到 x<0 或 y<0
- PASS：geom.screenX >= 0 && screenY >= 0

### AC10-03: F1 不抑 red alert (= CASE-F1-04)

### AC10-04: A1 drag 不破 v1 click (= CASE-A1-04)

---

## 19. CASE-BLIND-v2-01：v1 vs v2 盲选

- 录 60s A=v2_off (v1 行为) / B=v2_on
- 脚本：鼠标进 hit-zone 移 / 单击 / 双击 / 拖窗 / 发"好的" / TTS 念 / 等静默
- 1 周后自盲 + 1 朋友
- Q1 哪个更"有灵魂"？Q2 哪个想多看？
- 2/2 选 B → PASS；1/2 → WARN；0/2 → FAIL

---

## 20. 通过判定

1. Day-0 6 探针 PASS 或合法 graceful degrade（**不砍 FR**）
2. 所有 P0 case PASS（A1/B1/B2/B3/B4/C1/C2/C3/D1/D2/E1/E2/F1 + PERF + AC3 + AC10）
3. CASE-BLIND-v2-01 PASS / WARN
4. evidence 完整
5. CI 全绿：tsc + lint + test:anim:cov + test:e2e-wire-v2 + **test:ac3-snapshot**

---

## 21. FAIL 模板（同 v1）

---

## 22. 子代理执行约定

- 按 §1 矩阵顺序：D0 → A1..F1 → PERF → AC3 → AC10 → BLIND
- 严禁跳 P0
- 严禁用单测代替手测
- PERF 关 DevTools
- FAIL 按 §21 模板
- 不允许直接改 src
- 任何 D0 FAIL 必须按 PRD §8 graceful degrade 调整（**不砍 FR**）

---

## 23. 修订日志

### v2
- D0 探针 5→6（加 D1 后端 / phoneme estimator viable / F1 通用 audio session）
- B3 加 fallback case (B3-07) + blend A/B (B3-08)
- C1/C2/C3/D1/D2/E1/E2/F1 各加 round-1 评审解的 case
- 加 AC-3 v1 零回归 snapshot 4 条 (§6.11)
- IME 兼容 / first-chunk 退出 / 投票分类 / 通用 audio session / grid sampling / ZZZ badge UI / 800ms 兜底
- KPM 50→250

### v1
- 初稿
