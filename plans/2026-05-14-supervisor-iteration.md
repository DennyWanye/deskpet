# Supervisor 迭代计划（2026-05-14）

## 背景

经过 R1-R7 共 7 轮 live E2E 测试（10+ 小时累积运行 + 1000+ tool calls），
以及最近 chinzy 403 outage / agent self-destruction（kill 5173 端口）事件，
supervisor 的多个设计缺陷被暴露。本计划基于真实 log 证据系统梳理迭代方向，
不拍脑袋。

## 当前架构速览

```
watchdog (60s tick)
  ↓ 触发条件：idle > 900s 或 running > stuck_threshold
SupervisorAgent.diagnose(sid, snapshot)
  ↓ 单次 LLM 调用 (与主 agent 共用 provider chain)
  ↓ 输出 JSON {action, severity, diagnosis, hint, user_message, buttons}
_dispatch(action)
  ├─ audit → SessionDB.supervisor_hints
  ├─ nudge → nudge_queue.push (next chat 消费)
  └─ broadcast → ws supervisor_alert (UI 弹气泡)
```

- 代码：`backend/agent/supervisor.py` (522 行)
- action 类型：`wait | nudge | ask_user | cancel(coerced→ask_user)`
- **完全无状态**：每次 tick 从 snapshot 独立判断，不读历史 hints

## Log 暴露的 9 类问题

| # | 问题 | 真实证据 |
|---|---|---|
| 1 | **无记忆 → 死循环** | chinzy 403 outage 期间 supervisor 每 ~2 分钟重复触发，自己也连不上 LLM，但仍 23 次 auto_continue + 28 次 followup，无意义燃烧请求 |
| 2 | **hint 不累积** | LLM 调 unknown tool `directory_tree`，supervisor 发 hint，但 agent 下次仍可能犯同样错——hint 没注入到 agent 的长期记忆 |
| 3 | **不识别危险操作** | agent 用 `run_shell` kill 5173 端口（deskpet 自己的 vite），supervisor 无机制预审 → 整个 deskpet stack 崩溃，UI 显示"小说网站"网页 |
| 4 | **与主 agent 共用 provider chain** | chinzy 死时主 agent 死，supervisor 用同一 chain 也死 → 给用户的 hint 退化成 `supervisor_unavailable: ConnectError`，无新信息 |
| 5 | **反应式而非预测式** | watchdog 触发条件 `idle > 900s`，实际 agent 早 5 分钟就有 retry / hallucination 模式可识别，等了 15 分钟才介入太晚 |
| 6 | **user_message 空洞** | 典型："发现 session 卡住，要中断吗？" — 没说哪个 session、什么任务、上次进展、卡多久 |
| 7 | **action 词汇过粗** | 只 4 种 (wait/nudge/ask_user/cancel)；缺 `restart_task` / `narrow_scope` / `summarize_pause` / `handoff` 这些常用解题动作 |
| 8 | **auto-mode bypass 太激进** | 同 root cause 第 1 次就自动 continue，没有 escalation ladder。chinzy 403 时每次都被自动 continue，无意义死循环 |
| 9 | **audit trail 没被复用** | `supervisor_hints` 表写得很全（alert_id / hint_text / action / severity / diagnosis），但下次 diagnose 不读，丢失历史教训 |

## 迭代方向（按 ROI 排序）

### 🔥 高 ROI / 中成本（Sprint 1 优先）

#### A. 记忆与去重（解 #1 #8 #9）

**目标**：supervisor 不再"失忆症"，能识别"我已经问过同样问题 N 次"。

**设计**：
- `SupervisorAgent` 加 per-sid 滑动窗口：
  ```python
  self._recent_alerts: dict[str, deque[AlertRecord]] = defaultdict(
      lambda: deque(maxlen=20)
  )
  # AlertRecord: (timestamp, root_cause_hash, action, severity)
  ```
- `root_cause_hash = md5(diagnosis[:50] + last_error_type)`（粗哈希，类似错误算同一组）
- 在 `_dispatch` 入口、`_call_llm` 之前先查窗口：
  - 同 hash 在 cooldown_seconds (默认 300s) 内已发过 → 不再 broadcast/followup，只 audit 留痕
  - 同 hash 累计 ≥ 3 次（窗口内）→ 强制升级到 `escalation_action`：
    - 默认 → `ask_user` + buttons=["停止任务", "继续重试"]
    - auto_mode 下 → 不再 auto-continue，直接停在 ask_user 让用户看见
- 启动时从 SessionDB 加载该 sid 最近 24h 的 supervisor_hints（恢复窗口）

**ROI**：直接解决"chinzy 死时无限循环"和"反复弹同一气泡"两个用户最痛的体验。

**预估成本**：1 天（含单测）

#### B. user-facing 信号结构化（解 #6）

**目标**：用户看到 supervisor 气泡时一眼能判断"严重不严重 + 是什么 + 我要不要管"。

**设计**：
- 扩展 supervisor_alert payload schema：
  ```json
  {
    "session_id": "code-rkjdd9vo",
    "project_name": "小说网站",
    "current_task": "补全 BookDetailPage.jsx",
    "task_started_at": "2026-05-14T01:20:00Z",
    "last_progress_at": "2026-05-14T01:22:15Z",
    "attempts_count": 3,
    "supervisor_history_brief": [
      "2 分钟前提醒过：write_file 缺 path 字段",
      "5 分钟前 hint：分小段写不要一次 6KB"
    ],
    "diagnosis": "...",
    "severity": "yellow",
    "action": "ask_user",
    "recommended_buttons": [...]
  }
  ```
- supervisor 输出 schema 不变，main.py 的 `_broadcast_supervisor_alert`
  在发送前补全这些字段（从 SessionDB + cmm 拼接）。
- UI 端 SessionGridView 加专门的 supervisor alert 组件，按这些字段渲染。

**ROI**：用户体验立竿见影。空洞的"卡住了"变成"小说网站 BookDetailPage 写了 3 分钟，已重试 3 次 (写文件太长)"。

**预估成本**：0.5 天（后端 payload 扩展）+ 0.5 天（UI 组件）

#### C. action 词汇扩展（解 #7）

**目标**：supervisor 真正能解题，而不是只会"问用户"。

**新增 action 类型**：

| action | 语义 | 默认 hint 模板 |
|---|---|---|
| `restart_task` | 重置 todos + 重发原始 prompt | "把当前进度归零，从用户原始需求重新开始。已观察到的死结：{diagnosis}" |
| `narrow_scope` | 让 agent 把当前 todo 拆得更细 | "你正卡在 '{current_todo}'。把它拆成 3-5 个更小的步骤，每步独立可验证。" |
| `summarize_pause` | 暂停 + agent 输出现状总结 | "总结：(1) 已完成什么 (2) 当前卡在哪 (3) 你需要外部什么资源/决策才能继续。完后等用户回复，不再调任何工具。" |
| `handoff_provider` | 切换到 chain 下一个 provider | (no LLM hint, backend 自己切 provider 后用相同 prompt 重试) |

**实现**：
- supervisor.py 的 `SupervisorAction` 扩展 enum
- prompt 加描述告诉 LLM 这些选项
- main.py / agent_loop 对应 action handler

**ROI**：让 supervisor 从"巡夜员"升级成"项目经理"。

**预估成本**：1 天

### ⚡ 治本 / 高成本（Sprint 2）

#### D. 危险操作预审 hook（解 #3）

**目标**：agent 调用 `run_shell` / 大文件 `write_file` 等高危工具时，预先 sample 审查。

**设计**：
- `ToolRegistry.dispatch` 加可选 pre_check hook
- 默认 sampling 率：高危类别 (shell, write_file, edit_file, run_shell) 1/3 抽审
  + 100% 审：command 含 `kill|rm -rf|format|drop|del /f` 关键词
- 审查 supervisor 用轻量 prompt：仅判断"是否危险/自杀/数据丢失"，60 秒超时
- 危险 → 阻断 dispatch，给 agent 返回"危险被拦截"的 tool_result + suggested alternative
- 当前已实现的硬规则（`run_shell` 的 5173/8100 端口保护，commit `cc94a4f`）保留

**ROI**：直接防止 agent 自爆这类灾难。但 invasive，要小心 false positive 拖慢正常工作。

**预估成本**：2-3 天（含 sampling 策略调参 + 单测）

#### E. supervisor 独立 provider（解 #4）

**目标**：chinzy 死时 supervisor 不跟着死。

**设计**：
- config.toml 加 `[supervisor.provider]` 段，可配独立 base_url/model
- 默认 fallback 链：`[supervisor.provider]` → 主 chain[1] → 本地 Ollama → 规则系统
- 规则系统（zero-LLM）：纯字符串模式匹配 snapshot 给出最朴素 action：
  - last_error 含 "ConnectTimeout|403|all_providers_failed" → `ask_user` + "整条 LLM 链都断了，请检查 API key 余额"
  - same_tool_consecutive ≥ 5 → `nudge` + "你刚反复调 X 工具 5 次，换个方法"
  - idle > 30min → `ask_user` + "已 30 分钟无活动，要中断吗"

**ROI**：让 deskpet 在外部 outage 时仍能给出有意义的反馈。

**预估成本**：2 天（含规则系统）

### 💡 锦上添花（Sprint 3）

#### F. 早期信号检测（解 #5）

**目标**：从"15 分钟卡死后报警"升级到"5 分钟苗头就预警"。

**设计**：
- watchdog 维护 fine-grained metrics（rolling window）：
  - `tool_use_ratio_5min`（最近 5 分钟 tool_use turn 占比）
  - `same_tool_consecutive_max_5min`
  - `content_chars_volatility`（content_chars 标准差，跳跃说明在重写）
  - `iterations_per_minute`
- 当二阶导数异常（ratio 突降 / volatility 突增）→ "observation 中" 状态：
  - 频率提高到 20s 一观察，但不发 supervisor LLM 调用
  - 持续 3 次异常才升级到正式 supervisor diagnose
- avoid 误报：normal compile / large download 有 idle 期但不该报警

**ROI**：把"用户已经骂了"的时间从 15min 缩到 3-5min。

**预估成本**：2 天（含调参 + 单测）

#### G. hint 累积注入（解 #2）

**目标**：agent 自己学会回避反复犯的错。

**设计**：
- agent 每轮 system prompt 自动注入"过去 24h supervisor 给过的 N 条 hint 摘要"
- 摘要由 `supervisor_hints` 表 group_by(root_cause_hash) 生成：
  ```
  [过往教训]
  · 已 3 次因 unknown tool 'directory_tree' 失败 — 用 list_directory 替代
  · 已 2 次 write_file 太长被截断 — 限制 ≤3KB 或分多次
  ```
- 注入位置：ContextAssembler 在 PersonaComponent 之后插 LessonsComponent
- 上限：≤300 token，按发生次数和最近性排序

**ROI**：从 agent 端"内化" supervisor 经验，减少 supervisor 介入次数。

**预估成本**：1 天

#### H. 跨子系统事件 bus（解 #10）

**目标**：PermissionGate / TerminationGate / CircuitBreaker / supervisor 互通。

**设计**：
- 轻量内存 EventBus（asyncio.Queue 实现）
- 各子系统发事件：
  - PermissionGate: `permission_denied_streak(category, count)`
  - TerminationGate: `hard_cap_approaching(metric, used, limit)`
  - CircuitBreaker: `breaker_opened(tool_name)` / `breaker_closed(...)`
- supervisor 订阅这些 → 下次 diagnose 时这些 signals 注入 snapshot
- 同时也支持 supervisor 发事件给其他系统（e.g. 告诉 PermissionGate "请放宽 read_file"）

**ROI**：架构性提升，让各 layer 协同。但短期影响最弱。

**预估成本**：3 天（架构改造）

## 推荐的迭代顺序

### Sprint 1（1-2 天，立刻见效）：A + B + C

- A 记忆去重：止住 chinzy outage 时的死循环
- B 结构化信号：用户体验飞跃
- C action 词汇扩展：supervisor 真正能解题

**Sprint 1 完成后预期**：
- chinzy 突发死时不再循环烧请求；用户收到清晰报告"chain 死了请检查"
- supervisor 气泡含项目名/任务/历史，用户一眼看懂
- supervisor 能下"重启任务"/"缩小范围"/"切换 provider"等命令

### Sprint 2（4-5 天，治本）：E + D

- E 独立 supervisor provider：外部 outage 时仍能服务
- D 危险操作预审：杜绝 self-destruction 这类灾难

### Sprint 3（5-6 天，精修）：F + G + H

- F 早期信号
- G hint 累积
- H 跨系统 bus

## 不在本计划内的相关改进

为避免 scope creep，下面这些虽然相关但不属于"supervisor 迭代"范畴：

- 主 agent 的 self-check 机制（已存在 `p5s2_selfcheck_injected`，独立线）
- PermissionGate 的 deny pattern 配置 UX
- TerminationGate 的硬 cap 调参（已修：40→200）
- LLM provider 健康检查 ping（multi-provider-management followup）

## 验收标准（Sprint 1）

落地后跑一次"chinzy 模拟 outage"测试：
1. 启动 deskpet + 让两个 code session 跑
2. 用 firewall block chinzy.com（或换错的 API key）
3. 观察 10 分钟
4. **期望**：
   - 第 1 次 supervisor 触发后给用户**清晰错误信息**（chain 死，请检查）
   - 不再有 second/third/Nth 重复 alert（cooldown 生效）
   - 不再有 auto_followup 死循环
   - UI 显示具体的 session/task 信息

通过即 Sprint 1 验收。

## 修改文件清单（Sprint 1）

```
backend/agent/supervisor.py       (+ 记忆窗口 + cooldown + 新 action)
backend/agent/watchdog.py         (snapshot 加 hint 历史字段)
backend/main.py                   (broadcast payload 扩展 + 新 action handlers)
backend/tests/test_p5s1_supervisor.py  (新增 cooldown + escalation 测试)
backend/tests/test_supervisor_memory.py (新增 文件)
tauri-app/src/code-panel/SupervisorAlert.tsx (新增 UI 组件)
tauri-app/src/code-panel/ws.ts    (handle 扩展 payload)
```

预计代码量：+800 / -100 行（不含 tests），+300 行 tests。
