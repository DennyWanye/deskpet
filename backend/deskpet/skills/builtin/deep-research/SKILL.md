---
name: deep-research
description: 对一个主题做严肃的多源调研，产出带引用 + 抓取时间的结构化报告（不编造结论）。基于 DeepResearch V8 方法论。
when_to_use: 用户要深度调研、综述报告、技术选型/竞品/政策分析、多源查证、要带引用来源的研究报告、或要求"第二轮/继续深入"时。简单事实查一下用 web_search，不用本技能。
triggers: [深度调研, 调研报告, 调查研究, 研究报告, 深入调研, 做个调研, 做一份调研, 综述, 技术选型, 竞品研究, 政策分析]
version: 0.2.0
author: deskpet
task_types: [recall, web_search, task]
argument_hint: <主题> [--depth=light|standard|deep]
requires_script: false
---

我需要你帮我做一份**严肃**的研究报告。要的是**真实、可核验、有出处**的内容，
**不接受任何无来源的结论**。本技能基于 DeepResearch V8 方法论。

## 0. 先判断：要不要动用本技能（路由）

**该用 deep-research**（至少满足一条）：
- 用户要一份能复用/分享的书面产物（报告/综述/备忘）
- 需要跨多个来源综合
- 是决策导向、权衡很重要（技术选型、竞品、政策）
- 用户要"第二轮 / 继续深入 / 有什么变化"

**不该用**（改用 `web_search` 直接答，别小题大做）：
- 简单事实查找 / 名词解释
- 总结单篇文章/网页
- 1-2 个来源就能答的短问题
- 用户明确只要一个简短答案

> 判据：**这个任务需不需要"可复用的证据产物 + 多源综合"？** 不需要就做轻的。

## 1. 选输出档位（搜之前先定）

| 档位 | 用于 | depth 参数 | 目标长度 |
|---|---|---|---|
| **简报 brief** | 简洁但有证据支撑的回答（默认） | `standard` | 800-1800 字 |
| **完整报告 full** | 综述/全面分析/决策文档 | `deep` | 2500-6000 字 |
| **增量 delta** | "继续/第二轮/有什么变化" | `standard`（带上一轮上下文） | 600-1800 字 |

**用户没要长报告就默认 brief。** 别动不动甩 6000 字。

## 2. 调 `research_run` 工具

> ⚠️ **必须用 `research_run` 一次完成深度调研，禁止自己用 `web_search` +
> `web_fetch` 手动拼。** `research_run` 内部已经做了多源搜索 + 分层权威打分 +
> 反思迭代补证 + 引用自检 + 报告落盘——手动编排会跳过打分/校验/落盘，浪费
> 工具调用且质量更差。只有"快速查一个事实/网址"才用 `web_search`。

```
research_run(topic=<用户主题>, depth=light|standard|deep)
```

工具内部跑：plan（拆 3-6 子问题）→ **query 扩展（multi-query 改写 + HyDE，提召回）**
→ search（多引擎兼容性降级队列：必应→DuckDuckGo，百度备选；中文自动走中文区；某引擎被
墙/限流自动降级下一个；**政策/企业/学术类子问题额外 site: 定向官方域**：政策→site:gov.cn、
上市公司→site:cninfo.com.cn、学术→site:arxiv.org）→ **中文一手源直连（谈上市公司/财报→
巨潮资讯公告 PDF；谈国标/标准→国家标准全文系统，中国可直连）**→
fetch+extract（trafilatura 抽正文，JS 渲染站抽不到时**二级兜底 Jina Reader** 跑 JS 救回
+ 抓取时间）→ **分层打分**（域名权威 TIER1/2/3 含中文学术/媒体 + 新鲜度按主题速度 +
相关性[关键词+BGE-M3语义] + 深度 + 来源多样性）→
**deep 档反思迭代**（找证据缺口 → 补搜第二轮）→ synthesize（每条事实配 `[^n]`）→
cite_check（自检引用真实）。

返回 `ResearchReport`：
```python
{
  "topic": "...",
  "summary": "TL;DR 一段话",
  "report_md": "完整 markdown 报告（含 footnotes + 引用 appendix）",
  "citations": [{"n":1,"url":"...","title":"...","snippet":"...","fetched_at":1700000000.0}, ...],
  "sub_questions": ["..."],
  "coverage": {"n_sources":8,"n_domains":5,"rounds":2,"topic_velocity":"fast",
               "cite_check_ok":true,"unique_domains":5,"max_single_domain_share":0.2,
               "diversity_ok":true,...},
  "path": "...\\OutPut\\Research\\xxx.md",   # 报告已自动落盘
  "errors": [...]
}
```

## 3. 给用户的回复（报告组装规则）

**正常情况**：
- 直接把 `report_md` 完整贴出来——已含 `[^n]` 引用和 appendix。
- 顶部加一行覆盖度：`> 调研覆盖 **X 个来源** 来自 **Y 个独立域名**（N 轮检索，引用自检通过）。`
- **报告已自动存到 `coverage.path`（OutPut/Research/*.md）——把这个完整路径告诉用户。**

**每份产物必须包含**（V8 硬要求）：
- 对用户问题的**直接回答**
- **来源支撑的发现**与**你自己的分析**分开写（"基于这些来源，最可能的解读是…"）
- **局限/权衡**（证据薄/旧/间接/冲突在哪；什么条件会翻转结论）—— **必写**
- 校准过的不确定性，不要把弱证据写成精确确定
- 一个**非显然的洞察**（不要为唱反调而唱反调；稳定话题给最值得注意的细节即可）
- 只有用户在做**选择**时才加"决策框架"

**自检不通过（`coverage.cite_check_ok == false`）**：报告带 ⚠️。**主动**告诉用户
"自检发现 N 个引用编号无效，请核对来源后使用"，建议重跑或自己核对，不要假装修好。

**来源过于集中（`coverage.diversity_ok == false`）**：提醒用户"证据集中在少数域名，
代表性有限"。

**完全失败（`citations == []`）**：老实说"搜索/抓取都失败了，可能网络问题或主题太冷门，
试试更具体的关键词"。**永远不要**用预训练知识替代搜索结果。

## 4. 红线（不可妥协）

1. **不编造引用**：报告里每个 `[^n]` 必须在 `citations` 列表里。
2. **不省略 errors**：工具返回了 errors 就在末尾折叠提一句"调研中遇到：…"。
3. **抓取时间要可见**：`fetched_at` 体现信息新鲜度；论据基于一年前旧文要明示。
4. **证据分级 claim-specific**：产品特性看官方文档/release notes；法规看官方原文；
   科学效力看同行评审；市场趋势看财报/权威研究。**有名气 ≠ 适合支撑这条 claim。**

## 5. 与 ppt-generate / doc_create 串联

用户说"研究 X 然后做成 PPT / 写成 Word"：
1. 先 `research_run` → 拿 `report_md` + `citations`
2. PPT：每个 `##` 章节 → 一张 bullet slide，引用 URL 放 `notes`，调 ppt_create
3. Word：report_md 直接 doc_create（heading/paragraph/list 元素），引用做附录

用中文回复。引用编号保持工具返回时的数字，不要重新编号。
