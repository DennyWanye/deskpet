# 业界最佳实践调研 — Claude Code 多 agent / superpowers / git worktree

**调研日期**: 2026-05-28
**方法**: WebSearch 3 个独立 query + 官方源 + 社区资源
**用途**: 验证 `2026-05-28-workflow-upgrade-design.md` 设计与业界 2026 对齐

---

## 一句话

2026 业界共识：**多 agent + worktree 隔离已成默认协调层**（4+ 并发 session 时），
Anthropic 官方推 3-tier 架构（subagents → worktrees → cloud VMs）；superpowers
plugin 已被收进 Anthropic 官方 marketplace（2026-01-15），证明本次升级方向对齐。

---

## 1. Claude Code 多 agent 协作（2026 业界）

### 1.1 Anthropic 官方 3-Tier 架构

来自 [Anthropic Claude Code Docs — Agent Teams](https://code.claude.com/docs/en/agent-teams):

| Tier | 形态 | 适用场景 |
|------|------|---------|
| **Tier 1** | 单 terminal session 内的 Subagents + Agent Teams | 起步默认；无额外工具 |
| **Tier 2** | 多 worktree 隔离 + 本地 dashboard / diff review / merge | **3-10 agents** + 已知 codebase（**本次设计的目标层**） |
| **Tier 3** | Agents 跑在 cloud VMs，无 terminal | 大规模 / 远端 |

**结论**：本次设计的 `sp-multi-agent-orchestration` skill 默认 worktree 隔离，**完全对齐 Tier 2**。

### 1.2 Subagents vs Agent Teams 区别

来自 [Shipyard.build](https://shipyard.build/blog/claude-code-multi-agent/) +
[Developers Digest 2026 Playbook](https://www.developersdigest.tech/blog/claude-code-agent-teams-subagents-2026):

| 维度 | Subagents | Agent Teams |
|------|----------|------------|
| 用途 | 快速、专注的 worker，报告后退场 | 队友间共享 findings、相互挑战、自主协调 |
| 协调原语 | 无 | 共享 task list + dependency / peer messaging / file locking |
| 适用 | 任务自然分专业（backend/frontend/test/review） | 复杂多步、需要协商 |

**本次设计映射**：`sp-multi-agent-orchestration` 的"Sprint Contract"是
**手动版的 Agent Teams 协调原语**（task 文件 + handoff + status JSON 三件套）。
业界用 Anthropic 官方 Agent Teams API 实现这层；我们这里用文件约定实现，避免
依赖最新 API。

### 1.3 成本优化

来自 [CloudZero 2026 Cost Analysis](https://www.cloudzero.com/blog/claude-code-agents/):

> Model selection is the biggest cost lever — an agent team with **one Opus 4.7
> orchestrator and four Sonnet 4.6 workers** costs roughly **40% less than five
> Opus agents**.

**本次设计映射**：`sp-multi-agent-orchestration` Step 2 选 agent 表已经按此
原则 — 主线程（lead） + codex/general-purpose 子代理（workers）+ opus 4.7
仅用 review。**自然对齐成本最优结构**。

### 1.4 2026 已知局限（业界共识）

> In 2026, Claude Code multi-agent works well for **solo developer workflows**,
> but it is less mature for **team handoff** with no built-in cross-session
> subagent memory, limited first-class observability, and uneven output
> determinism.

**对我们的影响**：
- ✅ DeskPet 当前是 solo dev workflow — 本次升级覆盖的场景
- ⚠️ 跨 session 子代理记忆 — 用 handoff.md 文件兜底（已在 references/handoff-format.md 规范）
- ⚠️ Observability — 用 status.json 机器可读 + 主线程二次 verify 兜底
- ⚠️ Determinism — Sprint Contract 写明 "不允许 我尽量 / 差不多" 拉齐预期

---

## 2. Git Worktree 并行 AI Agent（2026 业界）

### 2.1 业界共识

来自 [MindStudio Worktrees Guide](https://www.mindstudio.ai/blog/git-worktrees-parallel-ai-coding-agents)
+ [Zylos Research](https://zylos.ai/research/2026-02-22-git-worktree-parallel-ai-development):

> This pattern is **now natively supported by Claude Code, OpenAI Codex, and
> Cursor**, and has become the **default coordination layer for teams running
> four or more concurrent AI sessions**.

> JetBrains IDEs shipped first-class Git worktree support in the **2026.1
> release (March 2026)**. VS Code added worktree support in **July 2025**.

**结论**：worktree 已经是 2026 业界标准默认。本次设计选 worktree 隔离 = 跟主流对齐。

### 2.2 配套工具生态

来自 [Augment Code Guide](https://www.augmentcode.com/guides/git-worktrees-parallel-ai-agent-execution)
+ [nekocode/agent-worktree GitHub](https://github.com/nekocode/agent-worktree):

- **agentree** — 快速 worktree 创建 CLI
- **git-worktree-runner**（CodeRabbit 出品）— 跨 AI 平台
- **worktree-cli** — 含 MCP server 集成 Claude Code

**对我们**：DeskPet 已有 `scripts/dev-worktree.ps1` 注入 `DESKPET_BACKEND_PORT`
解决端口冲突 — 自家工具但解决的是业界同一问题。`sp-multi-agent-orchestration`
SKILL.md Step 3 已经引用了它。

### 2.3 探索性并行（多解最优）

> Multiple agents can solve the same problem in parallel with the best result
> selected, which is particularly useful for tasks with ambiguous design
> constraints where exploring multiple directions simultaneously saves
> wall-clock time.

**留待 future**：本次未设计"多解竞争 + 选最优"，但 references/sprint-contract-template.md
的 Sprint Contract 形态可扩展支持。

---

## 3. Superpowers Plugin 现状（2026）

### 3.1 官方化里程碑

来自 [Anthropic 官方 Plugin 页](https://claude.com/plugins/superpowers)
+ [Pasquale Pillitteri Complete Guide 2026](https://pasqualepillitteri.it/en/news/215/superpowers-claude-code-complete-guide):

> Since **January 15, 2026**, Superpowers has been accepted into the
> **official Anthropic Claude Code plugin marketplace**, which confirms its
> quality and reliability. It's an open-source project created by Jesse
> Vincent (obra) and the community.
>
> The latest version is **5.1.0**, released **May 4, 2026**.

**对我们**：用户 `~/.claude/settings.json` 已 `"superpowers@claude-plugins-official": true`
— 装的就是官方版。本次升级建立在官方版本之上，符合主流。

### 3.2 Slash Command 命名约定

来自 [Claude Directory Skills Doc](https://www.claudedirectory.org/skills/superpowers)
+ [MindStudio Use Guide](https://www.mindstudio.ai/blog/how-to-use-superpowers-plugin-claude-code):

> You can invoke skills with slash commands like **/brainstorming** to explore
> requirements and design before implementation, or **/execute-plan** to run
> batched implementation plans with review checkpoints.
>
> The plugin includes these main slash commands:
> - **/superpowers:brainstorm** to capture task context
> - **/superpowers:write-plan** to draft a plan doc
> - **/superpowers:execute-plan** to execute the plan in a subagent

**关键观察**：官方用 `/superpowers:brainstorm` 这种 `<plugin>:<command>`
namespacing。我们用 `/sp:brainstorm` — **更短 + 同样清晰**，没冲突。

### 3.3 已知问题 — deprecated 命令困扰用户

来自 [GitHub Issue #756](https://github.com/obra/superpowers/issues/756):

> New install includes deprecated slash commands that confuse users.

**对应到我们看到的**：`/brainstorm` `/write-plan` `/execute-plan` 都标 deprecated
但还在用户机器里 — 这是已知社区问题。**本次设计的 `/sp:*` 命令是替代方案**，
跟社区方向一致。

### 3.4 工作流推荐

来自 [st0012.dev 工作流文章](https://st0012.dev/links/2026-01-15-a-claude-code-workflow-with-the-superpowers-plugin/):

业界推荐流程：**Brainstorm → Spec → Plan → Subagent-driven Execute → Verify**

**本次设计映射**：8 个 /sp:* 命令完整覆盖此流程：
- `/sp:brainstorm` → spec
- `/sp:plan` → plan
- `/sp:exec` → execute
- `/sp:tdd` + `/sp:debug` → 实施 + 修
- `/sp:verify` → 收尾

---

## 4. 跟本次设计的对账

| 业界共识 | 本次设计是否对齐 |
|---------|----------------|
| Tier 2 worktree 隔离 (3-10 agents) | ✅ sp-multi-agent-orchestration Step 3 默认 worktree |
| 主线程 lead + worker 子代理结构（成本最优） | ✅ Step 2 agent 选择表 + 主线程 verify-merge |
| Subagents 分 backend/frontend/test/review | ✅ codingsys CLAUDE.md "代码 review / 模块开发 / 重构" 分工 |
| File locking 防 race | ✅ Step 1 file scope "不准动" 显式约定（无 LSP 锁就用规则约束）|
| 子代理无 cross-session memory → 用 handoff 文件 | ✅ references/handoff-format.md 三件套规范 |
| `/<plugin>:<cmd>` namespace 命名 | ✅ 用 `/sp:*` 短形式（功能等价）|
| Brainstorm→Plan→Exec→Verify 流程 | ✅ 6 个核心 slash command 全覆盖 |

**没对齐的**（本次未做，留 future）：
- ❌ Anthropic 官方 Agent Teams API（peer messaging / 真 file lock）— 用文件
  约定兜底，依赖更稳但功能弱
- ❌ "多解竞争 + 选最优" 探索性并行模式 — 当前都是分工式
- ❌ Cloud VMs (Tier 3) — 不在 solo dev 范围

---

## 5. Sources

### Multi-agent orchestration
- [Anthropic Claude Code Docs — Agent Teams](https://code.claude.com/docs/en/agent-teams)
- [Shipyard — Multi-agent orchestration for Claude Code in 2026](https://shipyard.build/blog/claude-code-multi-agent/)
- [AddyOsmani — The Code Agent Orchestra](https://addyosmani.com/blog/code-agent-orchestra/)
- [CloudZero — Claude Code Agents In 2026: Cost Analysis](https://www.cloudzero.com/blog/claude-code-agents/)
- [Anthropic API Docs — Multiagent Sessions](https://platform.claude.com/docs/en/managed-agents/multi-agent)
- [Developers Digest — Agent Teams 2026 Playbook](https://www.developersdigest.tech/blog/claude-code-agent-teams-subagents-2026)
- [Claudio Novaglio — Agent Teams Complete Guide](https://www.claudio-novaglio.com/en/papers/agent-teams-claude-code-multi-agent-orchestration)

### Git worktree
- [MindStudio — Git Worktrees Parallel AI Coding](https://www.mindstudio.ai/blog/git-worktrees-parallel-ai-coding-agents)
- [Zylos Research — Worktree Isolation Patterns](https://zylos.ai/research/2026-02-22-git-worktree-parallel-ai-development)
- [Augment Code — Worktree Parallel AI Guide](https://www.augmentcode.com/guides/git-worktrees-parallel-ai-agent-execution)
- [nekocode/agent-worktree GitHub](https://github.com/nekocode/agent-worktree)
- [Upsun — Worktrees for AI Agents](https://developer.upsun.com/posts/ai/git-worktrees-for-parallel-ai-coding-agents)
- [MindStudio — Parallel Agentic Development Playbook](https://www.mindstudio.ai/blog/parallel-agentic-development-git-worktrees)

### Superpowers
- [Anthropic Plugins — Superpowers](https://claude.com/plugins/superpowers)
- [Claude Directory — Superpowers Skill](https://www.claudedirectory.org/skills/superpowers)
- [Pasquale Pillitteri — Superpowers Complete Guide 2026](https://pasqualepillitteri.it/en/news/215/superpowers-claude-code-complete-guide)
- [GitHub Issue #756 — Deprecated commands confuse users](https://github.com/obra/superpowers/issues/756)
- [MindStudio — How to Use Superpowers](https://www.mindstudio.ai/blog/how-to-use-superpowers-plugin-claude-code)
- [st0012.dev — Superpowers workflow](https://st0012.dev/links/2026-01-15-a-claude-code-workflow-with-the-superpowers-plugin/)
