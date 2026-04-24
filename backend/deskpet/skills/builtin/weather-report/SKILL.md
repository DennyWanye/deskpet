---
name: weather-report
description: 查询当前所在城市的天气并播报
version: 0.1.0
author: deskpet
task_types: [task, chat]
requires_script: false
---
请帮我查一下当前所在地的天气。

步骤：
1. 如果我在消息里给了城市（例如"${args[0]}"非空），直接用它；否则调用 `web_search` 搜索 "我的城市 天气 今天"，从结果里挑一个可信来源。
2. 调用 `web_fetch`（或 `mcp_call` 到 weather server）拿到今日温度、体感、降水概率、风力四项。
3. 用一句话播报：`今天<城市>气温 X°C，体感 Y°C，降水概率 Z%，风力 W 级`，若数据缺失用"未知"占位。
4. 如果所有请求都失败，告诉我"天气接口暂时不可用，稍后再试"，不要编造数值。

请用中文输出。
