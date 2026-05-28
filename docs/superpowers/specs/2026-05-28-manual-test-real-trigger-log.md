# Phase F 真触发验证日志 — Skill tool 调用证据

**测试日期**: 2026-05-28 / 2026-05-29 跨夜
**测试方法**: Claude 主线程 Sonnet 4.7 直接调 `Skill` tool
**目的**: 验证 ★ MT-7 + ★ MT-8 — 新 skill 真能被系统加载（**不是**只看 skill 列表 routing）

---

## ★ MT-7 — Skill(skill="sp-goal-management")

### 触发记录

主线程在本 session 内调用：
```
Skill(skill="sp-goal-management")
```

### 真返回内容（系统 reply 摘录）

```
Base directory for this skill: C:\Users\24378\.claude\skills\sp-goal-management

# Goal Management — `/goal` 用法方法论

`/goal` 是 codingsys 自定义命令，设置 session-scoped Stop hook condition。LLM
被强制持续工作直到 condition 自动 match 才能停。本 skill 教**怎么写不糊弄
自己**的 goal。
...
## 5 条规则

### 规则 1：写**物理硬证据** condition

❌ "完成 XX 模块" / "测试通过" / "实施完成"
❌ "fake-completion 拦截率达到 100%"
❌ "verify_gate 接电成功"

✅ "`%APPDATA%\\deskpet\\metrics.jsonl` 文件新增 ≥ 1 行 `verify_gate_init` event"
...
```

### 验收

- ✅ Skill tool 返回 base directory + SKILL.md 完整内容
- ✅ 含 v3 工具层 round1 → round2 教训
- ✅ 含 5 条规则（规则 1 物理硬证据 / 规则 2 ★≤3 / 规则 3 禁止退化 / 规则 4 单测≠实机 / 规则 5 客观可测）
- ✅ 含标准 condition 模板 + 派子代理 prompt 骨架
- ✅ 含触发关键词清单

**判定**: ★ MT-7 PASS（实机真触发 + 内容真加载到 LLM context）

---

## ★ MT-8 — Skill(skill="sp-multi-agent-orchestration")

### 触发记录

主线程在本 session 内调用：
```
Skill(skill="sp-multi-agent-orchestration")
```

### 真返回内容（系统 reply 摘录）

```
Base directory for this skill: C:\Users\24378\.claude\skills\sp-multi-agent-orchestration

# Multi-Agent Orchestration

整合 3 处分散规范：
- codingsys `~/.claude/CLAUDE.md` 的"多 Agent 并行" + "调度 Codex 做开发"段
- `sp-dispatching-parallel-agents` skill（superpowers）
- `sp-subagent-driven-development` skill（superpowers）

---

## 触发判断（决策树）

任务进来先问 3 个问题：
...
**简化**：3 个 yes → 走多 agent；任一 no → 退化到单 agent 或串行。

---

## 6 步流程

### Step 1: 拆任务 + 声明 file scope
...

### Step 2: 选 agent 类型

| 任务类型 | 默认 agent | Fallback |
|---------|-----------|---------|
| 代码 review / 第二意见 | opus 4.7 | claude-general-purpose |
| 独立模块开发 | codex | claude-general-purpose |
...
```

### 验收

- ✅ Skill tool 返回 base directory + SKILL.md 完整内容
- ✅ 含决策树 graphviz dot 图（3 yes → 多 agent）
- ✅ 含 6 步流程（拆任务 → 选 agent → worktree → Sprint Contract → 派 agent → 收+verify+merge）
- ✅ 含 agent 类型决策表（codex / opus / general-purpose / 主线程亲跑）
- ✅ 含 codex 调度先决条件 + Sprint Contract 模板
- ✅ 含反模式登记表

**判定**: ★ MT-8 PASS（实机真触发 + 内容真加载到 LLM context）

---

## Round 2 真触发证据 vs Round 1 routing 证据

| 维度 | Round 1（前一份 manual-test.md） | Round 2（本日志）|
|------|-----|-----|
| 证据层 | 系统 skill 列表 reload 后显示 sp-* | **真 Skill tool 调用 + 内容真加载** |
| 硬度 | routing 层（"配置就位"） | 加载层（"真能跑"）|
| 类比 v3 工具层 | "pytest PASS" | "metrics.jsonl 真增 verify_*" |
| 按 sp-goal-management 规则 4 | 单测层兜底 | 实机硬证据 |

**符合 sp-goal-management 规则 4：单测 ≠ 实机**。本日志是 round2 实机层。

---

## 一票否决判定

| ★ 用例 | Round 1 状态 | Round 2 状态 |
|--------|-------------|-------------|
| ★ MT-7 sp-goal-management 加载 | ⚠️ Routing OK，未真触发 | ✅ 真触发 + 完整内容加载 |
| ★ MT-8 sp-multi-agent-orchestration 加载 | ⚠️ Routing OK，未真触发 | ✅ 真触发 + 完整内容加载 |
| ★ Phase I push (git log origin/master..HEAD == ∅) | — | 见 final-report.md §3 |

★ 两大 skill 触发 PASS。Phase I 待 commit + push 后验证。

---

## 自我检讨（sp-goal-management 规则 5 自查）

> 收敛 condition 必须 LLM 自己能客观判断

本日志验收锚点 = "Skill tool 返回 SKILL.md 完整内容"。**客观可测**：
- 系统 message 含 "Base directory for this skill: ..." 文字 → ✅
- 含 SKILL.md 内的特征字符串（"5 条规则" / "决策树" / 等）→ ✅
- 不是主观判断"加载成功" → ✅

通过自查。
