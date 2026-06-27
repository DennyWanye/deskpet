---
description: "系统化 debug — sp-systematic-debugging skill 包装"
allowed-tools: ["Skill"]
---

User just invoked `/sp:debug` with arguments: $ARGUMENTS

`$ARGUMENTS` is the bug / failure / unexpected behavior description.
Invoke the `sp-systematic-debugging` skill via the Skill tool. 它会先
复现 + 最小化 + 假设 + instrument + 修复 + 回归测试，**禁止"看起来
应该能跑"就交付**。
