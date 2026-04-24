---
name: recall-yesterday
description: 回忆昨天聊过的亮点（调用 memory_recall 查询昨日会话）
version: 0.1.0
author: deskpet
task_types: [recall, chat]
requires_script: false
---
请帮我回忆昨天（相对当前时间的前一天）我们聊过的内容。

步骤：
1. 先调用 `memory_recall` 工具，参数 `query` 使用"昨天我们聊了什么"，`limit=8`。
2. 把召回结果按时间顺序整理成 3-5 条要点的 bullet list。
3. 每条要点控制在 25 字以内，聚焦于：我表达过的情绪、提到的正在做的事、以及任何未完成的承诺。
4. 如果 `memory_recall` 返回为空，直接告诉我"昨天没有留下记录"，不要编造内容。

请用中文回复。
