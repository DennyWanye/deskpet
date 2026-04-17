# Future Optimizations Backlog

> 用于记录"当前不做、但未来阶段值得评估"的候选优化项。
> 每一条都必须附上"触发条件"（什么情况下应该从 backlog 升级到正式 sprint）。
> 不是 TODO 列表 —— TODO 进 Phase Sprint plan；这里只收"战略级别的长周期选项"。

Created: 2026-04-17
Owner: 项目 Lead
Last reviewed: 2026-04-17

---

## FO-001 · 评估 NVIDIA/PersonaPlex 端到端全双工语音模型

**建议归属阶段**：P3 spike（不进 P2）

### 背景
我们在 2026-04-14 roadmap §3.3 里把 P2-2 Sprint 代号命名为 "PersonaPlex 实时双工"，**是借用当时刚开源的 NVIDIA/PersonaPlex 项目作为"全双工语音体验"的图腾**，并不是要真的接入该模型的权重。实际 P2-2 技术路线是经典流水线的流式化（VAD + faster-whisper + LLM + edge-tts），架构和 PersonaPlex 完全不同。

NVIDIA/PersonaPlex 是基于 Kyutai Moshi 架构的 7B 端到端语音到语音模型，能够：
- 单一模型同时"听 + 思考 + 说"（没有 ASR / LLM / TTS 边界）
- 全双工为原生能力（训练时即如此）
- 延迟 ~200ms（显著低于我们当前流水线的 ~800ms-1.2s）
- persona 控制：文本 role prompt + 音频 voice conditioning

GitHub: https://github.com/NVIDIA/personaplex
HuggingFace weights: nvidia/personaplex-7b-v1

### 可能收益（如果替换成功）
1. 延迟从 ~1s 级降到 ~200ms 级，交互体验质变
2. 打断逻辑不再需要手写状态机（BargeInFilter），模型原生处理
3. 声音克隆能力内置（audio prompt 即可定制 persona 声线）
4. 代码栈大幅简化：消除 VAD + ASR + LLM + TTS 四个 provider 的协调逻辑

### 为什么暂不做（2026-04 当前的阻塞点）

| 阻塞点 | 现状 |
|---|---|
| **硬件门槛** | 推荐 Blackwell GPU；消费级 PC 至少要 4090/24GB 才能跑 fp16；与"本地优先、普通 PC 可跑"定位冲突 |
| **厂商锁定** | 只支持 NVIDIA CUDA；AMD / Intel / Apple Silicon 用户直接被排除 |
| **中文质量未知** | Moshi 基底主要英文训练；中文多语种能力需要实测（论文有提及但产品质量没验证） |
| **Persona 系统冲突** | 我们的 Live2D Hiyori 已有完整 persona 设计（表情/动作/台词风格），PersonaPlex 的 persona 是训练态 audio prompt，两套系统不兼容 |
| **项目成熟度** | v1 刚开源几个月，生态不稳定；依赖 libopus-dev，在 Windows 上的支持链待验证 |
| **显存占用** | 7B fp16 常驻 ~14GB；与当前 Whisper(2.7GB) + Edge-TTS(0) + Ollama(视模型) 的动态占用模式差异大 |

### 触发"启动评估 spike"的条件（任一满足）
1. NVIDIA 或第三方发布 **3B 或以下**的量化版本（Q4/Q5），能在 8GB 显存运行
2. **中文原生**的同类端到端 S2S 模型出现（如 CosyVoice / Index-TTS / 通义千问的后续迭代出端到端版本）——国内厂商更可能首发
3. Apple Silicon / Intel NPU 版本出现，打破 NVIDIA 锁定
4. 用户群画像明确偏向**游戏/设计师**（普遍持有 4090 及以上），此时硬件门槛不再是障碍
5. Phase 2 流水线路线已走到瓶颈（延迟 p95 卡在 500ms 以上打不下来）

### 评估 Spike 工作量（未来真启动时）
- **2-3 天**：在 4090 上跑官方 demo，测中文对话质量 + 延迟 p95
- **2-3 天**：评估能否接入现有 Live2D 唇同步（PersonaPlex 是否暴露 phoneme/amplitude 流）
- **1-2 天**：对比 CPU offload 模式的延迟回退表现
- **输出**：Go / No-go 决策书 + 如果 Go 列出 P3-Sx 详细 slice

### 风险
- 即便技术指标达标，也可能因为**中文 voice cloning 的版权 / 隐私顾虑**（声音是生物识别数据）被 legal / 伦理 review 卡住
- 离线可用性可能下降（某些端到端模型假设网络）
- Persona 的美术一致性（声音 + Live2D 外观）协调成本

### 相关文档
- 原始 P2-2 代号来源：`docs/superpowers/plans/2026-04-14-phase2-v6-roadmap.md` §3.3
- Phase 1 handoff 里的扩展点预留：`docs/superpowers/plans/2026-04-14-phase1-handoff.md` §Phase 2 features

### 命名清理（可立即做的小事）
未来 roadmap 修订时，建议把 P2-2 Sprint 名字从 "PersonaPlex 实时双工" 改成 "Realtime Duplex Voice" 或 "实时双工语音"，避免和 NVIDIA 同名模型产生歧义。

---

## 模板（新增条目时复制）

## FO-00X · {标题}

**建议归属阶段**：{P3 spike / Phase 3 / Phase 4 / ...}

### 背景
{为什么会冒出这个想法；当前状态 vs 候选方案}

### 可能收益

### 为什么暂不做

### 触发"启动评估"的条件
