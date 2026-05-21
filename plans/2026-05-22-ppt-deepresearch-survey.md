# PPT 生成 + DeepResearch Skill — 调研与设计

日期：2026-05-22
作者：调研 by Claude
状态：调研稿 + 实施方案

---

## TL;DR

给桌宠加两个 builtin skill：`ppt-generate` 和 `deep-research`。
两者可串联：deep-research 产出结构化报告 → ppt-generate 把报告转成 .pptx。

底层不依赖外部付费 API（沿用 deskpet 原则）：
- PPT：用 `python-pptx` 本地生成，3 套主题 × 7 种布局
- DeepResearch：复用现有 `web_fetch` / `web_extract_article` / `web_crawl` + DuckDuckGo HTML 端点免费搜索 + 多轮 LLM 推理

---

## 一、业界 PPT 生成现状（2025-26）

### 主流玩家

| 产品 | 技术路线 | 优点 | 缺点 |
|---|---|---|---|
| **Gamma.app** | LLM 生成大纲 + 自家模板引擎（前端 React 渲染）| 视觉好、布局智能、卡片式 | 不导出 .pptx 友好；锁在 SaaS |
| **Tome.app** | 同上，叙事驱动（story-first）| 故事流畅；嵌入丰富 | 锁定平台 |
| **Beautiful.ai** | 智能模板引擎 | 自动版式调整 | 商务模板偏少 |
| **Slidesgo / Canva AI** | 海量模板检索 + LLM 填词 | 模板美 | "千篇一律"，AI 含量低 |
| **MS Copilot for PPT** | GPT-4o + Office Open XML 后端 | 与 Office 生态深度集成 | 需要订阅 |
| **MarpIt / Reveal.js** | Markdown → HTML/PPT | 程序员友好，diff 友好 | 视觉中规中矩 |
| **python-pptx** | 直接写 .pptx XML | 完全可控、本地、免费 | 需要自己设计版式 |

### 关键 takeaway

1. **大纲先行**：所有 AI PPT 工具都先生成 outline，再选模板填充。outline 质量 ≈ PPT 质量。
2. **布局复用**：一份 PPT 一般 ≤ 10 个布局类型反复用——title/section/bullet/two-column/image/quote/data。
3. **主题统一**：颜色 + 字体 + 间距三件套需要 baked-in 才好看；用户改不动太多东西。
4. **导出 .pptx 比 PDF 重要**：用户要二次编辑。

### 我们的选择

**python-pptx 本地生成 + 模板化布局 + 桌宠 LLM 出 outline**。
- 优点：零外部成本、可离线、用户拿到 .pptx 后可在 PowerPoint/Keynote/WPS 二次编辑。
- 缺点：视觉不可能赶上 Gamma，但是"专业、整洁、统一"足够。

---

## 二、业界 DeepResearch 现状（2025-26）

### 主流玩家

| 产品 | 技术路线 | 关键特性 |
|---|---|---|
| **OpenAI Deep Research (o3-deep-research)** | LLM agent 长时间循环（10-30 分钟）+ 浏览器 + 文件读取 + 多源对照 | 引用每条事实；多步 self-critique |
| **Perplexity Pro Search / Deep Research** | RAG + 搜索 + LLM 综合 | 来源在线展示；focus mode（learn/academic/news）|
| **Anthropic Research（Claude）** | Tool-use 循环（multi-tool agent）+ 引用 | 抓 PDF / 网页 / 内部知识 |
| **You.com Genius** | Multi-LLM 编排 + 搜索 | 比较多个模型答案 |
| **Google Gemini Deep Research** | Gemini + Google Search 索引 | 索引覆盖最广 |

### 关键 takeaway

1. **分阶段流水线**：plan → search × N → fetch + extract → synthesize → cite-check。
2. **引用必备**：每条事实必须带 URL + 抓取时间。没有引用的结论 = 幻觉。
3. **多源对照**：同一事实至少 2 个独立源支持才算 "high confidence"。
4. **deep mode 不是单次 RAG**：是多轮迭代——读了一篇发现新线索 → 再搜 → 再读。
5. **抗噪声**：内容农场、SEO 垃圾页要识别；学术 / 官方源加权。

### 我们的选择

**多阶段流水线 + 复用 deskpet web 工具 + 严格引用契约**：

```
research_run(topic):
  1. plan      → LLM 把 topic 拆 3-6 个 sub-questions
  2. search    → 每个 sub-q 跑 DuckDuckGo HTML SERP 取 top-N URL
  3. fetch     → 并发 web_extract_article 拉正文 + 时间戳
  4. score     → 按域名权威性 + 内容长度 + 关键词覆盖打分
  5. synthesize→ LLM 把高分段落整合成 markdown 报告 +
                 inline footnote 引用 [^n]
  6. cite_check→ 抽样校验：报告里每个 [^n] 都能映射回真实段落
  7. return    → {report_md, citations[], coverage_stats}
```

降级：LLM 不可用 → 仅返回 top-K passages（用户自己读）；
搜索全失败 → 返回明确错误，不编造结论。

---

## 三、实施计划

### 文件清单

```
backend/
├── deskpet/
│   ├── tools/
│   │   ├── ppt_tools.py         # NEW — python-pptx wrapper + themes
│   │   └── research_tools.py    # NEW — multi-stage research orchestrator
│   └── skills/builtin/
│       ├── ppt-generate/SKILL.md      # NEW
│       └── deep-research/SKILL.md     # NEW
└── tests/
    ├── test_deskpet_ppt_tools.py      # NEW
    └── test_deskpet_research_tools.py # NEW

plans/
└── 2026-05-22-ppt-deepresearch-survey.md  (this file)
```

### 接口契约

**`ppt_tools.py`**:
```python
@dataclass
class SlideOutline:
    """One slide spec — LLM produces a list of these."""
    layout: Literal["title","section","bullet","two_column","image","quote","toc"]
    title: str
    subtitle: str = ""
    bullets: list[str] = field(default_factory=list)
    left: list[str] = field(default_factory=list)    # two_column.left
    right: list[str] = field(default_factory=list)   # two_column.right
    image_path: str | None = None
    quote: str = ""
    cite: str = ""

def ppt_create(
    outline: list[dict] | str,  # accepts JSON string too
    *,
    theme: Literal["minimal","dark","playful"] = "minimal",
    title: str = "",
    author: str = "DeskPet",
    output_path: str | None = None,
) -> dict:
    """Returns {"path": str, "slide_count": int, "theme": str}.
    On failure / missing python-pptx → {"error": "...", "markdown_fallback": "..."}.
    """
```

**`research_tools.py`**:
```python
@dataclass
class Citation:
    n: int               # footnote number
    url: str
    title: str
    snippet: str
    fetched_at: float

@dataclass
class ResearchReport:
    topic: str
    summary: str         # 1-paragraph TL;DR
    sections: list[dict] # [{heading, body_md, cite_ids}]
    citations: list[Citation]
    coverage: dict       # {n_sources, n_domains, n_subquestions, ...}

async def research_run(
    topic: str,
    *,
    llm_call: Callable[[str], Awaitable[str]],
    max_sub_questions: int = 5,
    max_urls_per_query: int = 5,
    max_total_passages: int = 20,
) -> ResearchReport:
    """Pipeline: plan → search → fetch → synthesize → cite_check."""
```

### 测试策略

- **ppt_tools**: 用临时目录生成真的 .pptx → 用 python-pptx 读回检查 slide_count / 每张 slide 的 title / layout / bullets。
- **research_tools**: mock LLM + mock httpx；验证 plan/search/synthesize 三层的契约；cite_check 拒绝"引用不存在的 footnote"的报告。
- 全链路 manual E2E：跑一次真 LLM + 真 DuckDuckGo → 检查输出 PPT 能被 PowerPoint 打开 + 引用合规。

### 工作量估算

| 步骤 | 时间 |
|---|---|
| 调研 + 设计（本文档） | 完成 |
| ppt_tools.py + 测试 | 4-6 小时 |
| research_tools.py + 测试 | 6-8 小时 |
| 两个 SKILL.md + 接入 | 1 小时 |
| Manual E2E + 截图证据 | 1 小时 |
| 全套 pytest 回归 | 30 分钟 |

---

## 四、与 memory-v2 (Phase A-E) 的关系

DeepResearch 产出的引用条目，未来可以作为 `fact` 行（category=`research`）写进 Phase B `facts` 表，agent 召回时直接命中。这是 V2 优化方向，本期不实现。

PPT 生成的"上次做过的演讲"可以进 Phase D `workspace_state`，下次"再做一份类似的"时直接复用 outline。本期也不实现。

---

## 五、参考资料

- **python-pptx**：https://python-pptx.readthedocs.io/
- **OpenAI Deep Research blog**：2025-02 launch — multi-step browse + cite
- **Anthropic Claude research mode**：tool-use loop + citations
- **DuckDuckGo HTML endpoint**：`https://html.duckduckgo.com/html/?q=...` 无 API key
- **trafilatura paper**：Barbaresi 2021，正文抽取召回 92.5% F1
