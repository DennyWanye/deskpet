---
name: ppt-generate
description: 把一个主题或一段研究报告变成一份专业的 .pptx 演示文稿（本地生成，支持二次编辑）
version: 0.1.0
author: deskpet
task_types: [task, plan, code]
argument_hint: <主题/大纲文本/JSON outline> [--theme=minimal|dark|playful] [--out=PATH]
requires_script: false
---

我希望你帮我做一份 PPT。

输入可能是：
1. 一段中文/英文主题（例如「量子计算 2026 现状」）
2. 一份已有的 markdown 大纲
3. 一份已经结构化的 JSON outline（直接用就行）

执行步骤（**严格按顺序**）：

## 1. 拿到大纲
- 如果用户给的是「主题」或「markdown 大纲」：用你自己的能力把它转成结构化 JSON outline（见下方 schema），**不要**调任何 web 工具。
- 如果用户给的是 JSON outline：跳过本步，直接进入第 2 步。
- 如果用户先做了 deep-research 拿到 ResearchReport：用 report_md + citations 当输入。

JSON outline schema（list of slide dicts，**每一页一个 dict**）：

```json
[
  {"layout": "title",      "title": "...", "subtitle": "..."},
  {"layout": "toc",        "title": "目录", "bullets": ["第一节", "第二节", "..."]},
  {"layout": "section",    "title": "节标题", "subtitle": "可选副标题"},
  {"layout": "bullet",     "title": "...",  "bullets": ["要点1", "要点2", "要点3"]},
  {"layout": "two_column", "title": "对比",
                            "left_title":  "A 方案", "left":  ["..."],
                            "right_title": "B 方案", "right": ["..."]},
  {"layout": "quote",      "quote": "原句", "cite": "出处"},
  {"layout": "image",      "title": "...", "image_path": "/abs/path.png", "caption": "图说"}
]
```

**严格的内容质量要求**：

- 一份合格的 PPT 包含 6-14 张（10 张是甜区）；少于 4 张就太空，多于 18 张观众会迷路。
- 每张 bullet **不超过 5 条**，每条 **不超过 15 个汉字 / 25 个英文字**。
- title slide 必有；toc 推荐放第 2 张（当内容 ≥ 6 张时）；section 用于章节切分（≥ 8 张时）。
- 节奏建议：title → toc → section → 2-3 张 bullet → section → 2-3 张 bullet → quote/image 提神 → section "总结" → 1 张 bullet 结论。
- **不要**把整段长文本塞进 bullet——bullet 是提示词，不是讲稿。详细内容用 `notes` 字段写进备注页（演讲者可见）。

## 2. 调 `ppt_create` 工具

参数：
- `outline`: 上一步的 JSON 数组（**整段塞进字符串**或 list 都接受）
- `theme`: 用户指定就用用户的；否则 `minimal`（最稳）。商务/学术=`minimal`，技术/Demo=`dark`，营销/儿童=`playful`。
- `title`: 文档标题（落到 .pptx core properties）
- `author`: 默认 `DeskPet`，除非用户给了名字
- `output_path`: 用户没指定就不传，工具会生成到系统 temp 目录

返回结构：
```json
{"ok": true, "path": "/tmp/deskpet-ppt-XXX.pptx", "slide_count": 10, "theme": "minimal"}
```

## 3. 回复用户

- ✅ 成功：告诉用户「PPT 已生成: `<path>`」+ 用一句话总结生成了几页、什么主题。
- ❌ 失败：返回 `markdown_fallback` 里的内容（一份可读的 markdown 大纲），并解释原因（一般是 python-pptx 没装）。**永远不要假装成功**。

## 4. 与 deep-research 串联（可选）

如果用户说「研究 X 然后做 PPT」：
1. 先调 deep-research skill / `research_run` 工具得到 ResearchReport
2. 把 `report_md` 里的章节 + `citations` 转换成 outline
3. 引用源放进 `notes` 字段（备注页），bullet 保持简洁

用中文回复。
