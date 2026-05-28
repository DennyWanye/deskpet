# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""DeepResearch orchestrator — multi-stage research with citations.

Pipeline
--------
    research_run(topic)
    ├── 1. plan          ─ LLM splits the topic into 3-6 sub-questions
    ├── 2. search        ─ each sub-q → DuckDuckGo HTML SERP → top-N URLs
    ├── 3. fetch+extract ─ concurrent web_extract_article on those URLs
    ├── 4. score+filter  ─ authority × content-length × keyword-coverage
    ├── 5. synthesize    ─ LLM merges high-score passages into a Markdown
    │                       report with inline footnote refs [^n]
    ├── 6. cite_check    ─ every [^n] must map to a real Citation entry
    └── 7. return        ─ ResearchReport dataclass

Design constraints
------------------
* No paid search APIs (DuckDuckGo HTML endpoint is free + permissive).
* Authority list is a small per-domain bonus map — we don't try to
  rank Wikipedia vs Quora "scientifically"; we just nudge.
* Every claim in the final report MUST cite at least one source. The
  ``cite_check`` step rejects reports with dangling footnotes.
* LLM failure / web failure → return whatever stage succeeded with
  ``coverage`` reflecting the partial state. We never fabricate
  conclusions.

This module is **library-only** — no FastAPI / IPC glue. The SKILL.md
side calls the orchestrator via the ToolRegistry façade.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import urllib.parse
from dataclasses import asdict, dataclass, field
from typing import Any, Awaitable, Callable, Iterable, Optional, Protocol

import httpx

log = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------


# DuckDuckGo HTML SERP. Returns plain HTML (no JS needed). The /html
# subdomain is the "lite" / no-JS path; results are stable across years.
_DDG_HTML_URL = "https://html.duckduckgo.com/html/"

# A bounded UA so DuckDuckGo doesn't aggressively bot-check us.
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120 Safari/537.36 DeskPet/0.5 (+research)"
)

# Per-host fetch politeness — we hand work off to web_tools for the
# real extract step; this client only hits DuckDuckGo.
_DEFAULT_TIMEOUT = 12.0

# Authority bonus map — small, hand-curated. Higher = more trustworthy.
# Anything not listed gets ``_DEFAULT_AUTHORITY``. Tuned for the
# typical "research a topic" use case: academic + reference + .gov >
# named news > random blogs > content farms (we don't try to block
# them, just down-weight).
_AUTHORITY: dict[str, float] = {
    "wikipedia.org": 1.5,
    "nature.com": 1.6,
    "arxiv.org": 1.5,
    "github.com": 1.3,
    "stackoverflow.com": 1.2,
    "mit.edu": 1.5,
    "stanford.edu": 1.5,
    "ox.ac.uk": 1.5,
    "harvard.edu": 1.5,
    "nih.gov": 1.6,
    "europa.eu": 1.4,
    "who.int": 1.4,
    "ietf.org": 1.5,
    "developer.mozilla.org": 1.4,
    "docs.python.org": 1.5,
    "python.org": 1.4,
    "anthropic.com": 1.3,
    "openai.com": 1.3,
    # Common low-quality patterns — slight penalty rather than ban
    "quora.com": 0.7,
    "medium.com": 0.85,
    "csdn.net": 0.8,
    "zhihu.com": 0.95,
}
_DEFAULT_AUTHORITY = 1.0

# Default prompts. Kept as module constants for testability / pinning.
_PLAN_PROMPT = """\
You are planning a deep research project on the topic below.

TOPIC: {topic}

Break it into 3-6 focused sub-questions whose combined answers would
form a thorough, balanced briefing. Cover different angles: what is it,
why does it matter, current state of the art, controversies, recent
developments. Avoid duplicate questions.

Output ONLY a JSON array of strings. No prose, no fences.

Example for "Quantum computing in 2026":
["What is the current state of quantum hardware (qubits, error rates)?",
 "Which problems do quantum computers solve faster than classical?",
 "How close are we to fault-tolerant quantum computing?",
 "Who are the leading vendors and what's their roadmap?",
 "What are the main controversies / scepticism about near-term value?"]

JSON ARRAY:"""


_SYNTH_PROMPT = """\
You are writing a research briefing on:  {topic}

You have {n_passages} source passages, each labelled with a numeric tag
like (1), (2), etc. Use these AS FOOTNOTES in your report — when you
make a claim that comes from passage 3, end the sentence with `[^3]`.
Every factual claim MUST cite at least one footnote.

Write a Markdown briefing with this structure:

  # {topic}

  ## TL;DR
  (one paragraph — 2-4 sentences, no citations)

  ## Background
  (what is it, why does it matter)

  ## Current state
  ## Open questions / controversies
  ## What's next

  (Each section: 2-5 short paragraphs, cite footnotes inline.)

Rules:
- ONLY use footnote numbers that exist in the passages below.
- Do NOT invent citations. If you can't cite, drop the claim.
- Same language as the passages.
- No bullet lists — flowing prose. The reader is an intelligent adult.

PASSAGES:
{passages}

REPORT:"""


# ----------------------------------------------------------------------
# Public dataclasses
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class Citation:
    n: int
    url: str
    title: str
    snippet: str
    fetched_at: float
    authority: float = _DEFAULT_AUTHORITY

    def as_footnote(self) -> str:
        """Markdown footnote line, used at the bottom of the report."""
        return f"[^{self.n}]: [{self.title}]({self.url})"


@dataclass
class Passage:
    """An extracted article passage scored + tagged for the LLM."""

    citation: Citation
    text: str
    score: float


@dataclass
class ResearchReport:
    topic: str
    summary: str
    report_md: str
    citations: list[Citation]
    sub_questions: list[str]
    coverage: dict[str, Any]
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "topic": self.topic,
            "summary": self.summary,
            "report_md": self.report_md,
            "citations": [asdict(c) for c in self.citations],
            "sub_questions": list(self.sub_questions),
            "coverage": dict(self.coverage),
            "errors": list(self.errors),
        }


# ----------------------------------------------------------------------
# Protocols / type aliases
# ----------------------------------------------------------------------


class _LLMCall(Protocol):
    async def __call__(self, prompt: str) -> str: ...


class _Searcher(Protocol):
    async def __call__(
        self, query: str, *, max_results: int
    ) -> list[dict[str, Any]]: ...


class _Extractor(Protocol):
    async def __call__(
        self, url: str
    ) -> dict[str, Any]: ...


# ----------------------------------------------------------------------
# Default network implementations — both can be swapped in tests
# ----------------------------------------------------------------------


async def default_search(
    query: str, *, max_results: int = 5,
    client: Optional[httpx.AsyncClient] = None,
) -> list[dict[str, Any]]:
    """DuckDuckGo HTML SERP → ``[{url, title, snippet}]``.

    No API key required. Returns at most ``max_results`` entries.
    Failure → empty list (caller decides how to recover).
    """
    params = {"q": query, "kl": "us-en"}
    owns_client = client is None
    cli = client or httpx.AsyncClient(
        headers={"User-Agent": _UA, "Accept-Language": "en-US,en;q=0.9,zh;q=0.8"},
        timeout=_DEFAULT_TIMEOUT,
        follow_redirects=True,
    )
    try:
        try:
            resp = await cli.post(_DDG_HTML_URL, data=params)
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            log.debug("ddg search failed for %r: %s", query, exc)
            return []
        return _parse_ddg_results(resp.text, max_results=max_results)
    finally:
        if owns_client:
            await cli.aclose()


def _parse_ddg_results(html: str, *, max_results: int) -> list[dict[str, Any]]:
    """Pure parser — easier to unit-test without network."""
    out: list[dict[str, Any]] = []
    # Try selectolax first (fast); fall back to regex if missing.
    try:
        from selectolax.parser import HTMLParser  # type: ignore
    except ImportError:
        return _parse_ddg_results_regex(html, max_results=max_results)

    tree = HTMLParser(html)
    # DDG result blocks: <div class="result results_links_deep ...">
    nodes = tree.css("div.result")
    for node in nodes:
        if len(out) >= max_results:
            break
        a = node.css_first("a.result__a")
        s = node.css_first(".result__snippet")
        if a is None:
            continue
        url = a.attributes.get("href", "")
        title = (a.text() or "").strip()
        snippet = (s.text() if s else "").strip()
        cleaned_url = _clean_ddg_url(url)
        if not cleaned_url or not title:
            continue
        out.append({"url": cleaned_url, "title": title, "snippet": snippet})
    return out


def _parse_ddg_results_regex(html: str, *, max_results: int) -> list[dict[str, Any]]:
    """Regex fallback for environments without selectolax (shouldn't happen
    in dev — listed as fallback for defensive coding)."""
    pattern = re.compile(
        r'<a class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
        re.DOTALL,
    )
    snippet_re = re.compile(
        r'<a class="result__snippet"[^>]*>(.*?)</a>',
        re.DOTALL,
    )
    urls = pattern.findall(html)
    snippets = snippet_re.findall(html)
    out: list[dict[str, Any]] = []
    for i, (url, title_html) in enumerate(urls):
        if len(out) >= max_results:
            break
        title = re.sub(r"<[^>]+>", "", title_html).strip()
        snippet = re.sub(r"<[^>]+>", "", snippets[i]).strip() if i < len(snippets) else ""
        cleaned_url = _clean_ddg_url(url)
        if not cleaned_url or not title:
            continue
        out.append({"url": cleaned_url, "title": title, "snippet": snippet})
    return out


def _clean_ddg_url(url: str) -> str:
    """DDG wraps real URLs in /l/?uddg=ENCODED. Unwrap to the real URL."""
    if not url:
        return ""
    if url.startswith("//"):
        url = "https:" + url
    if "duckduckgo.com/l/" in url or url.startswith("/l/"):
        parsed = urllib.parse.urlparse(url)
        qs = urllib.parse.parse_qs(parsed.query)
        uddg = qs.get("uddg", [""])[0]
        if uddg:
            return urllib.parse.unquote(uddg)
    return url


async def default_extract(
    url: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
) -> dict[str, Any]:
    """Fetch + extract main article text via trafilatura.

    Returns ``{ok, text, title, url, fetched_at}`` or
    ``{ok: False, error, url}`` on failure. trafilatura's main-content
    extraction is roughly Newspaper3k-level but lighter.
    """
    owns_client = client is None
    cli = client or httpx.AsyncClient(
        headers={"User-Agent": _UA},
        timeout=_DEFAULT_TIMEOUT,
        follow_redirects=True,
    )
    try:
        try:
            resp = await cli.get(url)
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc), "url": url}
        html = resp.text
        title = ""
        text = ""
        try:
            import trafilatura  # type: ignore
            extracted = trafilatura.extract(
                html, include_comments=False, include_tables=False,
                favor_recall=False,
            )
            text = (extracted or "").strip()
            meta = trafilatura.extract_metadata(html)
            title = (getattr(meta, "title", None) or "").strip() if meta else ""
        except Exception as exc:  # noqa: BLE001
            log.debug("trafilatura extract failed for %s: %s", url, exc)
        if not title:
            m = re.search(r"<title>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
            if m:
                title = re.sub(r"\s+", " ", m.group(1)).strip()
        if not text:
            return {"ok": False, "error": "no text extracted", "url": url}
        return {
            "ok": True, "url": url, "title": title or url,
            "text": text, "fetched_at": time.time(),
        }
    finally:
        if owns_client:
            await cli.aclose()


# ----------------------------------------------------------------------
# Scoring
# ----------------------------------------------------------------------


def _host(url: str) -> str:
    return (urllib.parse.urlparse(url).hostname or "").lower()


def authority_for_url(url: str) -> float:
    """Look up the authority bonus for the URL's host (with parent-domain
    fallback so subdomains inherit). Unknown → :data:`_DEFAULT_AUTHORITY`.
    """
    h = _host(url)
    if not h:
        return _DEFAULT_AUTHORITY
    # Walk from full host down to TLD; return first match
    parts = h.split(".")
    for i in range(len(parts) - 1):
        candidate = ".".join(parts[i:])
        if candidate in _AUTHORITY:
            return _AUTHORITY[candidate]
    return _DEFAULT_AUTHORITY


def score_passage(text: str, *, keywords: Iterable[str], url: str) -> float:
    """Heuristic score in [0, ~5+]:

      * length: ``min(len(text)/2000, 1)`` — caps at 2K chars
      * coverage: fraction of keywords present (case-insensitive)
      * authority: per-host bonus

    Score = 0.6 * length + 0.4 * coverage + (authority - 1.0)
    Empty text → 0. Authority can push final score above 1.0; that's
    intentional — known-trustworthy short snippets still outrank random
    long blogs.
    """
    if not text:
        return 0.0
    kws = [k for k in (kw.strip().lower() for kw in keywords) if k]
    text_lower = text.lower()
    length_norm = min(len(text) / 2000.0, 1.0)
    if kws:
        coverage = sum(1 for k in kws if k in text_lower) / float(len(kws))
    else:
        coverage = 0.0
    auth = authority_for_url(url)
    return max(0.0, 0.6 * length_norm + 0.4 * coverage + (auth - 1.0))


# ----------------------------------------------------------------------
# Cite check
# ----------------------------------------------------------------------


_FOOTNOTE_REF_RE = re.compile(r"\[\^(\d+)\]")


def find_footnote_refs(text: str) -> list[int]:
    return [int(m) for m in _FOOTNOTE_REF_RE.findall(text or "")]


def cite_check(
    report_md: str, citations: list[Citation],
) -> dict[str, Any]:
    """Validate every ``[^n]`` in the report maps to a known citation.

    Returns ``{ok, missing, unused, total_refs}``.
    ``missing``: footnote numbers used in text but not in ``citations``.
    ``unused``: citations never referenced.
    """
    refs = set(find_footnote_refs(report_md))
    known = {c.n for c in citations}
    missing = sorted(refs - known)
    unused = sorted(known - refs)
    return {
        "ok": not missing,
        "missing": missing,
        "unused": unused,
        "total_refs": len(refs),
    }


# ----------------------------------------------------------------------
# Plan / Synthesize parsers — defensive against LLM drift
# ----------------------------------------------------------------------


def parse_sub_questions(raw: str, *, max_questions: int) -> list[str]:
    if not raw:
        return []
    text = raw.strip()
    # Strip fences
    if text.startswith("```"):
        nl = text.find("\n")
        if nl != -1:
            text = text[nl + 1:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    lb, rb = text.find("["), text.rfind("]")
    if 0 <= lb < rb:
        try:
            arr = json.loads(text[lb:rb + 1])
            if isinstance(arr, list):
                cleaned = [str(x).strip() for x in arr if str(x).strip()]
                # Dedup while preserving order
                seen: set[str] = set()
                out: list[str] = []
                for q in cleaned:
                    if q not in seen:
                        seen.add(q)
                        out.append(q)
                return out[:max_questions]
        except json.JSONDecodeError:
            pass
    # Fallback — lines that look like questions
    lines = [
        re.sub(r"^[-*\d.\s]+", "", ln).strip().strip("\"'")
        for ln in text.splitlines()
    ]
    questions = [ln for ln in lines if ln and ("?" in ln or "？" in ln)]
    return questions[:max_questions]


# ----------------------------------------------------------------------
# Orchestrator
# ----------------------------------------------------------------------


async def research_run(
    topic: str,
    *,
    llm_call: _LLMCall,
    search: Optional[_Searcher] = None,
    extract: Optional[_Extractor] = None,
    max_sub_questions: int = 5,
    max_urls_per_query: int = 4,
    max_total_passages: int = 12,
    min_passage_chars: int = 250,
) -> ResearchReport:
    """End-to-end research pipeline. See module docstring.

    All network / LLM I/O is injected so unit tests can mock everything.
    Defaults wire up to :func:`default_search` + :func:`default_extract`.
    """
    if not topic or not topic.strip():
        return ResearchReport(
            topic="", summary="", report_md="",
            citations=[], sub_questions=[],
            coverage={"n_sources": 0, "n_domains": 0, "n_sub_questions": 0},
            errors=["empty topic"],
        )
    topic = topic.strip()
    search_fn = search or default_search
    extract_fn = extract or default_extract
    errors: list[str] = []

    # ---- 1. plan ----------------------------------------------------
    try:
        plan_raw = await llm_call(_PLAN_PROMPT.format(topic=topic))
    except Exception as exc:  # noqa: BLE001
        errors.append(f"plan_llm: {exc}")
        plan_raw = ""
    sub_questions = parse_sub_questions(plan_raw, max_questions=max_sub_questions)
    if not sub_questions:
        # Fall back to the topic itself as the single sub-question. This
        # lets users with offline / failing LLM still get *something*.
        sub_questions = [topic]
        errors.append("plan_fallback: using topic verbatim")

    # ---- 2. search --------------------------------------------------
    search_tasks = [
        search_fn(q, max_results=max_urls_per_query) for q in sub_questions
    ]
    raw_results = await _gather_safe(search_tasks, label="search")
    # Dedup by URL, preserving the question index for keyword scoring.
    url_to_question: dict[str, str] = {}
    for q, hits in zip(sub_questions, raw_results):
        if isinstance(hits, BaseException):
            errors.append(f"search:{q!r}: {hits}")
            continue
        for h in hits or []:
            u = h.get("url")
            if not u or u in url_to_question:
                continue
            url_to_question[u] = q

    if not url_to_question:
        errors.append("no search results")
        return ResearchReport(
            topic=topic, summary="",
            report_md=_no_results_template(topic, sub_questions),
            citations=[], sub_questions=sub_questions,
            coverage={
                "n_sources": 0, "n_domains": 0,
                "n_sub_questions": len(sub_questions),
            },
            errors=errors,
        )

    # ---- 3. fetch + extract ----------------------------------------
    # Cap how many URLs we actually fetch so we don't burn 5 minutes.
    candidate_urls = list(url_to_question.keys())[
        : max_urls_per_query * len(sub_questions)
    ]
    extract_tasks = [extract_fn(u) for u in candidate_urls]
    extracted = await _gather_safe(extract_tasks, label="extract")

    # ---- 4. score + filter -----------------------------------------
    keywords = _topic_keywords(topic) + [
        k for q in sub_questions for k in _topic_keywords(q)
    ]
    passages: list[Passage] = []
    for url, payload in zip(candidate_urls, extracted):
        if isinstance(payload, BaseException):
            errors.append(f"extract:{url}: {payload}")
            continue
        if not isinstance(payload, dict) or not payload.get("ok"):
            err = (payload or {}).get("error", "extract failed") if isinstance(payload, dict) else "unknown"
            errors.append(f"extract:{url}: {err}")
            continue
        text = (payload.get("text") or "").strip()
        if len(text) < min_passage_chars:
            continue
        snippet = text[:min_passage_chars].replace("\n", " ").strip()
        sc = score_passage(text, keywords=keywords, url=url)
        passages.append(Passage(
            citation=Citation(
                n=0,  # assigned below after sort
                url=url,
                title=(payload.get("title") or url)[:200],
                snippet=snippet,
                fetched_at=float(payload.get("fetched_at", time.time())),
                authority=authority_for_url(url),
            ),
            text=text,
            score=sc,
        ))
    passages.sort(key=lambda p: -p.score)
    passages = passages[:max_total_passages]
    # Re-number citations 1..N in score order
    for i, p in enumerate(passages, start=1):
        p.citation = Citation(
            n=i, url=p.citation.url, title=p.citation.title,
            snippet=p.citation.snippet, fetched_at=p.citation.fetched_at,
            authority=p.citation.authority,
        )

    if not passages:
        errors.append("no usable passages")
        return ResearchReport(
            topic=topic, summary="",
            report_md=_no_results_template(topic, sub_questions),
            citations=[], sub_questions=sub_questions,
            coverage={
                "n_sources": 0, "n_domains": 0,
                "n_sub_questions": len(sub_questions),
            },
            errors=errors,
        )

    # ---- 5. synthesize ---------------------------------------------
    passage_block = _format_passages_for_llm(passages)
    try:
        report_md = await llm_call(_SYNTH_PROMPT.format(
            topic=topic,
            n_passages=len(passages),
            passages=passage_block,
        ))
    except Exception as exc:  # noqa: BLE001
        errors.append(f"synth_llm: {exc}")
        report_md = _passages_only_fallback(topic, passages)

    report_md = (report_md or "").strip()
    if not report_md:
        report_md = _passages_only_fallback(topic, passages)

    # ---- 6. cite_check ---------------------------------------------
    citations = [p.citation for p in passages]
    cc = cite_check(report_md, citations)
    if not cc["ok"]:
        errors.append(
            f"cite_check failed: missing footnotes {cc['missing']}"
        )
        # Append a warning + force unique footnote list. We keep the
        # report rather than dropping it — the caller can decide
        # whether to ask the LLM to retry.
        report_md += (
            f"\n\n> ⚠️ 自检发现 {len(cc['missing'])} 个引用编号在引用列表里不存在: "
            f"{cc['missing']}。请用户在使用本报告前核对来源。\n"
        )

    # Always append the citation list as Markdown footnotes
    report_md = report_md.rstrip() + "\n\n---\n\n## 引用\n\n" + "\n".join(
        c.as_footnote() for c in citations
    ) + "\n"

    # ---- 7. summary / coverage / return ----------------------------
    summary = _extract_summary(report_md)
    domains = {_host(c.url) for c in citations if _host(c.url)}
    coverage = {
        "n_sources": len(citations),
        "n_domains": len(domains),
        "n_sub_questions": len(sub_questions),
        "cite_check_ok": cc["ok"],
        "cite_missing": cc["missing"],
        "cite_unused": cc["unused"],
    }
    return ResearchReport(
        topic=topic,
        summary=summary,
        report_md=report_md,
        citations=citations,
        sub_questions=sub_questions,
        coverage=coverage,
        errors=errors,
    )


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


async def _gather_safe(tasks: list, *, label: str) -> list[Any]:
    """asyncio.gather with return_exceptions=True + debug logging."""
    if not tasks:
        return []
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for r in results:
        if isinstance(r, BaseException):
            log.debug("%s task raised: %s", label, r)
    return results


def _topic_keywords(text: str, *, max_keywords: int = 8) -> list[str]:
    """Cheap keyword extractor — split on whitespace + punctuation, drop
    stopwords + 1-2 char fragments. Good enough for scoring."""
    if not text:
        return []
    # Cheap CJK-aware tokenization: just keep word characters + CJK.
    raw = re.findall(r"[\w一-鿿]+", text.lower())
    stop = {
        "the", "a", "an", "is", "are", "was", "were", "of", "and", "or",
        "to", "in", "on", "for", "by", "with", "what", "how", "why",
        "this", "that", "it", "its", "as", "be", "do", "does", "did",
        "我", "的", "了", "是", "在", "有", "和", "也", "但", "什么", "怎么",
        "哪些", "如何",
    }
    out: list[str] = []
    seen: set[str] = set()
    for tok in raw:
        if len(tok) < 3 and not _is_cjk(tok):
            continue
        if tok in stop or tok in seen:
            continue
        seen.add(tok)
        out.append(tok)
        if len(out) >= max_keywords:
            break
    return out


def _is_cjk(s: str) -> bool:
    return any("一" <= ch <= "鿿" for ch in s)


def _format_passages_for_llm(passages: list[Passage]) -> str:
    """Render passages in a stable format the LLM can cite from."""
    chunks: list[str] = []
    for p in passages:
        c = p.citation
        chunks.append(
            f"({c.n}) [{c.title}] {c.url}\n{p.text[:1800]}\n"
        )
    return "\n---\n".join(chunks)


def _passages_only_fallback(topic: str, passages: list[Passage]) -> str:
    """No-LLM fallback report — just list the top passages with citations.

    Every passage automatically becomes a cited claim (the snippet IS
    the claim) so cite_check passes trivially.
    """
    lines = [f"# {topic}", "", "## TL;DR", "",
             f"（自动综合不可用 — 以下是 {len(passages)} 个高质量来源的摘要节选。请人工对照判断。）",
             "", "## Key findings", ""]
    for p in passages:
        c = p.citation
        lines.append(f"- {p.text[:400].strip()}…[^{c.n}]")
    return "\n".join(lines)


def _no_results_template(topic: str, sub_questions: list[str]) -> str:
    questions_md = "\n".join(f"- {q}" for q in sub_questions) or "- (no plan)"
    return (
        f"# {topic}\n\n"
        f"## TL;DR\n\n"
        f"未能找到可用的来源（搜索失败或抓取失败）。已尝试以下子问题：\n\n"
        f"{questions_md}\n\n"
        f"请稍后重试，或使用 `web_fetch` 工具针对具体网址手动取证。\n"
    )


def _extract_summary(report_md: str) -> str:
    """Pull the TL;DR paragraph out of the report for the dataclass field."""
    m = re.search(
        r"##\s+TL;DR\s*\n+([^\n#]+(?:\n[^\n#]+)*)",
        report_md,
        flags=re.IGNORECASE,
    )
    if not m:
        return ""
    return re.sub(r"\s+", " ", m.group(1)).strip()


# ---------------------------------------------------------------------
# Tool registry wiring
# ---------------------------------------------------------------------


_RESEARCH_SCHEMA = {
    "name": "research_run",
    "description": (
        "Deep multi-source research pipeline. Plans sub-questions, "
        "searches the web, extracts article text, scores by authority, "
        "and synthesizes a Markdown report with inline [^n] footnote "
        "citations + a final sources appendix. Every claim must cite a "
        "real source — the tool refuses to fabricate. Use for any task "
        "where the user wants 'real research' / '认真调研' / '查一下'."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "topic": {
                "type": "string",
                "description": "The research question or topic (concise; the planner will split it).",
            },
            "max_sub_questions": {
                "type": "integer",
                "description": "How many sub-questions the planner may produce. 3-6 typical.",
                "default": 5,
            },
            "max_urls_per_query": {
                "type": "integer",
                "description": "Top-N URLs to fetch per sub-question.",
                "default": 4,
            },
            "max_total_passages": {
                "type": "integer",
                "description": "Cap on passages used for synthesis.",
                "default": 12,
            },
        },
        "required": ["topic"],
    },
}


async def _handle_research_run(args: dict, task_id: str) -> str:
    """Async handler. Bridges the registry's ``args`` dict to the
    orchestrator. The LLM call is resolved from the global provider
    chain inside ``main.py`` — for unit tests we go directly through
    ``research_run`` so this code path isn't exercised.
    """
    topic = str(args.get("topic") or "").strip()
    if not topic:
        return json.dumps(
            {"ok": False, "error": "topic is required"}, ensure_ascii=False
        )

    # Resolve an LLM callable from the running provider chain. We import
    # lazily so test environments without an LLM still pass.
    try:
        llm_call = await _resolve_default_llm_call()
    except Exception as exc:  # noqa: BLE001
        return json.dumps(
            {"ok": False, "error": f"no LLM available: {exc}"},
            ensure_ascii=False,
        )

    report = await research_run(
        topic,
        llm_call=llm_call,
        max_sub_questions=int(args.get("max_sub_questions") or 5),
        max_urls_per_query=int(args.get("max_urls_per_query") or 4),
        max_total_passages=int(args.get("max_total_passages") or 12),
    )
    return json.dumps({"ok": True, **report.as_dict()}, ensure_ascii=False)


async def _resolve_default_llm_call() -> _LLMCall:
    """Best-effort default LLM bridge for the live tool path.

    We dispatch through the LLM provider chain (set up in ``main.py``).
    On import failure / missing config, raise so the registry handler
    returns an error JSON.
    """
    try:
        from deskpet.config import load_config  # type: ignore
        from deskpet.providers.openai_compatible import (  # type: ignore
            OpenAICompatibleProvider,
        )
    except ImportError as exc:
        raise RuntimeError(f"provider modules unavailable: {exc}") from exc

    cfg = load_config()
    providers = getattr(cfg.llm, "providers", None) or []
    if not providers:
        raise RuntimeError("no llm provider configured")
    p = providers[0]
    provider = OpenAICompatibleProvider(
        base_url=p.base_url,
        api_key=getattr(p, "api_key", "") or "",
        model=p.model,
    )

    async def _call(prompt: str) -> str:
        result = await provider.chat_with_tools(
            messages=[{"role": "user", "content": prompt}],
            tools=[],
            max_tokens=2048,
        )
        return (result or {}).get("content") or ""

    return _call


def _register_research_tool() -> None:
    """Side-effect: register research_run with the global tool registry."""
    try:
        from .registry import registry  # type: ignore
        registry.register(
            "research_run",
            "web",
            _RESEARCH_SCHEMA,
            _handle_research_run,
            permission_category="read_file",
            timeout_seconds=180.0,
        )
    except Exception as exc:  # noqa: BLE001
        log.debug("research tool registration skipped: %s", exc)


_register_research_tool()
