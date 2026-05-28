# Codex 调度详解

按 codingsys `~/.claude/CLAUDE.md` "调度 Codex（GPT-5.4）做开发" 段实施。
用户 2026-04-26 显式授权默认偏好 — 能用 codex 的任务主动派，不用每次问。

---

## 前置检查

```bash
which codex
codex --version
```

未安装 → 不报错，fallback claude-general-purpose 子代理，handoff 里写明用了哪个。

---

## 适合派 codex（按 codingsys CLAUDE.md）

✅ **代码审查**（rc / 大改动 commit 前的二次意见）— GPT-5.4 视角不一样
✅ **独立可并行的模块开发**（前后端 / 多语言 / 多 slice）— Claude 写一边，codex 写另一边
✅ **大段重构** / 跨多文件机械改动（rename / API 迁移 / 类型补全）
✅ **生成单测**（针对已有 production 代码的测试覆盖补齐）
✅ **算法实现** / 纯逻辑函数（无需大量项目上下文的部分）

## 不适合派 codex（自己做更快）

❌ 需要大量对话上下文 / 当下决策连贯的小修改
❌ 探索性调试（写一段 → 跑测试 → 再写）— 派出去打包上下文反而慢
❌ 需要立即看到效果再调整的 UI 微调

---

## 派单脚本（参考实现）

```bash
#!/bin/bash
# 参考 ~/.claude/skill-repos/everything-claude-code/scripts/orchestrate-codex-worker.sh

set -euo pipefail

TASK_ID="$(date +%s)"
TASK_FILE="/tmp/codex-task-${TASK_ID}.md"
HANDOFF_FILE="/tmp/codex-handoff-${TASK_ID}.md"
STATUS_FILE="/tmp/codex-status-${TASK_ID}.json"
WORKDIR="${1:?Missing worktree path}"
TASK_CONTENT="${2:?Missing task content}"

# 1. 写 task 文件
cat > "$TASK_FILE" <<EOF
$TASK_CONTENT
EOF

# 2. 派 codex（前台同步执行）
codex exec \
  --workdir "$WORKDIR" \
  --task "$TASK_FILE" \
  --output "$HANDOFF_FILE" \
  --status "$STATUS_FILE"

# 3. 回报
echo "task_id=$TASK_ID"
echo "handoff=$HANDOFF_FILE"
echo "status=$STATUS_FILE"
```

并发跑多个 codex：用 bash `&` 或 Claude Code 的 `run_in_background: true`。

---

## 派出后纪律（CLAUDE.md 原话）

> - 给清楚的 Sprint Contract（输入 / 输出 / 验收标准 / 不准动什么）
> - codex 返回后 **必跑** 自己的测试 + 类型检查再 merge — 不盲信
> - 重大改动 codex 写 + Claude review；或反过来 Claude 写 + codex review

---

## codex vs Claude 子代理对比

| 维度 | codex (GPT-5.4) | claude-general-purpose | opus 4.7 |
|------|----------------|----------------------|---------|
| 视角 | 不一样（防同源偏见） | 同源（可能继承偏见） | 同源但更深推理 |
| 上下文窗口 | GPT-5.4 ~ 1M | claude-sonnet-4.7 200K-1M | opus 4.7 1M |
| 速度 | 中（CLI 启动有开销） | 快（tool 直接） | 慢（思考重） |
| 并发 | 通过 bash & | run_in_background | run_in_background |
| 适合 | 重构 / 单测 / review | 通用 / 集成 | 架构 review / 决策 |
| 成本 | $$ | $ | $$$ |

---

## 反模式

| 错误 | 修法 |
|------|------|
| codex 不在就报错退出 | 检测 + fallback claude-general-purpose，handoff 写明 |
| 派 codex 没给 worktree | 跟主目录共用 → 改动冲突 |
| codex 返回直接 merge 不 review | 主线程必跑 verify + skim diff |
| codex prompt 不写边界 | "改完整个项目" → codex 真会乱改；必须 Sprint Contract |
| codex stalled 不报 | 设时间预算 + status 文件 polling |
