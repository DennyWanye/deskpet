# Code 模式工作流纪律 (superpowers)

> **面向**：DeskPet 用户 + 开发者
> **日期**：2026-06-02
> **背景**：用户反馈 Code 模式 auto 行为"奇奇怪怪"——问个问题它就埋头改代码、
> 不先澄清需求、不按计划、做完不验证就说完成。本次把 superpowers 的结构化工作流
> 纪律搬进 Code 模式：意图门 → 澄清 → 计划 → 执行 → 验证。
>
> 方案与决策见 [`../plans/2026-06-02-superpowers-code-workflow/proposal.md`](../plans/2026-06-02-superpowers-code-workflow/proposal.md)；
> 锁定实现 spec 见 [`../plans/2026-06-02-superpowers-code-workflow/05-LOCKED-spec.md`](../plans/2026-06-02-superpowers-code-workflow/05-LOCKED-spec.md)；
> 真机 E2E 证据见同目录 `evidence/E2E-report-*.md`。

---

## 1. 五阶段工作流纪律（一图看清）

```
收到消息
  │
  ├─ 意图门：这是提问/闲聊，还是派活？
  │     提问 ──→ 直接回答（不调工具、不改东西）
  │     派活 ↓
  │
  ├─ 澄清：需求清楚吗？（目标 / 范围 / 成功标准 / 重点文件）
  │     不清 ──→ 先问，别埋头猜
  │     清楚 ↓
  │
  ├─ 计划：todo_write 列计划
  │     plan_confirm_gate ON ──→ 暂停，等用户点 [执行] / [取消]
  │
  ├─ 执行：按计划做（独立子任务可 agent_parallel 并行）
  │
  └─ 验证：完成前自检（写完读回 / 跑测试）
        verify_gate strict ON ──→ 裸声明"做完了"但无工具 receipt → 拦截
```

这套纪律一半靠 **Code persona 措辞**（治"自觉"层），一半靠 **可翻开关**（硬门，
治"靠不住自觉"的场景）。出厂三个开关全关，行为与现状字节级一致。

---

## 2. 意图门 + 意图记忆

### 行为

- **意图门**：收到消息先判断是"提问/闲聊"还是"派活"。
  - 元信息提问（"你用的是什么模型？"、"解释一下这个概念"）→ **直接一句话回答，
    零工具调用、不改任何东西**。
  - 真派活 → 进入后续工作流。

  *真机证据*（[`E2E-report-layer1a.md`](../plans/2026-06-02-superpowers-code-workflow/evidence/E2E-report-layer1a.md) TC-1）：问"你用的是什么模型？"
  → 回 "我用的是 deepseek-v4-pro。" 全程 `tool_calls=0`，没乱改任何文件。

- **意图记忆**（决策1，依赖 `features.preference_memory`）：第一次澄清过的意图记下来，
  后续语义相似的纯提问免再澄清、直接答。一类请求第一次问清"你是想知道，还是想让我改？"
  → 用户答"只是想知道" → 记成 `ask` → 后续同类直接答；派活类记成 `task` → 注入
  "进工作流" 提示。

### 记忆怎么记 / 怎么匹配

偏好记忆组件 `backend/deskpet/agent/preference_memory.py` 用 **BGE-M3 语义嵌入**
做 cosine 相似度匹配（不是关键词精确匹配，泛化更好）：

- **匹配阈值 cosine ≥ 0.86**：相似度达标才命中，低于阈值正常走门（防误命中）。
- **两类记忆**：
  - `intent`（意图记忆）— `{请求 → ask / task}`，命中后注入对应 persona hint。
  - `plan`（计划记忆）— `{任务 → approved}`，命中后让 plan 硬门自动确认（见 §3）。
- **持久化**：写盘到 userdata 的 `preference_memory.json`，重启不丢；带去重 + 条数上限截断。

---

## 3. 计划硬门：plan-confirm [执行] / [取消]

### 行为（`features.plan_confirm_gate` ON）

非平凡任务，Code 模式会**先列计划并暂停**，在面板上渲染一栏：

```
📋 计划 (N 步) — 确认后执行
  1. ……
  2. ……
[▶ 执行]  [取消]
```

- 点 **[执行]** → 才开始跑 ReAct（调工具、改文件）。
- 点 **[取消]** → 彻底阻断，**不碰任何文件**。

设计上是「后台 task await 一个 Future」，**不阻塞** WebSocket 接收循环，因此无需重构
ReAct 主循环。

*真机证据*（[`E2E-report-plan-confirm-gate.md`](../plans/2026-06-02-superpowers-code-workflow/evidence/E2E-report-plan-confirm-gate.md)）：
- **GO 路径**：派任务 → 弹 [执行]/[取消] → 点击前**无任何 tool dispatch** → 点 [执行]
  后才 `todo_write → list_directory → write_file → read_file 读回`，产物创建成功。
- **CANCEL 路径**：点 [取消] 后**零 tool dispatch**，目标文件**未创建**。

### 计划记忆让相似任务免点

开启 `features.preference_memory` 后，第一次点 [执行] 批准的任务会被记进 `plan` 记忆。
后续**语义相似且以往批准过**的任务（如只改了文件名）会**自动确认、免点直接跑**。

*真机证据*（[`E2E-report-layer1b-preference-memory.md`](../plans/2026-06-02-superpowers-code-workflow/evidence/E2E-report-layer1b-preference-memory.md)）：
- Task A "创建 PREF_ALPHA.md…" → 走硬门 → 点 [执行] → 记入 `plan` 记忆。
- Task B "创建 PREF_BETA.md…"（只改文件名）→ BGE-M3 cosine **0.936** ≥ 0.86 →
  `plan_confirm_auto_approved`，**没弹按钮、没等确认、直接执行**。

### 边角：面板刷新（F5/HMR）

plan-confirm 状态在面板重载（F5 / HMR rehydration）后可恢复（awaiting plan 的
[执行]/[取消] 栏不会丢）。正常使用（发任务→立即确认）无影响。

---

## 4. 验证强度：verify strict

### 行为（`tools.verifier.verify_gate_mode`）

防"做完不验证就说完成"的 fake-completion。三档：

| mode | 行为 |
|------|------|
| `off`（出厂默认） | 不校验声明，完全放行。 |
| `shadow` | 校验但**永不拦截**，只记 WARN 日志 + metrics（用于观测真实 fake-completion 发生率）。 |
| `strict` | 真硬卡 — 声明"已创建 X / 测试通过"但工具 ledger 没有对应 receipt（没真调过 write_file / run_shell …）→ **拦截，不算完成**。 |

**strict 不会死循环**：拦截时回灌一条 "请真调工具" 的 system 消息并 continue；nudge
有上限（`max_verify_nudges`），耗尽后走 ephemeral 子代理救援再放行，最多多跑几轮。

**strict 不误杀真任务**：`registry.execute_tool` 对每个工具 dispatch 都 emit receipt，
真调过工具的任务 ledger 必有对应 receipt → claim 命中放行。只有零工具调用的裸声明会被拦。

*证据*（[`E2E-report-verify-strict.md`](../plans/2026-06-02-superpowers-code-workflow/evidence/E2E-report-verify-strict.md)）：
- 单测 31/31 PASS：有 receipt 放行 / 无 receipt 拦截 / "我将创建 X"（未来时）不误判 /
  shadow 出厂兜底永不拦。
- 真机：strict 激活（`patterns=9`）下创建真文件，**无 nudge**，任务正常完成（不误杀）。

### code 场景 claim patterns

`verify/claim_patterns.yaml` 已补 4 条 code 场景（5 → 9 条），覆盖：
已创建文件、已修改/更新文件、测试通过、英文 "I modified X"。

---

## 5. `/prefs` 用法（查看 / 清除偏好记忆）

偏好记忆可能记错（把"提问"记成"派活"），所以提供 `/prefs` slash 命令查看与清除：

| 命令 | 作用 |
|------|------|
| `/prefs` | 列出所有偏好记忆条目（每条显示 `kind / label: text`，末尾"共 N 条"）。 |
| `/prefs clear` | 清除全部偏好记忆。 |
| `/prefs clear intent` | 只清意图记忆。 |
| `/prefs clear plan` | 只清计划记忆。 |

> `features.preference_memory` 未开启时，`/prefs` 返回
> "偏好记忆未启用 (features.preference_memory)"。

清除结果会在面板回显"已清除 N 条"，方便确认。误记时可一句 `/prefs clear` 重置。

---

## 6. 如何开启（`config.toml`）

三个 flag **出厂默认全关**，未开启时 Code 模式行为与现状字节级一致。dev / 高级用户
可在 `config.toml` 翻开：

```toml
[features]
plan_confirm_gate = true     # 非平凡任务先出 plan 等用户点 [执行]
preference_memory = true      # 相似任务自动确认 / 纯提问免澄清（依赖 BGE-M3 embedder）

[tools.verifier]
verify_gate_mode = "strict"  # off | shadow | strict；先用 shadow 观察更稳
emit_receipts = true         # verify_gate_mode != "off" 的硬前提（config invariant 强制）
```

### 注意事项

- **invariant 强制**：`verify_gate_mode` 设为 `shadow` / `strict` 时**必须**同时
  `emit_receipts = true`，否则 ledger 永远为空、校验失效（启动校验会报错）。
- **preference_memory 依赖 BGE-M3 embedder**：需本地嵌入模型可用（`is_mock=False`）。
- **出厂默认是否翻 shadow 留待拍板**：倾向出厂 `shadow`（稳、可观测真实 fake-completion
  发生率），`strict` 仅 dev / 高级用户开。利弊详见
  [`../plans/2026-06-02-superpowers-code-workflow/05-LOCKED-spec.md`](../plans/2026-06-02-superpowers-code-workflow/05-LOCKED-spec.md) §6。
- **记忆误记防护**：匹配阈值 cosine ≥ 0.86（高置信度才命中）、记忆可查可清（`/prefs`），
  双保险防"记错一直跑错"。

---

## 7. 相关代码与文档

| 位置 | 说明 |
|------|------|
| `backend/deskpet/agent/assembler/components/persona.py` | Code persona — 五阶段工作流纪律措辞（Layer 1A） |
| `backend/deskpet/agent/preference_memory.py` | 偏好记忆组件（BGE-M3 cosine + JSON 持久化 + list/clear） |
| `backend/deskpet/commands/__init__.py` | slash 命令路由（含 `/prefs`） |
| `backend/config.py` | `FeaturesConfig` / `VerifierConfig` flag 定义 + invariant 校验 |
| `verify/claim_patterns.yaml` | verify_gate 声明匹配 pattern（含 4 条 code 场景） |
| [`../plans/2026-06-02-superpowers-code-workflow/`](../plans/2026-06-02-superpowers-code-workflow/) | 方案 / 锁定 spec / 真机 E2E 报告全集 |
