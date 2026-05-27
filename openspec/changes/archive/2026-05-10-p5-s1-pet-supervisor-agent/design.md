## Context

DeskPet 在 P4-S22/S23 上线了 Code 模式的多 session 能力（最多 2 并发，code-panel 第二窗口承载，SessionGridView 多项目仪表盘）。但**主 agent 一旦卡住，没有任何上层观察、判断、干预机制**——这次提案补齐这条链路。

### 现状代码扎根

| 文件 | 关键现状 |
|------|---------|
| `backend/main.py:984` | `_chat_inflight: dict[str, asyncio.Task]` 已存在，按 sid 跟踪活动任务 |
| `backend/main.py:704` | `_todo_broadcaster` 已实现"广播到所有 control WS"模式（多窗口同步） |
| `backend/main.py:2197` | `async for ev in _agent.run(...)` 是事件 emit 主入口，每个事件都附带 sid |
| `backend/main.py:2089-2173` | `_msgs` 构造区，已有"插 system message 到 system stack 头部"的成例（P4-S25 plan injection） |
| `backend/main.py:2489-2493` | "用户重打 → cancel 旧任务 → 起新任务"的并发模式已落地 |
| `backend/agent/agent_loop.py` | AgentEvent 协议干净（AssistantMessageEvent / ToolCallEvent / ToolResultEvent / FinalEvent / ErrorEvent） |
| `backend/deskpet/code_mode/state.py` | `CodeModeManager.all_sessions()` 提供活动 session 枚举 |
| `tauri-app/src/components/Live2DCanvas.tsx` | `Live2DHandle` 已暴露 `setExpression` / `playMotion` |
| `tauri-app/public/assets/live2d/hiyori/Hiyori.model3.json` | **无 Expressions 字段**，仅 `Idle: m01..m10` + `TapBody: m04` |

### 约束

- **Live2D 表达力上限**：Hiyori 没有专门的"困惑/担忧"表情资源。本提案不引入新美术资源（用户已确认）
- **NVIDIA-only**：当前 GPU 路径未变，supervisor LLM 调用走云端 API（the relay 或同 OpenAI 兼容 endpoint）
- **首要不打扰原则**：90% 的"看起来卡住"实际只是慢，supervisor 必须保守（默认 `wait`），避免误打扰用户

## Goals / Non-Goals

**Goals:**

1. 自动检测 Code 模式下 session 的"卡住"状态（错误状态 + 15 分钟无活动两个触发条件）
2. 通过独立的 LLM 自检调用，对卡住状态做诊断与决策（4 种 action: wait/nudge/ask_user/cancel）
3. 实现"续命注入"——supervisor 输出的 hint 安全注入主 agent 下一轮，不丢已做工作
4. 桌宠用现有 Hiyori motion 集合 + 气泡颜色，可视化反馈最危险 session 的 severity
5. 用户可一键关闭 supervisor（settings toggle）+ 一键跳到出问题的 session（点桌宠气泡）
6. supervisor 自身的失败与超时不能拖垮主 agent 流程

**Non-Goals:**

1. **不**实现 `cancel` action 的执行路径（仅留协议字段，初版强制降级为 `ask_user`）——避免误杀用户工作
2. **不**做"假完成"（形态 8）的精确检测——这需要一个独立的 verifier agent，留 P5-S2
3. **不**改 `agent_loop.py` 内部循环——续命注入用 done_callback 在外部完成
4. **不**支持非 Code 模式 session 的 supervisor（companion 闲聊不需要监督）
5. **不**为桌宠引入新 Live2D 模型 / 新表情资源——本期接受表达力上限
6. **不**做 supervisor 用 LLM 自身的 fine-tune 或 RAG（用 prompt 工程足够）

## Decisions

### D1. Watchdog 触发模型：周期扫描 + 去重

**决定**：Supervisor Loop 是独立 asyncio.Task，每 60s 扫描一次所有活动 Code session；同 sid 在 12 分钟内不重扫。

**理由**：
- **周期扫描** vs **事件驱动**：事件驱动需要给每个 AgentEvent 加 hook，invasive；周期扫描只需读 session_activity 状态表。考虑 60s 粒度对 15min 阈值的检测精度足够（最多延迟 60s 报警）。
- **12 分钟去重**：避免 supervisor 在同一个 session 上反复消耗 token。比 15min 阈值短 3min，确保第二次唤醒时确实"又过了一个完整阈值周期"。

**备选**：
- **A. 纯事件驱动**（每个 event 检查阈值）— 性能没必要，且阈值是"无活动"，事件驱动反而难判
- **B. 用户主动呼救**（让用户点"我感觉它卡了"按钮）— 削弱"主动监督"价值

### D2. Supervisor 输入：结构化快照而不是原始 conversation

**决定**：supervisor LLM 拿到的不是完整对话，而是 `SessionSnapshot` 结构化对象（last_5_events / tool_signature_window / todos_state / context_token_pressure / user_goal[摘要]）。

**理由**：
- 完整 conversation 可能数千 token，supervisor 调用频繁（每分钟多次），成本失控
- supervisor 的判断维度本身就是"模式检测"——签名重复、长时间无活动、todo 停滞——结构化快照比原文更对应
- 可在不暴露完整对话给 supervisor 模型的前提下做出决策，**隐私边界更清晰**（用户的敏感对话不会重复送到云端）

**备选**：
- **A. 完整对话 + system prompt** — token 翻 10 倍，成本不可控
- **B. RAG 检索关键片段** — 引入新依赖（向量化、检索器），不值

### D3. SupervisorAction：4 种但初版只实现 3 种

**决定**：协议定义 `wait | nudge | ask_user | cancel`，初版只实现前 3 种；`cancel` 出现时强制降级为 `ask_user`。

**理由**：
- `cancel` 误判代价是"用户已做的工作丢失" — 不可逆，初版风险太高
- 留协议字段是为了未来不破坏 ws schema
- `ask_user` 已经能覆盖"supervisor 觉得该停"的场景——用户决定停不停

**备选**：
- 只支持 `wait/nudge` — 漏掉 permission 类场景的明显需求
- 全部支持 — `cancel` 实现后还要做撤销机制，scope 爆炸

### D4. nudge 注入：续命模式（B 方案）

**决定**：supervisor `action=nudge` 时，hint 入 `nudge_queue`，**不**打断当前 task。当前 task 通过 `add_done_callback` 在结束时检查队列，自动起 follow-up task，并在新 task 的 `_msgs` 头部注入 hint 作为 system message。

**理由**：
- 不动 `agent_loop.py` 内核（影响面最小）
- 利用现有 P4-S25 plan injection 的成例（system message 插入模式已成熟）
- 用户重打天然优先：done_callback 检查 `_chat_inflight[sid]` 还在跑就跳过，新 task 起来时再 pop hint 也能消化

**备选**：
- **A. 拦腰注入**（agent_loop 每 iter 检查 inbox）— 改动大，并发复杂
- **C. cancel + restart** — 丢工作，太粗暴

### D5. 重入安全：asyncio.Lock + 用户优先

**决定**：`nudge_queue` 用 `asyncio.Lock` 保护读写。三方并发场景规则：
- 用户消息永远优先：done_callback 起 follow-up 前必检查 `_chat_inflight[sid].done()`
- supervisor follow-up 起来后用户重打：现有 cancel-on-retry 路径直接 cancel，hint 还在队列里下一轮接着用
- 队列上限 cap=3：防 hint 堆积造成 LLM context 爆炸
- `code_mode_exit` 显式 `nudge_queue.clear(sid)`：退出 Code 模式时不残留 hint

**理由**：用户的明示意图永远高于 supervisor 的推断。

### D6. 桌宠状态机：5 状态 + 滞后阈值 + 最短驻留

**决定**：
- 5 状态：`idle / working / worried / alert / intervening`
- 滞后阈值：进入 worried 要求 score≥60，退出要求 score<50（10 点滞后带）
- 最短驻留：状态切换后必须停留 ≥10s 才允许再切（防抖动）

**理由**：纯阈值会导致 severity 在边界抖动让桌宠"抽搐"。10 点滞后 + 10s dwell 是工程经验值。

### D7. severity_score 公式

**决定**：

```
severity_score(s) = base_status_weight[s.status]
                  + age_penalty(s.last_activity_age)
                  + repeat_penalty(s.tool_signature_window)
                  + supervisor_severity_boost(s.last_supervisor_severity)
                  + iteration_pressure(s.current_iteration / s.max_iterations)
```

各项：
- `base_status_weight`: idle=0, running=10, permission=25, error=60
- `age_penalty`: `min(30.0, log2(max(1, age_seconds/60)) * 6)`
- `repeat_penalty`: `min(40.0, max_signature_count * 10)`
- `supervisor_severity_boost`: green=0, yellow=20, red=50
- `iteration_pressure`: `(cur/max) * 10`

**理由**：每项独立可解释，五项相加而非相乘——单一指标飙高足以触发警报，但需要多个维度同时异常才进 alert。具体常量在 S3 标定阶段调。

### D8. supervisor LLM 选型与成本

**决定**：默认走 OpenAI 兼容的便宜小模型（gpt-5-mini 类，单次 ~$0.001），settings 里可切换到主 LLM。30s 硬超时，失败回退 green。

**理由**：
- supervisor 单次 input 800 tokens + output 200 tokens，便宜模型完全够（不需要 deep reasoning，做模式识别即可）
- 串行扫描多 session（最多 2）→ 每分钟最多 2 次调用 → 每小时上限 ~10 次 → 月成本 < $1
- 失败回退 green：宁可漏报不要误报

### D9. 持久化：supervisor_hints 审计表

**决定**：SessionDB 新增 v14 迁移，建表 `supervisor_hints (id, sid, hint_text, action, severity, ts)`，每次 nudge 注入时写一行。

**理由**：用户事后回看可以理解"为什么主 agent 突然换了思路"。也是 supervisor 调试不可或缺的取证。

## Risks / Trade-offs

| Risk | Mitigation |
|------|------------|
| supervisor 自己卡住，拖垮主流程 | 30s 硬超时；asyncio.shield 保护 + 失败回退 green；扫描 loop 是独立 task，crash 不影响主 agent |
| supervisor 误判把"还在工作"当卡住 | 默认 action 是 `wait`；用户可关闭整个功能（settings toggle） |
| nudge 把主 agent 推向更糟方向 | cap=3 防堆积；二次唤醒升级 ask_user；hint 审计表方便事后核查 |
| Hiyori 表达力不足，"困惑"看不出 | 接受能力上限；气泡承担"具体在说什么"的语义；future enhancement 留给未来引入新 Live2D 模型 |
| severity 抖动桌宠抽搐 | 10 点滞后阈值 + 10s 最短驻留 + Hiyori 自带 0.5s motion fade-in/out |
| 多 session 评分误判（盯错最危险的） | 评分公式 5 维度叠加；S3 阶段需用真实 case 校准常量；用户始终可点桌宠气泡跳到任意 session |
| supervisor LLM 成本失控 | 12min 去重 + 串行扫描 + 便宜小模型默认；设月度成本告警阈值（settings 显示当前用量） |
| nudge 注入与用户重打竞争 | done_callback 必检 `_chat_inflight[sid].done()`；用户重打路径自动消化队列里的 hint |
| Code mode 退出时 hint 残留 | `code_mode_exit` IPC 显式 `nudge_queue.clear(sid)`；session_activity 也同步 drop |
| Hiyori motion 实际观感未校准 | S3 第一步做 debug UI，逐个播放 m01..m10 标注节奏感后再敲定 motion_pool |
| 与 P4-S25 plan injection 的 system message 冲突 | 同样的"插 system 到 stack 头部"模式；二者都标 `is_supervisor_hint` / `is_plan` 元数据，互不覆盖 |

## Migration Plan

### 部署顺序

1. **S1 上线**（活动追踪 + watchdog + 工具硬超时）
   - 兼容性：纯新增模块，不影响老路径
   - 验证：日志能看到每个 agent_event 的 activity bump；scan loop 在 backend 启动 30s 后开始
   - rollback：删 supervisor task 启动代码，session_activity 数据无害

2. **S2 上线**（supervisor LLM agent + supervisor_alert 事件）
   - 兼容性：前端不订阅 `supervisor_alert` 时静默丢弃
   - 验证：人为构造死循环 case，观察 SessionDB 写入 supervisor_hints
   - rollback：settings toggle off 让 supervisor loop 不调 LLM；或代码回退 S1

3. **S3 上线**（桌宠 UI 反馈）
   - 兼容性：前端独立模块，关掉 supervisor 时桌宠保持原行为
   - 验证：手工触发 supervisor_alert 事件，看 motion + 气泡是否切换
   - rollback：前端回滚不影响后端

4. **S4 上线**（nudge 注入回路）
   - 兼容性：nudge_queue 默认空，老路径完全不变
   - 验证：S2 已有的死循环 case，看主 agent 收到 hint 后是否切换思路
   - rollback：done_callback 注释掉，nudge_queue 写入但不消费

### 数据库迁移

- SessionDB v13 → v14：新表 `supervisor_hints`（无破坏性，老数据不动）
- 迁移脚本：`backend/deskpet/memory/migrations/003_p5s1_supervisor_hints.sql`
- 自动迁移在启动时跑（已有迁移框架）

### Feature Flag

- `config.toml` 新增 `[supervisor]` 段：`enabled=true`（默认开）、`scan_interval_seconds=60`、`stuck_threshold_seconds=900`、`llm_provider="default"`、`max_hints_per_session=3`
- settings UI 暴露 `enabled` toggle 和 `llm_provider` 切换

## Open Questions

| # | 问题 | 倾向 | 留给哪个阶段 |
|---|------|------|-------------|
| Q1 | supervisor LLM 用便宜小模型还是主模型？ | 便宜（gpt-5-mini 类） | S2 实现时可调 |
| Q2 | nudge 注入用 system 还是 user message？ | system + `is_supervisor_hint=True` 元数据 | S4 实现时定 |
| Q3 | 气泡按钮（继续/我看看）同步还是异步？ | 异步（点完气泡消失，supervisor 后台处理） | S3 实现时定 |
| Q4 | hint 持久化到 SessionDB 还是仅日志？ | 持久化（审计） | S2 已含表 |
| Q5 | 多 session supervisor 串行还是并行调用 LLM？ | 串行（避免 rate limit） | S2 默认串行 |
| Q6 | severity_score 各项常量怎么标定？ | S3 阶段用 ~10 个真实卡住 case 校准 | S3 必做 spike |
| Q7 | 需不需要给 supervisor 自己的 prompt 做 i18n？ | 中文 prompt + JSON output | S2 默认中文 |
| Q8 | "二次卡住升级 ask_user" 的判定边界？ | 同 sid 已收过 nudge 且距上次 < 30 分钟 → 升级 | S4 实现时定 |
