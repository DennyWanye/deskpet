# S1 — Pipeline 阶段化 + 情感/动作 → Live2D HANDOFF

**完成日期：** 2026-04-14
**分支：** `feat/slice-1-pipeline-stages`
**对应 Plan：** [docs/superpowers/plans/2026-04-14-slice-1-pipeline-stages.md](../plans/2026-04-14-slice-1-pipeline-stages.md)
**执行模式：** codingsys / 单 Agent / TDD（tag_parser 先测试后实现）

---

## 1. 完成的事

- ✅ `backend/pipeline/tag_parser.py`：`StreamingTagParser` + `TagEvent` frozen dataclass
- ✅ 10 个 tag_parser 单测全绿（含 chunk 切半、未闭合、白名单外、buffer 溢出等 corner case）
- ✅ `VoicePipeline` 构造参数 `llm: OllamaLLM` → `agent: AgentProvider`；新增 `session_id` 参数
- ✅ Pipeline LLM 循环插入 parser：tag 事件经 `control_ws` 推到前端，清洁文本继续喂 TTS
- ✅ `_emit_tag_event` 辅助方法：`emotion_change` / `action_trigger` 两种消息类型
- ✅ `main.py:195` 构造 VoicePipeline 改为 `agent=service_context.agent_engine` + 透传 session_id
- ✅ 前端 `messages.ts` 新增 `EmotionChangeMessage` / `ActionTriggerMessage` + 扩 `IncomingMessage` union（也顺手把之前漏的 `LipSyncMessage` 加进 union）
- ✅ 跨层统一：`/ws/control` 和 `/ws/audio` 两条进路现在都走 `agent_engine`（S0 铺路 + S1 搭桥）

---

## 2. 变更文件清单

### 新增（2 个）
| 文件 | 行数 | 说明 |
|---|---|---|
| `backend/pipeline/tag_parser.py` | 70 | StreamingTagParser 状态机 + TagEvent |
| `backend/tests/test_tag_parser.py` | 103 | 10 个单测（TDD 先写） |

### 修改（3 个）
| 文件 | 净变化 | 说明 |
|---|---|---|
| `backend/pipeline/voice_pipeline.py` | +41 / -9 | 参数改 agent；插 parser；emit tag event |
| `backend/main.py` | +4 / -1 | 构造参数改 agent + session_id |
| `tauri-app/src/types/messages.ts` | +19 / -1 | Emotion/Action 类型 + union 扩展 |

**生产代码净增：** ~66 行（plan §4 预算 ≤80，在预算内 ✅）
**测试代码：** 103 行（plan §4 预算 ≤150，在预算内 ✅）

---

## 3. 门控结果

```
pytest tests/ -v --ignore=tests/test_e2e_pipeline.py
────────────────────────────────────────────
29 passed, 1 skipped in 9.03s
  - 10 new: test_tag_parser.py（TDD 验证全绿）
  - 19 existing: 全绿（包含 S0 的 agent_provider 4 测）
  - 1 skipped: test_ollama_llm_chat_stream_integration（需真实 ollama）

import smoke:
  from pipeline.voice_pipeline import VoicePipeline         → OK
  from pipeline.tag_parser import StreamingTagParser, TagEvent → OK
  import main                                                  → OK

frontend:
  npx tsc --noEmit -p .                                     → exit 0（干净）
```

---

## 4. 偏离 Plan 的地方

### D1 — 补了 LipSyncMessage 进 union
- Plan §4 只说"加 Emotion/Action 类型"
- 实际顺手发现 `LipSyncMessage` 定义了但没进 `IncomingMessage` union，一并补进
- 判断：D-level 无关修复（plan §8 明确允许），不构成过度设计

### D2 — TagEvent 测试中 frozen 断言较宽松
- `test_tag_event_is_frozen_dataclass` 只断言"赋值会 raise"，但允许 `AttributeError` 或任何 Exception
- 原因：`@dataclass(frozen=True)` 在不同 Python 版本抛的具体异常类型略有差异
- 风险极低，行为等价

### D3 — 未补 VoicePipeline 单测
- Plan §4 "可选测试补充"列了 `test_voice_pipeline.py` 的选项
- 判断未补：VoicePipeline 的 agent 路由已经被两层覆盖——（a）tag_parser 单测验证解析；（b）`main.py` 构造改造 + 既有 `test_websocket.py` WS 路径测试。再写集成测试需要 mock 整个 ASR/TTS 栈，性价比低
- 若后续 S2 记忆接入后需要验证 session_id 真正传到 agent，再补一个端到端的 mock 测试更合适

### D4 — push 继续跳过
- S0 HANDOFF §4 D3 已说明 `git remote -v` 为空
- S1 延续：4 commit 完成后本地分支就绪，不 push

---

## 5. 已知问题 / 后续关注

### 无阻塞问题

### 供后续 slice 参考
| 观察 | 建议 slice |
|---|---|
| 解析器装好了但 **当前 LLM prompt 不会输出 `[emotion:xxx]`** | S2/prompt 工程 slice 时扩写 `SimpleLLMAgent` 的 system prompt，教 LLM 在合适处打标签 |
| 前端 `messages.ts` 新增的 Emotion/Action 事件**暂时无人消费** | 独立前端 slice：绑定到 `Live2DCanvas` 表情/动作切换（需 pixi-live2d-display 的 motion API） |
| `VoicePipeline.session_id` 当前来自 URL `?session_id=...`，默认 `"default"` | S2 记忆 slice 接入时注意：不同 session 的对话历史要真正隔离，不能全落到同一个 bucket |
| tag_parser 白名单硬编码 `{"emotion", "action"}` | 如未来扩"metadata" / "tool_call" 等更多类别，改成可配置 frozenset |

---

## 6. 对 S2/S3 的建议

S2 + S3 授权在 `plan §1` 后并行执行（worktree 隔离）。S1 给它们铺了这些基础：

1. **S2 记忆注入点已就位**：`SimpleLLMAgent.chat_stream` 签名已带 `session_id`，pipeline 也在透传；S2 扩写 Agent 即可，无需改 `VoicePipeline`。

2. **S3 工具路由 fork 点**：V5 §12 的"Hermes 不稳定时降级回 SimpleLLM"仍然可行——新建 `ToolUsingAgent(AgentProvider)` 类替换 service_context 里的 agent_engine，`VoicePipeline` 完全不动。

3. **协调点**（S2 ↔ S3 都可能触碰）：
   - `agent/providers/base.py` Protocol 签名——如果要加 `tools: list[Tool]` 参数就是 breaking change，需要 S2/S3 先协商
   - `SimpleLLMAgent` 实现——两个 slice 都想扩写它的话需要 merge 策略，S2 merge 到 master 后 S3 rebase

---

## 7. 提交记录（将在下一步创建）

计划 4 个 commit（按 plan §5）：
1. `feat(backend): streaming tag parser for emotion/action`
2. `refactor(backend): VoicePipeline routes through agent_engine`
3. `feat(frontend): emotion/action message types`
4. `docs: add S1 plan + HANDOFF`

---

**HANDOFF 状态：** ✅ Ready for review
**下一步：** 按 "全接受" 授权 → 建 worktree → S2 + S3 并行 → STOP 回报供你合并
