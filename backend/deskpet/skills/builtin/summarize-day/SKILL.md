---
name: summarize-day
description: 总结今天的对话要点
version: 0.1.0
author: deskpet
task_types: [recall, chat]
requires_script: false
---
请对今天（当前日期）到目前为止我们的对话做一次简短总结。

要求：
1. 先调用 `memory_recall`，参数 `query="今天聊过的重点"`、`limit=10`，用它返回的片段作为事实依据。
2. 输出结构：
   - **主题**：一句话概括今天的主轴。
   - **要点**：3 条 bullet，各 30 字以内。
   - **待跟进**：如果我提到了"明天""待会""等下"等延迟动作，单独列出；没有就写"无"。
3. 不要把模型自身的建议混入要点；只基于记忆片段。
4. 全程使用中文。
