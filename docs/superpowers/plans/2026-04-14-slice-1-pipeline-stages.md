# S1 — Pipeline 阶段化 + 情感/动作 → Live2D

**目标日期：** 2026-04-14
**分支：** `feat/slice-1-pipeline-stages`
**前置：** S0（AgentProvider 抽象，已 landed on `feat/slice-0-agent-abstraction`）
**授权：** "全接受" S0-S4（本 slice 按 X2=c 自动接续 S0）
**执行模式：** codingsys / 单 Agent / 无 MCP 调研（内部重构 + 增量特性）

---

## 1. 为什么做这个 slice

S0 把 `/ws/control` 接到了 `agent_engine`，但 `/ws/audio` 的 `VoicePipeline` 仍然直接持有 `LLMProvider` 引用（S0 HANDOFF §5 "供后续 slice 参考"）。S2 接记忆 / S3 接工具路由时需要一个统一入口，否则会出现"chat 有记忆但语音无记忆"这种割裂。

与此同时 V5 §4.4 明确要求 LLM 输出带 `[emotion:xxx]` / `[action:xxx]` 标签并驱动 Live2D 表情/动作切换——这是 deskpet 作为"桌宠"（而不只是语音助手）的核心体验，S1 是自然的携带时机。

---

## 2. 范围与非范围

### ✅ 范围内
- **P1**：`VoicePipeline` 参数 `llm: LLMProvider` → `agent: AgentProvider`
- **P2**：新建 `backend/pipeline/tag_parser.py`：流式解析 `[emotion:xxx]` / `[action:xxx]`，剥离标签后返回清洁文本（供 TTS），同时发出事件
- **P3**：control_ws 新增事件类型 `emotion_change` / `action_trigger`，由 pipeline 在 LLM stream 过程中推送
- **P4**：前端 `messages.ts` 补 Emotion/Action 事件类型（仅类型定义，不接 Live2D 行为——留给 S1.5 或后续前端 slice）
- **P5**：单元测试覆盖 tag_parser + pipeline agent 路由

### ❌ 非范围
- Live2D 表情实际切换（Cubism 参数绑定）— 单独前端 slice
- 工具调用（S3）
- 记忆注入（S2）
- prompt 工程（决定 LLM 何时输出标签）— 交给 `SimpleLLMAgent` 将来的 system prompt 层，S1 只保证"如果 LLM 输出标签，pipeline 能解析"

---

## 3. 设计要点

### 3.1 Agent 统一入口
`VoicePipeline.__init__` 签名：

```python
def __init__(
    self,
    vad: SileroVAD,
    asr: FasterWhisperASR,
    agent: AgentProvider,       # was: llm: OllamaLLM
    tts: EdgeTTSProvider,
    control_ws: WebSocket | None = None,
    session_id: str = "default",  # NEW — for agent_engine 记忆寻址
):
```

`main.py:193` 构造时换成 `agent=service_context.agent_engine`，session_id 从 query_params 透传。

### 3.2 流式标签解析器

LLM token 是按字符串流切片到达的（可能 `[emot` 在一帧、`ion:happy]` 在下一帧）。`StreamingTagParser` 维护 buffer，只在识别到完整 `[...]` 才 emit 事件；其余字符直接 flush 到输出文本流（供 TTS）。

规则：
- 只识别 `[emotion:xxx]` / `[action:xxx]` 两种标签（白名单，避免误触）
- `[` 开启潜在标签 → 进入 `TAG_OPEN` 状态 → 遇 `]` 解析 → 无效标签原样 flush
- 识别成功：emit `{"kind": "emotion", "value": "xxx"}` 或 `{"kind": "action", "value": "xxx"}`，**不**写入输出文本
- 最大 buffer 深度 32 字符（防 LLM 吐 `[[[[` 卡死）；超限强制 flush

### 3.3 Pipeline 改造结构

`_process_utterance` 内 LLM 循环改为：

```python
parser = StreamingTagParser()
response_text = ""
async for token in self._agent.chat_stream(messages, session_id=self.session_id):
    for evt_or_text in parser.feed(token):
        if isinstance(evt_or_text, str):
            response_text += evt_or_text
        else:  # TagEvent
            await self._emit_tag_event(evt_or_text)
# 结束时 flush 残留
for tail in parser.flush():
    if isinstance(tail, str):
        response_text += tail
```

`_emit_tag_event` 通过 `control_ws` 推：
```json
{"type": "emotion_change", "payload": {"value": "happy"}}
{"type": "action_trigger", "payload": {"value": "wave"}}
```

### 3.4 前端类型定义
仅加三个 interface + 扩 union，不改任何渲染逻辑。

---

## 4. 文件清单

### 新增（2）
| 文件 | 估行 | 说明 |
|---|---|---|
| `backend/pipeline/tag_parser.py` | ~70 | StreamingTagParser 状态机 + TagEvent |
| `backend/tests/test_tag_parser.py` | ~80 | 6-8 单测（chunk 边界、无效标签、白名单、buffer 溢出） |

### 修改（3）
| 文件 | 估净变化 | 说明 |
|---|---|---|
| `backend/pipeline/voice_pipeline.py` | +30 / -10 | 参数改 agent；插 parser；推 control 事件 |
| `backend/main.py` | +2 / -2 | 构造参数 agent / session_id |
| `tauri-app/src/types/messages.ts` | +10 / -1 | EmotionChange + ActionTrigger + 扩 union |

### 可选测试补充
| `backend/tests/test_voice_pipeline.py` | ~60 | 如之前没有，补 pipeline agent 路由单测（mock agent stream 吐带标签 token） |

**预算：** 生产代码 ≤80 行，测试代码 ≤150 行（应用 S0 HANDOFF §4 D1 教训，分开计）。

---

## 5. 实施步骤

1. 建分支 `feat/slice-1-pipeline-stages`
2. 写 `tag_parser.py` + `test_tag_parser.py`（TDD：先写测试）
3. 改 `voice_pipeline.py` 接 Agent + 插 parser
4. 改 `main.py:193` 构造参数
5. 改前端 `messages.ts` 类型
6. 补 pipeline 单测（如需要）
7. 跑 gate：`cd backend && uv run pytest tests/ -v`
8. 写 HANDOFF
9. 4 commit 拆分：
   - `feat(backend): streaming tag parser for emotion/action`
   - `refactor(backend): VoicePipeline routes through agent_engine`
   - `feat(frontend): emotion/action message types`
   - `docs: add S1 plan + HANDOFF`

---

## 6. 质量门控

- [ ] `pytest tests/ -v` 全绿（期望 ≥25 tests passed）
- [ ] `test_tag_parser.py` 覆盖：普通文本 / emotion / action / chunk 切半 / 未闭合 / 白名单外（如 `[color:red]` 原样透出） / buffer 溢出
- [ ] `python -c "from pipeline.voice_pipeline import VoicePipeline"` 导入不炸
- [ ] 手动 smoke：启动后端 + 前端 WS 连接，观察 control channel 不会因为新事件类型"error: unknown type"（向后兼容）

---

## 7. 风险与缓冲

| 风险 | 缓冲 |
|---|---|
| LLM 标签格式漂移（输出中文 `[情绪:开心]`） | S1 只认英文白名单；中文版本留给 S2/prompt 层 |
| parser 状态机 bug 吞字符 | 100% flush 保证 = 非标签字符必定出口；测试用例覆盖所有状态 |
| 前端 `messages.ts` 改动与 useAudioChannel 冲突 | 仅加类型，不改运行时路由；TS 编译检查兜底 |

---

## 8. 偏离 Plan 的容忍度

- D-level 偏离（≤5 行无关修复、补配置漏项）：直接做，HANDOFF §4 记录
- C-level 偏离（改 Agent 接口签名）：STOP 回报
- 若 LLM 当前 prompt 根本不吐标签（实际观察）：S1 接受"解析器存在但暂无数据路径"的状态，HANDOFF 说明等 prompt slice

---

**状态：** 🟡 Planned, 待执行
**下一步：** 切分支 → TDD tag_parser → 改 pipeline → gate → commit → 接 S2/S3 并行
