# Spec — Superpowers 工作流升级

**创建日期**: 2026-05-28
**作者**: Claude Sonnet 4.7 (主线程) 按 sp-brainstorming skill 流程
**状态**: ✅ 用户授权自决，已锁定 design

---

## 一句话

把 superpowers 14 个 sp-* skill 暴露 6 个 slash command 入口，新增 2 个 skill
（goal 管理 + 多 agent 编排）整合 codingsys CLAUDE.md 分散规范。

---

## 1. 背景

### 1.1 现状盘点（2026-05-28 实测）

| 资产 | 状态 |
|------|------|
| 14 个 sp-* skill | ✅ 已装（superpowers plugin enabled） |
| 旧 `/brainstorm` `/write-plan` 等 slash commands | ⚠️ Deprecated（README 写"用 Skill tool"），但**没替代品** |
| `/goal` codingsys 运行时 stop-hook 命令 | ✅ 已用（v3 工具层全程用过） |
| codingsys CLAUDE.md 多 agent 规范 | ✅ 已定义（Lead-Expert / Worktree / codex 调度） |
| 24+ 自定义子代理 (`~/.claude/agents/`) | ✅ 已装 |

### 1.2 痛点

1. **slash command 缺口**：旧 /brainstorm 已 deprecated → 用户改用 Skill tool，
   但 Skill tool 名字长 (`sp-brainstorming`)，无 `$ARGUMENTS` 透传，**记忆负担大**。
2. **/goal 是命令不是 skill**：方法论散在用户对话经验里。新 session / 别的项目
   不知道怎么写好 condition / 怎么定 ★ 一票否决用例 / 怎么收敛。
3. **多 agent 协作分散**：规范在 codingsys CLAUDE.md + sp-dispatching-parallel-agents
   + sp-subagent-driven-development 三处，**没有统一可触发入口**。codex 调度规则
   也只在 CLAUDE.md 注释里。

### 1.3 目标 (G) / 非目标 (NG)

**G1** 加 6 个 `/sp:*` slash command 包装核心 sp-* skill — 短名 + `$ARGUMENTS` 透传
**G2** 写 `sp-goal-management` skill 提炼 /goal 方法论（v3 工具层 round1 错→round2 修教训）
**G3** 写 `sp-multi-agent-orchestration` skill 整合 Lead-Expert + Worktree + codex 调度
**G4** 配 `/sp:goal` + `/sp:multi` slash command 入口
**G5** 写手测 testcase md + 实际跑一遍验证

**NG1** 不改 superpowers plugin 源码（不 fork）
**NG2** 不包装非核心 sp-* skill（finishing-branch / code-review / using-superpowers
       等 — 这些是被上面 6 个调用的底层，不暴露 /命令）
**NG3** 不写 unit test（.md 配置文件，靠手测）
**NG4** 不强制 codex 调度（检测 `which codex`，没装则 fallback Claude 自做）

---

## 2. 架构 — 三件互独立

```
~/.claude/
├── commands/
│   └── sp/                              ← 新增子目录
│       ├── brainstorm.md                ← /sp:brainstorm
│       ├── plan.md                      ← /sp:plan
│       ├── exec.md                      ← /sp:exec
│       ├── tdd.md                       ← /sp:tdd
│       ├── debug.md                     ← /sp:debug
│       ├── verify.md                    ← /sp:verify
│       ├── goal.md                      ← /sp:goal  (用 sp-goal-management)
│       └── multi.md                     ← /sp:multi (用 sp-multi-agent-orchestration)
└── skills/
    ├── sp-goal-management/              ← 新增 skill
    │   └── SKILL.md
    └── sp-multi-agent-orchestration/    ← 新增 skill
        ├── SKILL.md
        └── references/
            ├── sprint-contract-template.md
            ├── handoff-format.md
            └── codex-dispatch.md
```

---

## 3. Section 1 — `/sp:*` 8 个 slash command

### 3.1 命令清单（按 sp-brainstorming step "scaled to complexity"）

| 命令 | 触发 skill | 入参 `$ARGUMENTS` 含义 |
|------|-----------|----------------------|
| `/sp:brainstorm` | sp-brainstorming | 待 brainstorm 的 idea 文本（可省 → skill 自己问） |
| `/sp:plan` | sp-writing-plans | spec 路径（必填） |
| `/sp:exec` | sp-executing-plans | plan 路径（必填） |
| `/sp:tdd` | sp-test-driven-development | 要 TDD 的 feature 描述 |
| `/sp:debug` | sp-systematic-debugging | bug 描述 |
| `/sp:verify` | sp-verification-before-completion | （空 — 验当前对话所写代码） |
| `/sp:goal` | sp-goal-management（新） | goal condition 文本 |
| `/sp:multi` | sp-multi-agent-orchestration（新） | 总任务文本 |

### 3.2 命令 .md 文件模板（每个都长这样）

```yaml
---
description: "<一句话用途>"
allowed-tools: ["Skill"]
---

User just invoked `/sp:<name>` with arguments: $ARGUMENTS

Your job: invoke the `<skill-name>` skill via the Skill tool, passing
`$ARGUMENTS` (if non-empty) as initial context. Do NOT do the work
yourself — let the skill drive.

If $ARGUMENTS is empty AND the skill requires input (e.g. /sp:plan needs
spec path), ask the user for it ONCE then invoke the skill.
```

### 3.3 设计权衡

- ✅ `sp/` **子目录**形式（不是 `sp-*` 平铺）— Claude Code 支持嵌套 slash
  commands，`/sp:plan` 这种 namespacing 比 `/sp-plan` 更清晰
- ✅ 每个 .md ≤ 20 行 — 真正的 skill 内容在 Skill tool 加载的 SKILL.md，命令
  文件只做路由
- ✅ `allowed-tools: ["Skill"]` 限制命令文件只能调 Skill tool，防 LLM 走神

---

## 4. Section 2 — `sp-goal-management` skill

### 4.1 SKILL.md 关键章节

```markdown
---
name: sp-goal-management
description: 用 /goal codingsys 命令做长期目标管理 — 写 condition / 定 ★
  一票否决 / 收敛硬证据 / 真实机验证 vs 单测兜底的区别. Use when user
  invokes /sp:goal or types "/goal " or asks "how do I set a goal that
  doesn't fool itself".
---

# Goal Management — /goal 用法方法论

## 起源教训（v3 工具层 round1→round2）

Round 1 主代理派 opus 4.7 子代理跑 /goal 验收，子代理报"GO ship"但
**没碰 metrics.jsonl 一行**（全 pytest + grep 当 E2E 证据）。Round 2
主线程亲跑 windows-mcp 实机暴露了 2 个真接电缺口（VALID_EVENTS 白名单
+ agent_loop metric record 漏调）。

教训：goal condition 写"通过 / 实施完成"会鼓励 LLM 走捷径。必须写**物理
硬证据**（metrics.jsonl 真增 1 行 / 截图存盘 / 文件大小变化）。

## 5 条规则

1. **写硬证据 condition**：
   ❌ "完成 XX 模块" / "测试通过"
   ✅ "metrics.jsonl 真出现 verify_gate_init event"
   ✅ "windows-mcp Screenshot + 真坐标 click + 截图存盘"

2. **★ 一票否决用例 ≤ 3 个**：
   测试清单可以长，但必须挑 ≤ 3 个★ 真实机硬证据 + bug=0 作为收敛条件。

3. **派子代理跑测试时 prompt 必须**：
   - 明确禁止退化（"绝不允许 grep 源码当接电证据"）
   - 给具体工具加载指令（"ToolSearch query=windows-mcp max_results=30"）
   - 给操作步骤 + 验收锚点（"PowerShell tail metrics.jsonl"）

4. **单测 ≠ 实机**：
   单测 mock metrics_sink 看不到 VALID_EVENTS 隐私墙；mock 子进程看不到
   真 OS 焦点切换。E2E 必须真启 backend / 真 Tauri / 真 LLM 或至少真
   metrics_sink 走真 IO 路径。

5. **收敛条件具体可测**：
   "/goal" 的 stop hook 自动 clear 是按 condition match。所以 condition
   必须是 LLM 自己能客观判断的（"metrics.jsonl 含 verify_* line"），
   不是主观判断（"质量足够"）。

## 标准 condition 模板

> "完成 <范围>；★ <测试 1> + <测试 2> + <测试 3> 三大用例全 ✅ 且功能
> bug=0 才算收敛。绝不允许 <禁止退化方式>，必须有 <硬证据来源> 才算
> <WI 名> 完成。"

(完整方法论见 references/...)
```

### 4.2 调用方式

- 通过 `/sp:goal <condition>` 直接调
- 或 user 输入 "/goal" 时 Skill tool 自动 match

---

## 5. Section 3 — `sp-multi-agent-orchestration` skill

### 5.1 SKILL.md 关键章节

```markdown
---
name: sp-multi-agent-orchestration
description: 多 agent 协作工作流 — Lead-Expert 模式 + git worktree 隔离
  + Sprint Contract + HANDOFF + codex 调度. Use when user asks for
  parallel agent work, multi-module simultaneous dev, "多 agent" /
  "并行子代理" / "派 codex" / invokes /sp:multi.
---

# Multi-Agent Orchestration

## 适用判断（决策树）

任务进来先问：
  1. 能拆 ≥ 2 独立子任务吗？(否 → 自己做)
  2. 子任务能并行无 race condition 吗？(否 → 串行 sp-subagent-driven)
  3. 改动 ≥ 3 文件 / 跨模块吗？(否 → 单 agent 即可)
  → 全是 yes → 走本 skill

## 步骤

### Step 1: 拆任务
- 列子任务 + 显式 file scope（哪些文件 / 不准动哪些）
- 列依赖图（A→B 串 / A‖B 并）

### Step 2: 选 agent
- 复杂代码改 / 跨模块 → 默认 codex（先 `which codex`，无则 fallback
  general-purpose Claude）
- 代码 review / 第二意见 → opus 4.7
- UI 操作 / windows-mcp → 主线程亲跑（子代理一般无 mcp 权限）
- 纯逻辑 / 测试生成 → general-purpose Claude / codex 任一

### Step 3: 创 worktree（默认）
```bash
git worktree add ../<task>-worktree -b feat/<task>
```

### Step 4: 写 Sprint Contract（references/sprint-contract-template.md）
- 输入：spec / plan 路径
- 输出：哪些文件 / 哪些测试必绿
- 边界：不准动 X / Y / Z

### Step 5: 派 agent + run_in_background（如果是 Claude 子代理）
- 并行子任务 → 单 message 内多 Agent tool call
- codex → bash exec + status 文件

### Step 6: 收 + verify + merge
- 必跑：sp-verification-before-completion
- 不盲信子代理报告 — 主线程二次验证
- merge 用 fast-forward / squash

## codex 调度（详 references/codex-dispatch.md）

适合派 codex：
- 代码 review / 第二意见
- 独立可并行模块开发
- 大段重构 / rename / 类型补全
- 生成单测

不适合：
- 需要大量对话上下文的小修改
- 探索性调试
- UI 微调（需立即看效果）
```

---

## 6. 验收 — 手测 testcase

写 `docs/superpowers/specs/2026-05-28-workflow-upgrade-manual-test.md`
含 8 条用例：

| # | 用例 | 验收 |
|---|------|------|
| MT-1 | session 输入 `/sp:brainstorm 加个新功能` | Skill tool 真触发 sp-brainstorming + 含 $ARGUMENTS 文本 |
| MT-2 | `/sp:plan docs/.../spec.md` | sp-writing-plans 真触发 + 读 spec 路径 |
| MT-3 | `/sp:exec docs/.../plan.md` | sp-executing-plans 真触发 |
| MT-4 | `/sp:tdd 实现 X` | sp-test-driven-development 真触发 |
| MT-5 | `/sp:debug 这个 bug` | sp-systematic-debugging 真触发 |
| MT-6 | `/sp:verify` | sp-verification-before-completion 真触发 |
| MT-7 ★ | `/sp:goal 收敛条件...` | sp-goal-management skill 真加载 + 输出 5 条规则 |
| MT-8 ★ | `/sp:multi 并行任务...` | sp-multi-agent-orchestration 真加载 + 输出决策树 + 6 步流程 |

★ = 一票否决（新 skill 必须真能加载）

---

## 7. 排期

| 阶段 | 内容 | 预估 |
|------|------|------|
| Phase A | 写 8 个 slash command .md 文件（每个 ≤ 20 行） | 15 min |
| Phase B | 写 sp-goal-management/SKILL.md | 20 min |
| Phase C | 写 sp-multi-agent-orchestration/SKILL.md + references/ | 30 min |
| Phase D | 写 manual test md + 跑 8 条用例 | 20 min |
| Phase E | 修测试发现的问题 | 15 min |
| Phase F | commit + push + 终态报告 | 10 min |

**总计**：~ 110 min。可并行 Phase B / C（派子代理）。

---

## 8. 风险登记

| # | 风险 | 缓解 |
|---|------|------|
| R1 | Claude Code 不支持 `sp/` 子目录 slash command | 用 `sp-` 前缀平铺替代 |
| R2 | $ARGUMENTS 透传失败 | 在 .md 里加 `{{args}}` placeholder fallback |
| R3 | 新 skill description 写不好 → Claude 不自动 match | 多用触发关键词，参考 sp-brainstorming description 格式 |
| R4 | sp-goal-management 跟 codingsys /goal 命令冲突 | 明确 skill 是方法论文档，/goal 是运行时命令，不互相替代 |

---

## 9. spec self-review

✅ 无 TODO / TBD 占位
✅ 三个 section 之间一致（命令路由 → skill）
✅ scope 聚焦（不动 plugin 源码 / 不写 unit test）
✅ 无歧义（每个命令 / skill 都有明确路径 + 入参）

---

## 10. 终态报告承诺

按用户 goal "完成后给我一份报告，用简单易懂的方式说明，你计划做了什么，
完成了什么，还有什么未完成的"，最后输出 5-section 报告：

1. 计划做什么（本 spec 总结）
2. 实际做了什么（commits + files 列表）
3. 测试结果（8 条 MT 用例真跑）
4. 未完成 / deferred
5. 怎么用（用户 cheat sheet）
