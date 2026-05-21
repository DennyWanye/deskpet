---
name: deep-research
description: 对一个主题做严肃的多源调研，产出带引用 + 抓取时间的结构化报告（不编造结论）
version: 0.1.0
author: deskpet
task_types: [recall, web_search, task]
argument_hint: <主题> [--depth=light|standard|deep]
requires_script: false
---

我需要你帮我做一份**严肃**的研究报告。要的是**真实、可核验、有出处**的内容，**不接受任何无来源的结论**。

## 流程契约（严格遵守）

调用 `research_run(topic=<用户主题>, ...)` 工具。工具内部会跑：

1. **plan**：把主题拆 3-6 个子问题
2. **search**：每个子问题在 DuckDuckGo 上拉 3-5 个 URL（免费，无 API key）
3. **fetch + extract**：并发抓取 + trafilatura 抽正文，标记抓取时间戳
4. **score**：按域名权威性（wikipedia/arxiv/.gov/.edu 加分；quora/medium/csdn 减分）+ 内容长度 + 关键词覆盖打分
5. **synthesize**：LLM 把高分段落综合成 Markdown 报告，每条事实必须配 `[^n]` footnote
6. **cite_check**：自检每个 footnote 都映射到真实来源

工具返回 `ResearchReport`：
```python
{
  "topic": "...",
  "summary": "TL;DR 一段话",
  "report_md": "完整 markdown 报告（含 footnotes + 引用 appendix）",
  "citations": [{"n": 1, "url": "...", "title": "...", "snippet": "...", "fetched_at": 1700000000.0}, ...],
  "sub_questions": ["...", ...],
  "coverage": {"n_sources": 8, "n_domains": 5, "cite_check_ok": true, ...},
  "errors": [...]
}
```

## 参数选择

- `max_sub_questions`: light=3, standard=5（默认）, deep=6
- `max_urls_per_query`: light=2, standard=4（默认）, deep=5
- `max_total_passages`: 默认 12，deep 用 16

## 输出给用户

**正常情况**：
- 直接把 `report_md` 完整贴出来——里面已经有 `[^n]` 引用和 appendix。
- 在 `report_md` 上方加一行 metadata：`> 调研覆盖 **X 个来源** 来自 **Y 个独立域名**；抓取时间 YYYY-MM-DD HH:MM。`

**自检不通过 (`coverage.cite_check_ok == false`)**：
- 报告里会带 ⚠️ 警告。**主动**告诉用户「自检发现 N 个引用编号无效，请核对来源后使用」。
- 不要试图"修复"——告诉用户这是 LLM 综合时的错误，建议重新跑或自己核对。

**完全失败 (`citations == []`)**：
- 老老实实告诉用户「搜索/抓取都失败了。可能是网络问题或主题太冷门。可以试着用更具体的关键词」。
- **永远不要**用你的预训练知识替代搜索结果。

## 红线

1. **不编造引用**：你看到的每个 `[^n]` 必须在 `citations` 列表里。
2. **不省略 `errors`**：如果工具返回了 errors，在回复末尾以折叠形式提一句"调研过程中遇到以下问题: ..."。
3. **抓取时间要可见**：`fetched_at` 体现"信息新鲜度"，论据基于一年前的旧文要明示。
4. **学术 / 官方源优先**：score 已经做了，但你回复总结时也要倾向引用 wikipedia/arxiv/.edu/.gov 的条目。

## 与 ppt-generate 串联

用户如果说「研究 X 然后做成 PPT」：
1. 先跑 deep-research → 拿到 `report_md` + `citations`
2. 把每个章节 (## 开头) 转成 ppt outline 的一张 bullet slide
3. 引用 URL 放到对应 slide 的 `notes` 字段（备注页）
4. 调 ppt-generate

用中文回复。引用编号保持工具返回时的数字，不要重新编号。
