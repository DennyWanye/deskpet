# P5-S2 Self-Healing Harness — Design

## 1. 背景与原则

P5-S1 给桌宠装了"被动监督"（watchdog 60s 扫一次，supervisor LLM 给 hint，hint 进 NudgeQueue）。**问题**：hint 必须等用户下次发消息才被注入 → 不算自愈。

业界 2026 主流共识（见 [proposal.md](proposal.md) 的 references）：

> **Self-healing agent loop = 检测失败 → 诊断 → 自动修复 → 验证，全程无人。** 人只在多次自愈失败后被叫。

我们这次的 4 项设计决策都围绕**让无人自愈成为默认路径**：

1. **Sensor + Remediation Hint**：从源头让大多数错误有自描述修复路径，根本不需要 supervisor 介入
2. **Error Taxonomy**：永久错误立刻 break-out，节省 90% 重试浪费
3. **Per-tool Circuit Breaker**：单工具反复失败时熔断，让 LLM 换思路
4. **Auto-Resume Orchestrator**：上述都没救住时 supervisor 给 hint + 自动重跑，而不是弹窗等用户

## 2. 关键设计决策

### 2.1 为什么"工具自己 own 错误 hint"，而不是 LLM 即兴生成？

考虑过 3 个方案：

**A. Tool 自带 hint**（选）—— 工具开发者写死 error → hint 映射，比如 `run_shell` 的 schema 检查到 missing command 就回 `{"error": "command required", "hint": "command 字段必填，例如 \"ls -la\"", "examples": [...]}`。

**B. Supervisor LLM 看 error 即兴生成 hint** —— 灵活但贵 (每次 error 都得调 supervisor)、慢、不可预测。

**C. 主 agent 看 error 自己反思** —— 这就是当前死循环的成因，靠不住。

**A 胜出**因为：(1) 修复信息在工具实现者那里最准确 (他知道这个 error 是缺什么)，(2) 零额外 LLM 成本，(3) 确定性可测试（hint 文案是常量）。

### 2.2 为什么 Error Taxonomy 用 3 类，不是 5 或 2？

调研过 ICLR 2026 *Agent Error Taxonomy* 论文的 5 类（memory / reflection / planning / action / system）—— 太细，工程上落地难（很多 error 跨类）。

我们这次只关心**重试决策**，所以收敛到 3 类，正交：

| Class | 是否重试 | 是否 escalate supervisor | 例子 |
|---|---|---|---|
| `TransientToolError` | ✅ 指数退避重试 | ❌ | timeout, network reset, 503 |
| `PermanentToolError` | ❌ 立刻 break | ✅ supervisor 拿 hint 重跑 | missing required param, file not found, schema invalid |
| `HallucinationError` | ❌ 立刻 break | ✅ supervisor 必须介入 | tool_not_found, malformed tool_call structure |

3 类够用，"未知错误"默认归 Transient（保守，宁可重试）。

### 2.3 为什么 Circuit Breaker 是 per-(session, tool) 不是全局？

**Per-tool**：write_file 熔断不该影响 read_file。两个工具能力正交，没有相关性，没理由一起断。

**Per-session**：用户 A 的 session 把 `run_shell` 用挂了，不该影响用户 B 的 session（多 panel/多项目并行场景）。SessionA 和 SessionB 跑在不同 LLM context 里，工具失败可能是 prompt-specific 的，不能跨 session 传染。

数据结构：`dict[(sid, tool_name), CircuitState]`。

三态机用业界标准：

```
        ┌─ CLOSED ─┐                  正常通行
        │   ↓ failure_count >= 3
        ↓                              熔断；阻止调用
       OPEN ─ wait cooldown ─→ HALF-OPEN
        ↑ failure                    │   下一次调用作为探针
        └────── HALF-OPEN ←──────────┘
                  ↓ success
                CLOSED
```

cooldown=60s 是经验值（足够 chinzy proxy 临时抽风恢复，又不会让用户等太久）。可配置。

### 2.4 为什么 Same-Signature 检测放在 agent_loop 里，不在 circuit breaker 里？

两者分工：

| Circuit Breaker | Same-Signature Detector |
|---|---|
| 看"工具调用结果是否成功" | 看"工具调用参数是否重复" |
| Per-(sid, tool) | Per-(sid, tool, args_hash) |
| OPEN 时拒绝调用 + 给 alternatives | 第 3 次 detect 时跳过执行 + 注入 system msg |
| 适合 transient 类失败累积 | 适合 LLM 不长记性的死循环 |

举例：LLM 用空 args 调 write_file 3 次都失败 → **同 signature**（因为 args_hash 都是 `{}`） → signature detector 第 3 次拦下，注入 "你重复 3 次同一调用，看看 hint 字段或换思路"。

如果不同 signature 都失败（比如 path 一会儿 `/sys/foo` 一会儿 `/proc/bar`），signature detector 不拦，但 circuit breaker 会在第 3 次 failure 时 OPEN。两者互补。

### 2.5 Auto-Resume 怎么避免"自愈失败再自愈"死循环？

硬上限 `max_auto_resume_attempts=2`：

```
Turn 1: agent loop → max_iterations 触发 → orchestrator 调 supervisor → spawn Turn 2 (attempt=1)
Turn 2: agent loop → 又 max_iterations → orchestrator 调 supervisor → spawn Turn 3 (attempt=2)
Turn 3: agent loop → 还 max_iterations → orchestrator 看 attempt=2 已达上限 → emit auto_resume_exhausted ws event → 弹用户
```

`auto_resume_attempts` 字段存在 `SessionActivity` 里，新 user message 时清零（"用户接手了，从头开始计")。

### 2.6 为什么不直接复用 P5-S1 的 supervisor 而是新建 orchestrator？

Supervisor 已经做的事：snapshot → LLM → 决定 action → 推 hint。
Orchestrator 要做的：在收到 action 后**根据 action 决定下一步**（spawn 新 task / 等用户 / 不做事）。

把 orchestrator 独立出来，supervisor 保持单一职责（只做诊断）。orchestrator 是策略层。这样 supervisor 单测不需要 mock 整个 chat handler，orchestrator 单测不需要 mock LLM。

### 2.7 Hint 注入位置：system message 而非 user/assistant

用户 P5-S1 已经验证过的做法（`P5-S1 D5` 注入点 = system message at top of system stack）。继续用。

理由：
- system 消息有"指令性"权重，LLM 更服从
- 不污染 user/assistant 对话历史，前端渲染可以选择不显示
- 多个 hint 可合并成一段 `[Supervisor] 1. ... 2. ...`

### 2.8 为什么不引入 LangGraph / LlamaIndex 这种现成框架？

调研过。结论：

- **不引入** —— 我们已有 AgentLoop + ToolRegistry + SessionDB + Watchdog，runtime 都熟，引框架只为复用 circuit breaker / taxonomy 这种通用模块不值得（依赖膨胀，重写自己的代码反而维护成本低）
- **借鉴** —— 三态 circuit breaker 的状态机是业界标准，我们照搬概念但用 100 行 Python 自实现
- **借鉴** —— LlamaIndex 的 "you have N tries remaining" nudge 思路在 Phase 2 后可以加（agent_loop 在 iteration > max_iter * 0.7 时主动告诉 LLM "你已用 35/50 次，剩 15 次"）

### 2.9 跟 P5-S1 Supervisor / Hook A/B 的关系

P5-S1 + 我已经做的 Hook A/B 是**第一道防线**（被动监控 + completion guard）。

P5-S2 这次是**第二、三道防线**：

```
[第一道] Hook A: stop_reason=end_turn 但 todos 没完 → 反弹 nudge (in-loop)
[第二道] Phase 0+1+2: tool error 自带 hint + permanent break-out (大多数 case 在这里就修了)
[第三道] Phase 3: same-signature 检测 + circuit breaker (LLM 不听话也能挡)
[第四道] Phase 4: AutoResume 调 supervisor 自动重跑 (前三道都没救住时)
[第五道] P5-S1 Watchdog: 长期 stuck 时被动唤醒 supervisor (兜底)
```

5 道防线**层层降级**：成本从 0 → 0 → 1 LLM call → 1 LLM call + 1 task spawn → 1 LLM call。前期防线越多越厚，越省钱。

## 3. 数据流

### 3.1 正常成功 case (95%+)

```
user msg → AgentLoop iter 1 → tool call → success → AgentLoop iter 2 → final → done
                                          ↑
                                  Phase 0 hint 不出场
```

零开销。

### 3.2 工具用法错误 case (4%)

```
user msg → AgentLoop iter 1 → write_file(no path) → tool returns {error, hint}
        ↓
        AgentLoop iter 2 → LLM 看到 hint → write_file(path="...") → success → final
```

Phase 0 修复，1 次 retry 内解决，无需 supervisor。

### 3.3 重复死循环 case (0.5%)

```
user msg → AgentLoop iter 1..2 → write_file(no path) 重复 → 第 3 次 same-signature detected
        ↓
        AgentLoop 跳过工具执行 + 注入 "你在重复同一调用..." system msg → iter 4 → LLM 换思路 → final
```

Phase 3 拦截，4 次 iter 内解决。

### 3.4 永久错误 case (0.3%)

```
user msg → AgentLoop iter 1 → write_file(/sys/foo) → tool returns {error, hint, classified=permanent}
        ↓
        AgentLoop emit ErrorEvent(permanent_tool_error) + return
        ↓
        AutoResume orchestrator → supervisor.diagnose → "用户路径权限问题，建议改用 /tmp"
        ↓
        spawn new chat task with hint as system msg → AgentLoop iter 1 → 用 /tmp/foo → success → final
```

Phase 2+4，1 次自动 resume 解决。

### 3.5 自愈耗尽 case (<0.1%)

```
3.4 流程 × 2 attempts → 都失败 → orchestrator emit auto_resume_exhausted → 弹用户
```

5 道防线最后一关。用户接手。

## 4. 测试策略

### 4.1 测试金字塔

```
        E2E (manual)        ← 每 phase 必跑 + evidence 文档
            ↑
     Integration (5-10)     ← circuit breaker × dispatch, orchestrator × supervisor
            ↑
        Unit (40+)          ← 每个 class / pure function
```

### 4.2 mock 边界

- **mock LLM provider**：用 `_ScriptedLLM`（已有 P5-S2 Hook A 测试用过）
- **mock SupervisorAgent**：直接 instantiate 但替换 LLM 为 scripted；或更简单 stub `async diagnose(...)` 直接返回 SupervisorAction
- **不 mock SessionDB**：用真 sqlite (in-memory or tmp_path)，测真 schema
- **不 mock CircuitBreaker**：测真状态机
- **mock chat handler**：orchestrator 单测时 chat_dispatcher 是 callable，传 fake 即可

### 4.3 关键回归测试

每个 phase 必须保留以下 case 防回归：

- Phase 0: `test_all_os_tools_error_have_hint_field` (扫描所有工具)
- Phase 2: `test_classify_unknown_defaults_transient` (保守语义)
- Phase 3: `test_per_tool_isolation` + `test_per_session_isolation`
- Phase 4: `test_max_attempts_caps_resume`

## 5. 配置参数 (新)

```toml
[supervisor]
# P5-S1 已有
enabled = true
scan_interval_seconds = 60
stuck_threshold_seconds = 900
dedup_seconds = 720
startup_grace_seconds = 30

# P5-S2 新
auto_resume_enabled = true
max_auto_resume_attempts = 2
circuit_breaker_threshold = 3
circuit_breaker_cooldown_seconds = 60
tool_signature_repeat_threshold = 3
idle_with_todos_threshold_seconds = 60   # 已有 (Hook B)
```

所有新 key 都有默认值，向后兼容（旧 config.toml 无需修改）。

## 6. 取舍 / 已知局限

- **Sensor hint 文案需要每个工具开发者写**：增加工具贡献门槛。但 LLM 错误信息消费者就是 LLM 自己，让人写一次省 LLM 万次摸索，划算
- **Error 分类是关键字匹配，不是模型推断**：可能漏分类。Mitigate: 默认 Transient（保守），加新关键字成本低
- **Auto-resume 攻击面**：恶意 user prompt 让 LLM 故意触发 max_iterations 烧 token？Mitigate: max_auto_resume_attempts=2 + per-session daily budget (已有)
- **chinzy 流式截断 root cause 不修复**：Phase 1 只 dump 诊断不修。修需要 chinzy 那边或换 provider

## 7. 跟最新业界对比一下

| 我们做的 | 业界对应 | 来源 |
|---|---|---|
| Sensor + remediation hint | "Sensors with structured feedback for LLM consumption" | Martin Fowler 2026 |
| Per-tool circuit breaker | "Per-tool circuit breakers (CLOSED/OPEN/HALF-OPEN)" | Towards Data Science 2026 |
| Error taxonomy 3 类 | "TransientToolError / ToolNotFoundError / InvalidInputError" | Towards Data Science 2026 |
| Auto-resume orchestrator | "Self-healing agent loop with diagnostic feedback" | MindStudio + Cobus Greyling 2026 |
| Same-signature detection | "Event-based prevention if same tool same params appears twice" | Adamo Software 2026 |
| Hard cap on max attempts | "Hard stop based on number of turns" | Yamishift 2026 |

完全对齐 2026 主流，不是 over-engineering 也不是欠工程。
