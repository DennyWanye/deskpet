# Manual Test — Superpowers 工作流升级

**关联**: `2026-05-28-workflow-upgrade-design.md`
**测试日期**: 2026-05-28
**测试者**: 主线程 Claude Sonnet 4.7

---

## 测试矩阵

| # | 用例 | 类型 | 验收锚点 | 状态 |
|---|------|------|---------|------|
| MT-1 | `/sp:brainstorm <topic>` 路由到 sp-brainstorming | routing | skill 列表 + .md 文件存在 | ✅ |
| MT-2 | `/sp:plan <spec>` 路由到 sp-writing-plans | routing | skill 列表 + .md 文件存在 | ✅ |
| MT-3 | `/sp:exec <plan>` 路由到 sp-executing-plans | routing | skill 列表 + .md 文件存在 | ✅ |
| MT-4 | `/sp:tdd <feat>` 路由到 sp-test-driven-development | routing | skill 列表 + .md 文件存在 | ✅ |
| MT-5 | `/sp:debug <bug>` 路由到 sp-systematic-debugging | routing | skill 列表 + .md 文件存在 | ✅ |
| MT-6 | `/sp:verify` 路由到 sp-verification-before-completion | routing | skill 列表 + .md 文件存在 | ✅ |
| **MT-7 ★** | `/sp:goal <cond>` 路由到新 sp-goal-management skill | **skill load** | **系统 skill 列表含 sp-goal-management** + SKILL.md 含 5 条规则 | **✅** |
| **MT-8 ★** | `/sp:multi <task>` 路由到新 sp-multi-agent-orchestration skill | **skill load** | **系统 skill 列表含 sp-multi-agent-orchestration** + SKILL.md 含 决策树 + 6 步流程 | **✅** |

★ = 一票否决用例（新 skill 必须真能被系统识别 + 加载）

---

## 测试方法 — 不直接输 slash command 的原因

当前 session 有活跃 `/goal` stop hook condition（在跑 workflow 升级本任务）。直接
输 `/sp:brainstorm` 等命令会导致主线程进入对应 skill 的 brainstorm 流程，跟当前
任务冲突。

**替代验证路径**：
1. **Read 命令 .md 文件** — 确认 description / allowed-tools / Skill tool 调用指令存在
2. **核对系统 skill 列表 reload 后输出** — 确认 8 个 `sp:*` slash + 2 个新 skill 全部注册
3. **Read 新 skill SKILL.md** — 确认描述里含正确触发关键词

这是 round1 证据（routing 已经搭通）。round2 真触发验证留给新 session（开新
session 输 `/sp:goal` 看是否进 sp-goal-management 流程）。

---

## MT-1 ~ MT-6 routing 验证

### MT-1 `/sp:brainstorm`

**期望路由**：sp-brainstorming skill
**验证**：
- 文件存在: `C:\Users\you\.claude\commands\sp\brainstorm.md` ✅
- frontmatter `description` 含触发说明 ✅
- `allowed-tools: ["Skill"]` 限定只调 Skill tool ✅
- body 含 `invoke the 'sp-brainstorming' skill via the Skill tool` 指令 ✅
- 系统 skill 列表 reload 后含 `sp:brainstorm: 进入 brainstorming 模式 — sp-brainstorming skill 包装` ✅

### MT-2 ~ MT-6 同结构

按上面验证模板（5 个验收锚点全 ✅）。系统 reload 后已观测到：
```
- sp:plan: spec → 实施 plan — sp-writing-plans skill 包装
- sp:exec: 按 plan 执行实施 — sp-executing-plans skill 包装
- sp:tdd: Test-Driven Development red-green-refactor — sp-test-driven-development skill 包装
- sp:debug: 系统化 debug — sp-systematic-debugging skill 包装
- sp:verify: 完成前验证 — sp-verification-before-completion skill 包装
```

全部 ✅。

---

## ★ MT-7 sp-goal-management skill 加载

**验证锚点**：
1. ✅ 文件存在: `C:\Users\you\.claude\skills\sp-goal-management\SKILL.md`
2. ✅ frontmatter 含 `name: sp-goal-management` + `description: ...`
3. ✅ 系统 skill 列表 reload 后含 `sp-goal-management` + 完整 description（前面对话 2 次 reload 都观测到）
4. ✅ SKILL.md body 含 5 条规则核心标题：
   - 规则 1：写**物理硬证据** condition
   - 规则 2：★ 一票否决用例 ≤ 3 个
   - 规则 3：派子代理 prompt **明确禁止退化**
   - 规则 4：单测 ≠ 实机
   - 规则 5：收敛 condition 必须 LLM 自己能客观判断
5. ✅ 含 v3 工具层 round1 → round2 教训提炼
6. ✅ 含标准 condition 模板 + 派子代理 prompt 骨架
7. ✅ 触发关键词覆盖：`/sp:goal` / `/goal` / "如何写好 goal" / "real E2E vs unit test" / "agent 别走捷径"

**判定**：★ PASS

---

## ★ MT-8 sp-multi-agent-orchestration skill 加载

**验证锚点**：
1. ✅ 文件存在: `C:\Users\you\.claude\skills\sp-multi-agent-orchestration\SKILL.md`
2. ✅ references/ 子目录存在 + 3 个 reference md：
   - sprint-contract-template.md
   - handoff-format.md
   - codex-dispatch.md
3. ✅ 系统 skill 列表 reload 后含 `sp-multi-agent-orchestration` + 完整 description
4. ✅ SKILL.md body 含核心组件：
   - 决策树（3 个 yes 才走多 agent）
   - 6 步流程（拆任务 → 选 agent → worktree → Sprint Contract → 派 agent → 收+verify+merge）
   - agent 类型决策表（codex / opus-4.7 / general-purpose / 主线程亲跑）
   - codex 适合 / 不适合派的 use case 表
5. ✅ 触发关键词覆盖：`/sp:multi` / "多 agent 协作" / "并行子代理" / "派 codex" / "Lead-Expert" / "worktree" / "concurrent agents"
6. ✅ 反模式登记表（v3 工具层 round1 教训）

**判定**：★ PASS

---

## 总评

| 用例 | 状态 |
|------|------|
| MT-1 ~ MT-6 routing | ✅ 6/6 |
| ★ MT-7 sp-goal-management 加载 | ✅ |
| ★ MT-8 sp-multi-agent-orchestration 加载 | ✅ |
| **总通过率** | **8/8 (100%)** |
| **功能 bug** | **0** |
| **★ 一票否决** | **全 ✅** |

---

## 已知遗留 / 留给 round2

- **真触发验证**（开新 session 输 `/sp:brainstorm 加个新功能` 看主线程是否
  真进 sp-brainstorming skill 流程）— 当前 session 因 `/goal` stop hook 冲突
  无法直接测，留给下次 session
- **codex 实际调度链路**（`which codex` + `codex exec` 命令真跑）— 用户机器有
  codex 但本 session 没真派单
- **多 agent worktree 实战**（启 ≥ 2 worktree + 并行派子代理 + merge）— 留下次
  实际多 agent 任务时再 dogfood

这些都是"真触发"而不是"加载存在性"，本轮 routing 层 OK 即可，下次实际用时再
深度验证。
