# Spec: web-tools

## ADDED Requirements

### Requirement: Zero-Cost Web Toolset (No Paid APIs)

系统 MUST 提供 4 个零成本 web 工具：`web_fetch`、`web_crawl`、`web_extract_article`、`web_read_sitemap`，全部依赖开源库（`httpx + trafilatura + selectolax`）实现，MUST NOT 调用任何付费搜索 API（Brave / Bing / Tavily / Exa 等）。这是 D5 决策的落地。

#### Scenario: No paid search provider in codebase

- **WHEN** 审查 `deskpet/tools/web_*.py` 代码
- **THEN** MUST NOT 出现付费搜索 API 的 URL / auth header 模式；依赖列表只有开源库

### Requirement: web_fetch — URL Content Extraction

工具 `web_fetch(url: str, timeout: int = 10) → {"status_code", "text", "title", "url_final"}` MUST 用 httpx 请求 URL，用 trafilatura 提取正文。重定向 MUST 跟随（最多 5 次）。超时 MUST 可配。

#### Scenario: Fetch returns extracted main text

- **WHEN** `web_fetch("https://docs.python.org/3/tutorial/index.html")`
- **THEN** 返回的 `text` MUST 是去导航/广告的正文主体（trafilatura 提取），`title` MUST 非空

#### Scenario: Timeout returns error not raise

- **WHEN** URL 10 秒内无响应
- **THEN** 工具 MUST 返回 `{"error": "timeout", "retriable": true}`，不得让 agent loop 崩溃

### Requirement: web_crawl — Seed-Based BFS Crawl

工具 `web_crawl(seed_url, keywords, max_depth=2, max_pages=20) → list[{url, excerpt, score}]` MUST 从 seed_url 开始 BFS 同域链接，按 keywords 打分返回 top N 页面。MUST 尊重 robots.txt。单域名并发 MUST ≤ `per_domain_max_concurrency`（默认 2）。请求间隔 MUST ≥ `request_interval_ms`（默认 500ms）。

#### Scenario: Crawl stays within same domain

- **WHEN** `web_crawl(seed_url="https://stackoverflow.com/...", keywords=["python", "asyncio"])`
- **THEN** 返回结果 MUST 只含 `*.stackoverflow.com` 域名的 URL

#### Scenario: Robots.txt blocks disallowed paths

- **WHEN** seed 站点 robots.txt 禁止 `/admin/`
- **THEN** crawl MUST 跳过所有 `/admin/*` 路径，不得访问

#### Scenario: Rate limit respected

- **WHEN** 对同一域名连续抓 10 页
- **THEN** 相邻请求间隔 MUST ≥ 500ms，并发连接数 MUST ≤ 2

### Requirement: web_extract_article — Structured Article Extraction

工具 `web_extract_article(url) → {"title", "author", "date", "text", "language"}` MUST 专用于新闻/博客类页面，用 trafilatura 结构化提取 metadata。无法识别字段 MUST 返回 null 而非报错。

#### Scenario: Article metadata extracted

- **WHEN** `web_extract_article("https://example.com/blog/some-post")`
- **THEN** 返回结构 MUST 含 4 字段（title/author/date/text），缺失字段为 null

### Requirement: web_read_sitemap — Sitemap Parsing

工具 `web_read_sitemap(domain) → list[{"url", "lastmod"}]` MUST 拉取 `{domain}/sitemap.xml`（也尝试 `/sitemap_index.xml`），解析返回所有 URL + 最后修改时间。用于 agent 先看站点全貌再决定 crawl 起点。

#### Scenario: Sitemap parsed correctly

- **WHEN** `web_read_sitemap("docs.python.org")`
- **THEN** 返回 list 长度 > 0，每项含 url + lastmod（ISO 8601 字符串）

#### Scenario: Fallback to sitemap_index

- **WHEN** `{domain}/sitemap.xml` 404 但 `/sitemap_index.xml` 存在
- **THEN** 工具 MUST 自动尝试 sitemap_index 并递归解析子 sitemap

### Requirement: User-Agent and Attribution

所有 web 工具 HTTP 请求 MUST 带 `User-Agent: DeskPet/0.6 (+https://github.com/.../deskpet)` 或用户配置的 `config.tools.web.user_agent`。

#### Scenario: UA header present on every request

- **WHEN** web_fetch/web_crawl 发起任一 HTTP 请求
- **THEN** 请求 headers MUST 包含 UA 且格式符合桌宠 UA 模板

### Requirement: Preferred Sources Hint

系统 MUST 支持 `config.tools.web.preferred_sources` 白名单（`[{domain, topic}]` 列表），在 agent system prompt 中以 hint 形式注入，让 agent 知道"查 Python 去 docs.python.org / 查代码去 stackoverflow 等"。

#### Scenario: Preferred sources appear in agent prompt

- **WHEN** config 配置了 `preferred_sources=[{domain:"stackoverflow.com", topic:"code"}, ...]`
- **THEN** agent system prompt（由 ContextAssembler PersonaComponent 或 ToolComponent 生成）MUST 包含形如 "遇到 code 问题可以 crawl stackoverflow.com" 的提示

### Requirement: Graceful Degradation on Block

当某域名连续 N 次返回 429 / 403 / captcha 页面时，系统 MUST 标记该域名 "blocked" 在 in-memory cache 中（1 小时过期），后续 web_crawl 对该域名 MUST 跳过并返回 `{"error": "domain temporarily blocked"}`。

#### Scenario: Consecutive 429 triggers temporary block

- **WHEN** `example.com` 连续 3 次返回 429
- **THEN** 系统 MUST 在后续 1 小时内 skip 该域名的 crawl 请求，返回明确 error
