---
description: "按 plan 执行实施 — sp-executing-plans skill 包装"
allowed-tools: ["Skill", "Read"]
---

User just invoked `/sp:exec` with arguments: $ARGUMENTS

`$ARGUMENTS` should be the path to a plan markdown file. If empty, ask
the user for the plan path ONCE then proceed.

Invoke the `sp-executing-plans` skill via the Skill tool with the plan
path as initial context. Skill 会按 plan 步骤 + checkpoint 走完整执行
流程。
