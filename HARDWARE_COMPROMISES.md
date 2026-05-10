# Hardware Compromises — AMD/Intel GPU 兼容性待办

**Status:** 长期 backlog · 不阻塞当前 release
**Last updated:** 2026-05-09

DeskPet 当前为了赶发行节奏，在硬件支持上做了一系列**显式妥协**。
这份文档集中记录每条妥协的来由、目前的影响范围、以及"等有时间了应该怎么做"的
兼容化路径，避免几个月后忘记为什么这么写。

---

## 1. CUDA-only / NVIDIA-only 启动门禁 (P3-S2)

### 现状
- `tauri-app/src-tauri/src/gpu_check.rs` 启动时 NVML 探测 NVIDIA GPU，
  探不到直接弹窗 `DeskPet — 硬件不支持` + `app.exit(1)`，backend 根本不 spawn
- 后端 `WhisperModel(device="cuda")`、BGE-M3 向量化、未来的所有本地推理
  全部 hardcode `cuda`
- PyInstaller 打包的 `torch` 是 CUDA wheel（`+cu130`），AMD/Intel 用户
  下载下来即使绕过门禁也跑不起来

### 为什么这么做
- Phase 3 发行底线只验证过 NVIDIA 路径
- 早期没拦的话：lifespan 里 `WhisperModel` 抛异常 → 被 `logger.warning` 吞 →
  backend 自称启动完成 → 前端所有 ASR 请求 500 → 用户完全不知道"我缺驱动"
- 弹窗 + 早退是当时最便宜的"先告诉用户实情"方案

### 影响范围（用户层面）
- ❌ AMD GPU 用户（RX 系列 / Radeon Pro）— 全部被拒
- ❌ Intel Arc / 集显用户 — 全部被拒
- ❌ Apple Silicon — 不在 Phase 3 目标内（暂不讨论）
- ❌ 仅有 CPU 的轻办公本 — 全部被拒
- ✅ NVIDIA 桌面卡（GTX 10 系以上）+ 笔记本卡 — 唯一被支持的群体

粗估排除了 50%+ 的 Windows 用户基数。

### 兼容化待办（按 ROI 排序）

#### 🟢 Tier 1: CPU fallback 路径（最高优先级）
- `faster-whisper` 本来就支持 `device="cpu"`，性能会慢但能跑
- BGE-M3 也有 CPU 模式（onnxruntime）
- 工作量：
  1. `gpu_check.rs` 改为非阻塞警告（保留弹窗，但允许"以低性能模式继续"）
  2. backend `engine.load()` 在 CUDA init 失败时回退 `device="cpu"`
  3. PyInstaller 打两个 wheel 流派（`+cu130` 和 `+cpu`），或者打 universal CPU 版本作为 fallback installer
  4. UI 加 GPU/CPU 当前模式状态徽章
- 估计 1-2 个 sprint
- **解锁**：Intel/AMD 用户 + 老笔记本用户 全部可用（性能差但能用）

#### 🟡 Tier 2: AMD ROCm 路径
- `torch+rocm` 在 Linux 上成熟，**Windows ROCm 仍然不稳**（2026 年初情况）
- 等 PyTorch 官方 Windows ROCm wheel GA 之后再做
- 暂时不投入

#### 🟡 Tier 3: Intel XPU 路径
- `torch.xpu` 已在 mainline，但生态薄弱
- Arc A 系列显卡市场占比低，ROI 不足
- 跟随 Tier 2 一起评估

#### 🔴 Tier 4: 全本地大模型推理（含 PersonaPlex 类）
见下方 §2，单独说。

---

## 2. PersonaPlex 全双工语音模型评估（2026-05-09）

### 背景
用户提议接入 [NVIDIA/PersonaPlex](https://github.com/NVIDIA/personaplex) —
基于 Moshi/Helium 7B 的 full-duplex 语音对话模型，号称能让"桌宠语音体验高一个档次"。

### 评估结论：**短期不引入**，记入长期 backlog

#### 它真能带来的提升（限定场景下）
| 维度 | 现状 | PersonaPlex |
|---|---|---|
| 语音自然度 | edge-tts（合成感明显） | Moshi 真·人声，接近 ChatGPT Advanced Voice |
| 首包延迟 | ASR → LLM → TTS 串行 1-3s | 全双工 S2S，可即时 backchannel |
| 性格一致性 | system prompt + 固定 voice | voice embedding + role prompt 一体训练 |
| 闲聊感 | 一问一答 | 可被打断、可主动接话 |

#### 但对 DeskPet 是 hard blockers
1. **不支持中文** — 训练数据是 Fisher English Corpus + 全英文 customer service prompts，
   中国用户场景基本不可用。
2. **不支持 tool-use** — 纯 conversational S2S 模型，**完全跑不了 Code mode 的
   `file_read`/`file_write`/`todo_write`/MCP 工具调用**。等于把 P4-S22/S23 整套
   Code 能力废掉。它只能"陪聊"不能"干活"。
3. **部署不可行** — 7B 权重 + Moshi codec ≈ 十几 GB，全双工实时推理至少要
   RTX 3090/4090 24G。叠加在已有的 NVIDIA-only 妥协之上，目标用户群基本归零。
4. **架构不能直接替换** — 当前 `ASR → LLM(tools) → TTS` 三段分离架构里，
   LLM+tools 这层不能丢。引入 PersonaPlex 等于多一条平行链路，状态机 + UI
   复杂度大幅上升。
5. **License** — NVIDIA Open Model License（不是 MIT/Apache），商用要单独审。

### 等什么时候再看
任意一条满足之前，对 DeskPet 实际语言效果提升 = 0：
- [ ] 出中文版（NVIDIA 这种 research drop 大概率不会迭代中文，更可能等
      Qwen-Omni / 阿里 / 腾讯系列出对标 S2S 模型）
- [ ] 出蒸馏版（≤3B，8G 显存可跑）
- [ ] 出 tool-use 兼容方案（让 S2S 模型能 routing 到 LLM 工具层）

### 真要做的话的设计草图（未来 reference）
```
工作模式（中文 + Code，默认）：保留现有 ASR → gpt-5.5(tools) → edge-tts
闲聊模式（英文 + 玩，可选）   ：PersonaPlex 全双工 S2S，单独 toggle
```
代价：~15GB 模型下载、UI 加模式切换、状态机升一档复杂度、只服务英文+高端显卡用户。
**ROI 不划算**。同样工程量投到「edge-tts 选音优化 + 桌宠表情/动作和语音的同步」
收益更明显。

---

## 3. 通用兼容化路线图

未排期，等 Phase 5 / Phase 6 再认真讨论：

| 阶段 | 目标 | 解锁用户群 |
|------|------|----------|
| **Tier 1 — CPU fallback** | NVIDIA 不存在时降级到 CPU 路径 | Intel/AMD/老本 用户 |
| **Tier 2 — Cloud-only 模式** | 允许把 ASR/LLM/TTS 全走云端 API（chinzy/OpenAI/Azure），本地零模型 | 显卡白嫖党 + 移动办公 |
| **Tier 3 — AMD/Intel 原生加速** | ROCm/XPU 走 PyTorch 官方 wheel | 高端 AMD/Intel 显卡用户 |
| **Tier 4 — Apple Silicon** | macOS + MPS 后端 | mac 用户 |
| **Tier 5 — 高级语音体验** | PersonaPlex 类 S2S 模型作为可选模式 | 英文高端发烧友 |

每个 Tier 跨度约 2-4 个 sprint，建议按用户 ROI 顺序推进（Tier 1 > Tier 2 > 其它）。

---

## 备注

- 任何新增的硬件依赖（新模型、新加速库）**必须**先在这里登记，并写明
  fallback 策略，否则不准合入 master
- 每次发行 RC 时，对照这份文档检查是否有新妥协未记录
- 已经实施的兼容化工作完成后，把对应条目从这里移到 `CHANGELOG.md` 并标注 ✅
