---
description: "多 agent 协作工作流 — sp-multi-agent-orchestration skill 包装"
allowed-tools: ["Skill"]
---

User just invoked `/sp:multi` with arguments: $ARGUMENTS

Invoke the `sp-multi-agent-orchestration` skill via the Skill tool. Skill
会按决策树判断是否真该走多 agent + 拆任务 + 选 agent 类型（codex / opus
review / Claude general-purpose / 主线程亲跑）+ git worktree 隔离 +
Sprint Contract + HANDOFF 格式 + verify-merge。

`$ARGUMENTS` 是总任务描述。Skill 会先问能不能拆 ≥ 2 独立子任务，
不能拆就建议走单 agent 路径。
