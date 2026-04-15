# V6 — Phase 2 实施路线图

**Date**: 2026-04-14
**Scope**: V5 §13 升级路径 + Phase 1 发布尾活，对应 codingsys 规范下的下一个完整 roadmap。
**Precedes**: `v0.1.0-phase1` 之后的所有版本，直到 `v1.0.0-phase2-ga`。
**Mode**: codingsys autonomous slice execution（Lead-Expert + 两层审查 + auto-verify）。

**Status**: DRAFT → **PARTIALLY SIGNED-OFF**（2026-04-14）
- ✅ 整体节奏接受
- ✅ D0-1 图标：当前 `icon.png` 为红色占位符，Live2D Hiyori 素材受版权限制不能商用，无其它自有素材可用 → 回退方案：AI 生成一张临时桌宠头像走 `npx tauri icon` 全套生成，留占位待设计师替换。
- ✅ D1-1 云厂选型：**仅接阿里百炼（DashScope）**，其它厂商待 P2-1 结束后再评估。

---

## 0. 文档定位

这不是一份单 slice 的实现 plan，而是 **Phase 2 全周期的 sprint-级 roadmap**。每个 slice 正式开工前，仍然要按 `sp-writing-plans` skill 产出独立的 step-by-step plan 文档（路径：`docs/superpowers/plans/YYYY-MM-DD-p2s<N>-<slice>.md`）。本文档的职责是：

1. 锁定 Phase 2 的验收门（对标 V5 §1.1 的升级版）
2. 把 V5 §13 六条主线拆成有依赖顺序的 sprint / slice
3. 给出发布节奏（版本号 + tag 策略）
4. 标出每个 sprint 开工前必须让用户拍板的决策点
5. 声明 Phase 2 里**刻意不做**的事情

**不包含：** 具体代码片段、逐步命令、测试用例 —— 那些在 per-slice plan 里写。

---

## 1. Phase 2 验收门（V5 §1.1 升级版）

Phase 1 的八条门（TTFT / VRAM / 8h 稳定 / 崩溃自愈 / 可分发 / 跨会话记忆 / 工具确认 / 记忆 UI）**全部继承，全部必须守住**。在此基础上，Phase 2 加 6 条：

| # | 新增门 | 测量机制 | 目标 |
|---|---|---|---|
| P2-G1 | 云端混合 TTFT p95 | `scripts/perf/ttft_cloud.py`（新） | < 1500 ms（比本地 2500 ms 严苛，因为云端单次 RTT 更短） |
| P2-G2 | 云端 fallback 成功率 | `providers/hybrid_router` 埋点 | 网络异常 30s 内切回本地 Ollama，用户侧丢失 ≤ 1 轮 |
| P2-G3 | 双工打断延迟 | `scripts/perf/barge_in.py`（新） | 用户开口到 TTS 静音 p95 < 200 ms |
| ~~P2-G4~~ | ~~多角色切换时长~~ | — | **已砍**（2026-04-15）：多角色 (PersonaRegistry) 砍出 Phase 2，门一并撤 |
| P2-G5 | 多模态截屏上行 | `pipeline/stages/screen_capture` 埋点 | 单帧 1080p → JPEG 压缩 → VLM 可消费 < 300 ms |
| P2-G6 | 桌面工具执行审计覆盖 | 所有 `tool_call` 必经 `requires_confirmation` + 审计日志 | 100% 覆盖 —— 无白名单漏网 |

**验收原则不变：** 任一门未达标 = Phase 2 不能打 GA tag。

---

## 2. 架构快照 & 扩展点复用

Phase 1 handoff 已经声明这 6 个扩展点 survive Phase 2。V6 的实施路径就是**逐个把它们从 "接口留空" 变成 "真实现"**：

| V5 §13 主线 | 扩展点（已存在） | V6 归属 sprint |
|---|---|---|
| PersonaPlex 双工 | `ServiceContext.asr_engine / tts_engine` | P2-2 |
| 自研 Live2D | `Live2DCanvas.tsx` forwardRef + prop | P2-∞（延后） |
| 桌面自动化 | `tools/` 白名单 + `requires_confirmation` | P2-3 |
| 多模态 | `pipeline/` Stage 链 | P2-3 |
| 多角色协作 | `ServiceContext` N 实例化 | **延后到 Phase 3**（2026-04-15 砍）—— 单角色定位足够覆盖 v1.0 GA |
| 云端混合 | `providers/base.py::LLMProvider` | P2-1 |

新增抽象（Phase 1 没有预埋的）：

- `HybridRouter` —— LLMProvider 之上的 "本地 ↔ 云端" 分流策略
- `DuplexAudioPipeline` —— 取代当前单向 `/ws/audio`，支持同时收发 PCM
- ~~`PersonaRegistry`~~ —— **延后到 Phase 3**（2026-04-15 砍）；P2-1 维持单 ServiceContext
- `PerceptionStage` —— pipeline 里加的新 Stage 基类（screen / camera / clipboard 共用）

---

## 3. Sprint 规划

按推荐顺序排列。时长估算是**单人 + subagent-driven-development** 模式下的 calendar time；如果后续并行多 Agent + worktree 隔离可缩。

### 3.1 Sprint P2-0 — Phase 1 收尾与首次公测（目标版本 `v0.2.0`）

**目标：** 补齐 Phase 1 handoff §"deliberately out of scope" 的阻塞项，拿到一个可以放到 GitHub Release 首次对外分发的包。

**时长估算：** 1 周

**Slice 列表：**

| Slice | 内容 | Deliverables | 验收 |
|---|---|---|---|
| P2-0-S1 图标品牌化 (B1 · 临时方案) | AI 生成 1024×1024 初版桌宠头像（简约可爱，紫色主色对齐 favicon 色调）→ `npx tauri icon` 铺全套 + tray icon + 覆盖 `favicon.svg`。**注**：当前 `icon.png` 是纯红色占位符，Hiyori 素材版权不可用，本 slice 交付的仍然是临时占位，正式品牌素材等设计师介入后通过一个 follow-up slice 替换。 | `tauri-app/src-tauri/icons/` 全套尺寸 + tray + `public/favicon.svg` 替换 + 生成脚本记录到 `docs/RELEASE.md` | 打包包体可见新图标；任务栏 / 托盘 / installer / WebView favicon 四处视觉一致 |
| P2-0-S2 Updater 密钥对 (B2) | 生成 `tauri signer generate` 密钥对 + 配置 `TAURI_SIGNING_PRIVATE_KEY` CI 环境 | `tauri.conf.json::plugins.updater.pubkey` 替换；`docs/RELEASE.md` 的演练段落改为"已完成首次发布" | 用 P2-0-S1 的包做一次 `v0.1.1-test` 自更新全链路验证 |
| P2-0-S3 MemoryPanel 多会话 UI (B5) | 前端扩展 `scope:"all"` 的 UI —— tab 切换 "本会话 / 全部会话"；保留删除 / 导出 | `MemoryPanel.tsx` 新增 tab + backend 无改动（API 已存在） | CDP E2E 新增 `step_memory_all_sessions` 3 项断言 |
| P2-0-S4 性能脚本化 (B3 + B4) | 把手动 Task Manager 目测改成 `scripts/perf/rss_sampler.py` + 把手动秒表改成 `scripts/perf/cold_boot.py` | 2 个新脚本 + `docs/PERFORMANCE.md` 更新 | 短时 smoke 得到基线数值；CI 可跑 |
| P2-0-S5 VN Dialog Bar NIT 清理 (C1–C3) | 删 `messagesEndRef` 死代码 / 加 `step_dialog_bar` 中间 `ensure_mic_idle` / `DialogBar` 空态占位文字 | 单 commit | `tsc --noEmit` 0 error + E2E 绿灯 |
| P2-0-S6 ChatHistoryPanel a11y follow-up | focus trap + auto-focus close button | `ChatHistoryPanel.tsx` 微调 | 键盘盲测通过 |
| P2-0-S7 首次公测 release | 用 `scripts/release.ps1` 跑 `v0.2.0`；签名；发 GitHub Release；写 release notes | 二进制包 + release notes + `v0.2.0` tag | 链接可分发、安装器跑通、自更新能拉到 `v0.2.0` |

**开工前决策点：**
- ~~**D0-1**：B1 图标素材~~ ✅ 已定：AI 生成临时占位（Hiyori 素材有版权不可用），等设计师后续替换
- **D0-2**：更新渠道 —— GitHub Releases 足够，还是要自建 `latest.json` 静态站？

**风险：**
- 图标素材拖延 → 整个 sprint 阻塞（P2-0-S7 依赖 S1）
- Updater 密钥对是 one-way —— 一旦发布，公钥不可换；必须在 S2 前彻底确认流程

---

### 3.2 Sprint P2-1 — 云端混合推理（目标版本 `v0.3.0`）

**目标：** 上线云端 LLM fallback（DashScope）+ 安全的 API key 管理 + 计费护栏。

**Scope 调整记录（2026-04-15）：** 砍掉 PersonaRegistry + Switcher UI（原 S4/S5），多角色推迟到 Phase 3。Phase 2 维持单 ServiceContext / 单角色定位 —— DeskPet 是一只桌宠，不是分身平台。本 sprint 收敛到纯 "云端混合 + 安全护栏" 这一条主线。

**时长估算：** 1.5–2 周（原 2-3 周；砍 S4/S5 省 1.5 周）

**Slice 列表：**

| Slice | 内容 | Deliverables |
|---|---|---|
| P2-1-S1 LLMProvider 云端实现 | **仅接阿里百炼 `providers/dashscope.py`**（D1-1 决策）。默认模型 `qwen-plus` / `qwen-turbo`（按 D1-1a 待定）；走官方 DashScope Python SDK 或直 HTTP（二选一按 spike 结果） | DashScopeProvider 实现 + 统一 `AsyncIterator[Token]` 接口 + SSE 流式解析；抽象保留"多厂商"余地但不实现 |
| P2-1-S2 HybridRouter | `providers/hybrid_router.py` —— 策略：`local_first` / `cloud_first` / `cost_aware` / `latency_aware` | 包含 circuit breaker + fallback 计数 + 埋点 |
| P2-1-S3 API key 管理 | Windows Credential Manager 集成；前端 SettingsPanel 新增 "云端账号" tab | `src-tauri/src/secrets.rs` + Tauri 命令 `get_api_key` / `set_api_key`；明文 **绝不** 入 SQLite |
| ~~P2-1-S4 PersonaRegistry~~ | **已砍（2026-04-15）** | 多角色延后到 Phase 3；Phase 2 维持单 ServiceContext |
| ~~P2-1-S5 前端 Persona 切换~~ | **已砍（2026-04-15）** | 同上 |
| P2-1-S6 云端 TTFT 埋点 (P2-G1) | `scripts/perf/ttft_cloud.py` + `/metrics` 端点新增 `llm_ttft_seconds{provider}` | 短 smoke 出基线；纳入 `docs/PERFORMANCE.md` |
| P2-1-S7 Fallback E2E (P2-G2) | CDP E2E 新增 `step_hybrid_fallback` —— 模拟云端 503，断言 30s 内切本地并继续回复 | 测试含网络 toxiproxy 或用 monkeypatch |
| P2-1-S8 计费 & 预算护栏 | token 计数 + 每日 USD 预算 + 超限自动降级 | SQLite 新表 `billing_ledger`；超预算弹 toast 而非静默切 |

**剩余 slice 顺序：** S2 → S3 → S6 → S7 → S8（5 个，原编号保留以兼容已有 handoff 引用）。

**开工前决策点：**
- ~~**D1-1**：接哪几家云？~~ ✅ 已定：**阿里百炼 DashScope**（其它厂 Phase 2 不做）
- **D1-1a**（新增）：DashScope 默认模型选 `qwen-plus`（质量优先）还是 `qwen-turbo`（成本优先）？是否暴露用户手动切换？
- **D1-2**：默认策略选哪个？我推 `local_first`（隐私优先，云端只在用户显式触发或本地拒绝时使用）
- **D1-3**：单次预算上限 & 月度预算 —— 需要用户给数字（人民币，按 DashScope 计费单位）
- ~~**D1-4**：persona 配置文件放哪？~~ ✅ **已撤**（2026-04-15 砍 S4/S5 时一并撤）
- **D1-5**（新增）：DashScope API key 注册流程是否写入用户引导？首次启动是否强制弹窗引导？

**风险：**
- API key 泄露 —— 必须走 Credential Manager，**禁止** .env / config.toml
- ~~多 provider 的 tokenizer 差异~~ → 只接 DashScope，此项暂不成立；但 qwen 系列不同模型 tokenizer 有细微差别，计费用官方返回的 `usage.total_tokens` 为准而非本地估算
- DashScope 海外可用性 / IP 屏蔽 —— 如果目标用户群有海外场景，P2-3 前需加 proxy 或地域选择逻辑

---

### 3.3 Sprint P2-2 — PersonaPlex 实时双工（目标版本 `v0.4.0`）

**目标：** 取代当前"按麦克风 → 录 → 停 → 发 → 等回复"的半双工，实现真双工 —— 用户说话中途就能打断 TTS、TTS 输出中途用户开口立即暂停。

**时长估算：** 3–4 周（最高风险 sprint）

**分两步：**

**阶段 1：server-side VAD 打断（P2-2-A）**
- 这一步 **不改前端录音模型**，只是让后端在 TTS 播放时同步监听 `/ws/audio` 的客户端上行帧；检测到 VAD=speech 立即向前端发 `tts_barge_in` 事件 → 前端停 AudioContext。
- 验收门：P2-G3 打断延迟 p95 < 500 ms（过渡目标，严格目标放阶段 2）

**阶段 2：真双工 DuplexAudioPipeline（P2-2-B）**
- WebRTC 替换现有 `/ws/audio`；单一 PeerConnection 同时承载用户上行 PCM 与 TTS 下行 Opus。
- ASR 引擎切流式（faster-whisper streaming / sherpa-onnx 按 D2-1 决策）。
- TTS 引擎切流式（edge-tts 已经有 `stream()`，Coqui XTTS v2 要验证）。
- 口型 pipeline 改用 phoneme-level 对齐，替换当前 energy-based 近似。
- 验收门：P2-G3 严格目标 p95 < 200 ms。

**Slice 列表：**

| Slice | 阶段 | 内容 |
|---|---|---|
| P2-2-S1 | A | server-side VAD + `tts_barge_in` 事件 |
| P2-2-S2 | A | 前端 `useAudioPlayback.ts` 接入 barge-in 事件 + fade-out |
| P2-2-S3 | A | CDP E2E `step_barge_in` 断言 |
| P2-2-S4 | B | WebRTC 信令通道（Tauri IPC + backend peer） |
| P2-2-S5 | B | ASR 流式引擎切换 + 抽象 `StreamingASR` |
| P2-2-S6 | B | TTS 流式引擎切换 + 抽象 `StreamingTTS` |
| P2-2-S7 | B | Phoneme-level 口型驱动（`LipSyncPipeline` 重做） |
| P2-2-S8 | B | 200ms 严格门 perf smoke + 真机录音验证 |

**开工前决策点：**
- **D2-1**：流式 ASR 选 faster-whisper streaming vs sherpa-onnx vs 云端（Azure / Deepgram）？
- **D2-2**：WebRTC 信令走 Tauri IPC 还是独立 `/ws/signal`？
- **D2-3**：阶段 1 出来后，是否先发 `v0.3.1` 试水？推迟阶段 2 到下个月？

**风险：**
- Windows WebRTC 栈在 Tauri 里的兼容性 —— 需要 spike 1–2 天验证
- 口型同步 phoneme 粒度下仍可能出现音视频 drift；如果严重要回退到 energy-based

---

### 3.4 Sprint P2-3 — 多模态感知 & 桌面自动化（目标版本 `v0.5.0` → `v1.0.0-rc`）

**目标：** 桌宠"能看屏幕"+"能动手操作"。这是 Phase 2 商业价值最高但安全红线最紧的 sprint。

**为什么合并 A3 + A4？** 多模态感知（A4）本身价值有限 —— 必须接上 "看到屏幕 → 决定动作" 才闭环。但桌面自动化（A3）在没有视觉输入时也只是盲手放大镜。两者互为前置条件。

**时长估算：** 4–6 周

**Slice 列表：**

| Slice | 内容 | 安全红线 |
|---|---|---|
| P2-3-S1 PerceptionStage 基类 | `pipeline/stages/perception.py` —— 抽象类，定义 `capture() -> Frame` / `encode() -> bytes` | — |
| P2-3-S2 屏幕截图 Stage | Windows `BitBlt` / `PrintWindow`；多显示器；区域截屏 | 截屏**仅在**用户显式触发（快捷键或指令中含 "看一下屏幕"）；**永不**后台轮询 |
| P2-3-S3 VLM 集成 | 对接 Gemma-3-Vision / Qwen-VL / GPT-4o-mini-vision（走 HybridRouter） | VRAM 预算新增 tier `vlm_lite` / `vlm_full` |
| P2-3-S4 OCR Stage | tesseract / PaddleOCR（本地）作为 VLM 之外的便宜路径 | — |
| P2-3-S5 剪贴板 Stage | 读剪贴板内容；**写剪贴板必须 `requires_confirmation`** | 写剪贴板算 side effect，走工具确认流 |
| P2-3-S6 相机 Stage（可选） | `mediaDevices.getUserMedia` → 后端 VLM | 默认 disabled；SettingsPanel 里用户主动开；开启时 tray 图标显示红点 |
| P2-3-S7 工具白名单扩展 | 键鼠 `pyautogui` 封装：move / click / type / hotkey；每种独立确认 | **全部** 走 `requires_confirmation`；批量操作需逐步 confirm，不支持 "yes to all" |
| P2-3-S8 工具审计日志 | `tools/audit.py` —— 所有 `tool_call` 落 SQLite + 用户可在 UI 回看 | 日志含时间戳 / persona / 入参 / 结果 / 用户确认记录 |
| P2-3-S9 审计覆盖验证 (P2-G6) | CI 脚本扫 `tools/*.py` —— 任何没挂 `@audited` 装饰器的 fn 让 CI 红 | 100% 覆盖 |
| P2-3-S10 桌面自动化 E2E | CDP 录制 "让桌宠帮我关闭记事本" 端到端 | 必须手动确认；拒绝路径也要测 |

**开工前决策点：**
- **D3-1**：VLM 选型 —— 本地跑得动的 Gemma-3-Vision 7B 够用，还是必须云端 GPT-4o-mini？
- **D3-2**：键鼠操作是否默认禁用？我推默认禁用 + SettingsPanel 显式 opt-in
- **D3-3**：审计日志保留期？永久 vs 30 天 vs 用户手动清空
- **D3-4**：相机 Stage 是否进 Phase 2？我推延到 Phase 3 —— 隐私争议大且用户侧设备不保证

**风险：**
- **安全红线最重** —— 任何一条审计漏网 = Phase 2 GA 卡死
- VLM 显存预算：Phase 1 的 `VRAMTier` 只考虑 LLM；这里要重做
- Windows API 权限模型 —— `SendInput` / `SetForegroundWindow` 在某些 AV 下会被拦

---

### 3.5 Sprint P2-∞ — 自研 Live2D 渲染器（延后）

**目标：** 用自研 WebGL 渲染器替换 pixi-live2d-display，以便控制 physics / deformer / 性能上限。

**为什么延后？**
- 当前 pixi-live2d-display 性能够用（60fps @ 1080p，GPU 占用 < 5%）
- 自研至少 6–8 周且收益不明确
- Phase 2 其他 5 条主线的商业价值都更高

**触发条件（满足任一则重新评估）：**
- pixi-live2d-display 出现严重 license / 维护中断问题
- 多角色需要同屏渲染，pixi 的 multi-instance 性能崩
- 用户明确要求自定义 deformer / physics 超出 pixi 能力

**暂不拆 slice。**

---

## 4. 发布路线图 & 版本号

SemVer，`v0.X.Y-phaseN-<tag>`：

| 版本 | 里程碑 | 阻塞 sprint |
|---|---|---|
| `v0.2.0-phase2-beta1` | 首次公测（图标 + updater + 多会话 UI） | P2-0 |
| `v0.3.0-phase2-beta2` | 云端混合（DashScope + HybridRouter + API key 安全 + 计费护栏） | P2-1 |
| `v0.3.1-phase2-beta2.1`（可选） | 阶段 1 打断（A 级双工） | P2-2-A |
| `v0.4.0-phase2-beta3` | 真双工 | P2-2-B |
| `v0.5.0-phase2-rc` | 多模态 + 桌面自动化 | P2-3-S1..S8 |
| `v1.0.0-phase2-ga` | 全部 Phase 2 验收门 PASS + 1 周公测无 BLOCKER bug | P2-3-S9..S10 |

每个版本走 `scripts/release.ps1` + GitHub Release + release notes；Phase 1 已验证过的发布流程不变。

---

## 5. 风险清单 & 决策点

### 跨 sprint 风险

| 风险 | 影响 | 缓解 |
|---|---|---|
| API key / 隐私泄露 | 项目生死 | P2-1 一开始就用 Credential Manager；绝不走 config 文件 |
| 云端依赖使本地体验退化 | 商业价值受损 | `local_first` 默认策略；P2-G2 fallback 门守住 |
| WebRTC 在 Windows Tauri 下兼容性未知 | 整个 P2-2-B 阻塞 | 进 P2-2 前做 1–2 天 spike |
| 审计装饰器有漏网 | Phase 2 GA 卡死 | P2-3-S9 做 CI-level 扫描 |
| LLM 成本失控 | 用户流失 | P2-1-S8 预算护栏必须在 S1 前就有合约；超限降级而非阻塞 |

### 累计决策点

按 sprint 分档，需要**用户拍板**才能开工：

**P2-0 前：** ~~D0-1（图标）~~ ✅ AI 占位 · D0-2（更新渠道）

**P2-1 前：** ~~D1-1（云厂选型）~~ ✅ DashScope 百炼 · D1-1a（qwen 模型档位）· D1-2（默认策略）· D1-3（预算）· ~~D1-4（persona 配置路径）~~ ✅ 撤（2026-04-15 砍 S4/S5）· D1-5（首启引导）

**P2-2 前：** D2-1（流式 ASR 选型）· D2-2（信令通道）· D2-3（A/B 阶段拆 release）

**P2-3 前：** D3-1（VLM 选型）· D3-2（键鼠默认态）· D3-3（审计日志保留期）· D3-4（相机纳入 Phase 2 与否）

**Phase 2 GA 前：** 是否需要独立 1 周公测窗口 + bug bash？

---

## 6. 流程规范（per-slice）

**每个 slice 的标准开工流程**（继承 Phase 1 的 codingsys 规范）：

1. **Brainstorm**（如果 slice 里包含 3+ 文件新增，或者 slice 跨了 backend + frontend 边界）—— 用 `sp-brainstorming` skill
2. **Spec-first** —— 跑 `sp-writing-plans` skill，产出 `docs/superpowers/plans/YYYY-MM-DD-p2s<N>-<slice>.md`
3. **Worktree 隔离** —— `EnterWorktree` 进独立分支，防止主线污染
4. **Subagent-driven 执行** —— 用 `sp-subagent-driven-development` skill，fresh implementer per task + spec compliance review + code quality review
5. **Auto-verify** —— 两层验证循环，测试 + 类型检查 + lint 必须全绿
6. **E2E regression** —— CDP 套件加新断言；不允许旧用例退化
7. **Final code review** —— 整 slice 跑一次 code-reviewer agent
8. **HANDOFF 文档** —— `docs/superpowers/handoffs/p2s<N>-<slice>.md` 按 Phase 1 风格
9. **Commit + tag（如果对应 release）**
10. **ExitWorktree + 主线 merge**

**禁止项：**
- 跳过 brainstorm 直接写 plan（除非单文件改动）
- 跳过审查（spec 审 + 代码审两道缺一不可）
- 把 BLOCKER 留给下一 slice（发现即修，不能堆积）
- 在 master / 发布 tag 上打补丁（worktree 修 + merge）

---

## 7. 文档地图（Phase 2 预期产物）

Phase 2 结束时应该有：

- `docs/superpowers/plans/2026-04-14-phase2-v6-roadmap.md` — 本文档
- `docs/superpowers/plans/YYYY-MM-DD-p2s<N>-<slice>.md` — 每个 slice 一份 plan
- `docs/superpowers/handoffs/p2s<N>-<slice>.md` — 每个 slice 一份 handoff
- `docs/superpowers/plans/2026-XX-XX-phase2-handoff.md` — Phase 2 GA 时的总 handoff（对标 Phase 1 handoff）
- `docs/PERFORMANCE.md` —— 补 P2-G1 / P2-G3 / P2-G5 章节
- `docs/RELEASE.md` —— 补 updater 密钥对落地流程
- `docs/SECURITY.md`（新）—— 桌面自动化审计模型 + API key 管理说明

---

## 8. 下一步

**已拍板（2026-04-14）：**
- ✅ 整体节奏接受
- ✅ D0-1：AI 生成临时占位图标（Hiyori 版权不可用，无自有素材）
- ✅ D1-1：仅接阿里百炼 DashScope

**仍待拍板（可滚动决策，不阻塞 P2-0 开工）：**
- D0-2（更新渠道）
- D1-1a / D1-2 / D1-3 / D1-4 / D1-5（P2-1 开工前必须定）
- D2-1 / D2-2 / D2-3（P2-2 开工前）
- D3-1 / D3-2 / D3-3 / D3-4（P2-3 开工前）

**下一步动作：** 立即进入 Sprint **P2-0** —— 第一个 slice 是 `P2-0-S1 图标品牌化（临时占位）`，按 §6 标准流程开工：
1. brainstorm（决定 AI 图像生成工具：fal.ai / Seedream / DALL-E）
2. `sp-writing-plans` 产出 `docs/superpowers/plans/YYYY-MM-DD-p2s1-icon-branding.md`
3. Worktree 隔离 + subagent-driven 执行

**V6 路线图状态：** SIGNED-OFF（2026-04-14，含 D0-1 + D1-1；其它决策点随 sprint 推进滚动确认）
