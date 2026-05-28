---
description: "完成前验证 — sp-verification-before-completion skill 包装"
allowed-tools: ["Skill"]
---

User just invoked `/sp:verify` with arguments: $ARGUMENTS

Invoke the `sp-verification-before-completion` skill via the Skill tool.
它要求**真跑测试命令 + 看到 0 fail 输出**才能宣布完成，**禁止"基于
代码逻辑应该绿"就交付**。`$ARGUMENTS` 如果非空可以指定要验的范围
（"tools/v3 全套" / "memory_* 单测"等）。
