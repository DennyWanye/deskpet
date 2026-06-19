# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Phase-2 中文一手源直连适配器（deep-research）。

绕开搜索引擎,直接打权威数据库 API 拿一手数据,从源头避开搜狐/百家号转帖:

* :func:`cninfo_search` — 巨潮资讯(深交所旗下)``hisAnnouncement/query`` →
  上市公司公告(年报/财报等),下 PDF 用 pypdf 抽正文。中国大陆可直连。
* :func:`openstd_search` — 国家标准全文公开系统 ``openstd.samr.gov.cn`` 搜索 →
  解析标准号/名称(全文在 JS viewer 后,只取元数据)。中国大陆可直连。

两者返回与 research 管线一致的 passage dict ``{url,title,text,fetched_at}``;
命中域名(cninfo.com.cn / openstd.samr.gov.cn=.gov.cn) 天然 TIER_1,后续打分/精排
自然把一手源顶上来。全程 best-effort,任何失败返空 list,绝不抛出。

意图路由由 :func:`direct_source_for` 决定:子问题谈"上市公司/公告/财报"→cninfo;
谈"国标/标准/规范/GB"→openstd;否则 None(只走普通搜索)。
"""
from __future__ import annotations

import io
import logging
import re
import time
from typing import Any, Optional

import httpx

log = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
_TIMEOUT = 18.0

_CNINFO_QUERY = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
_CNINFO_TOPSEARCH = "http://www.cninfo.com.cn/new/information/topSearch/query"
_CNINFO_STATIC = "http://static.cninfo.com.cn/"
_OPENSTD_LIST = "https://openstd.samr.gov.cn/bzgk/gb/std_list"

# SEC EDGAR(美股一手源)。美国站,中国大陆访问时快时慢 → 短超时 + best-effort,
# 连不上返 [] 降级到普通搜索。SEC 要求 UA 带联系方式,否则 403。
_EDGAR_TICKERS = "https://www.sec.gov/files/company_tickers.json"
_EDGAR_CONCEPT = "https://data.sec.gov/api/xbrl/companyconcept/CIK{cik}/us-gaap/{concept}.json"
_EDGAR_FILINGS = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type=10-K"
_EDGAR_UA = "DeskPet-Research/0.6 (contact: deskpet-research@deskpet.app)"
_EDGAR_TIMEOUT = 10.0

_PDF_MAX_PAGES = 12        # 年报常数百页;只抽前 N 页(摘要/要点/主要财务通常在前面)
_PDF_MAX_CHARS = 18_000    # 单篇正文上限,控 token
_PDF_SCAN_PAGES = 40       # 财务类查询找"主要会计数据"表时最多向后扫多少页
# "主要会计数据和财务指标"那张表 = 营收+净利润+总资产+净资产 全在一处,是财报类
# 查询最该抓的页。年报正文里它可能在第 4~8 页(摘要前 1~3 页),用这些标记定位。
_FIN_TABLE_MARKERS = ("主要会计数据和财务指标", "主要会计数据")


# ── 意图路由 ──────────────────────────────────────────────────────────
_CNINFO_KW = ("上市公司", "公告", "财报", "年报", "季报", "半年报", "业绩",
              "营收", "净利润", "招股", "招股书", "问询函", "巨潮", "披露")
_OPENSTD_KW = ("国标", "国家标准", "技术规范", "标准号", "gb/t", "gb ",
               "强制性标准", "推荐性标准", "标准全文")


def direct_source_for(text: str) -> Optional[str]:
    """子问题命中 → "cninfo" / "openstd";否则 None。"""
    t = (text or "").lower()
    if any(k in t for k in _OPENSTD_KW):
        return "openstd"
    if any(k in t for k in _CNINFO_KW):
        return "cninfo"
    return None


def _strip_tags(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s or "").strip()


# 巨潮 searchkey 走标题全文匹配:噪声句("X公司2024年财报营收")命中 0,纯实体名
# ("X公司")命中。下表去掉年报/财报类噪声词 + 年份/数字 → 留公司/实体名。
_CNINFO_NOISE = (
    "年度报告", "年报", "半年报", "季报", "财报", "业绩快报", "业绩预告",
    "业绩", "营收", "营业收入", "净利润", "利润", "公告", "披露", "报告",
    "招股说明书", "招股书", "问询函", "怎么样", "如何", "多少", "情况",
    "的", "和", "与", "及", "了",
)


def _cninfo_category(q: str) -> str:
    """从原始问题判断公告类别 → cninfo category 过滤,显著提升相关性。
    谈年报/财报/营收/净利润 → 年度报告类;否则空(全部公告按时间)。"""
    t = q or ""
    if any(k in t for k in ("年报", "年度报告", "财报", "营收", "营业收入",
                            "净利润", "利润", "业绩")):
        return "category_ndbg_szsh"   # 年度报告(深沪)
    if any(k in t for k in ("半年报", "中报", "半年度")):
        return "category_bndbg_szsh"  # 半年度报告
    if "季报" in t or "一季" in t or "三季" in t:
        return "category_yjdbg_szsh"  # 季度报告
    return ""


def _clean_cninfo_kw(q: str) -> str:
    """把噪声子问题压成实体名(公司名)。压没了就退回原串。"""
    s = q or ""
    for n in _CNINFO_NOISE:
        s = s.replace(n, "")
    s = re.sub(r"\d{4}\s*年?", "", s)   # 年份
    s = re.sub(r"\d+", "", s)            # 残留数字
    s = re.sub(r"\s+", " ", s).strip()
    return s or (q or "").strip()


# LLM 子问题是长句("围绕宁德时代2024年年报数据有哪些争议…"),公司名几乎总在句首。
# 取首部实体: 砍掉句首填充词(围绕/关于/…) → 砍到第一个数字/停止词之前。
_CNINFO_FILLERS = ("围绕", "关于", "分析", "请", "调研", "介绍", "查询",
                   "查一下", "查", "对比", "比较", "梳理", "总结")
_CNINFO_STOP = ("年", "报告", "财报", "营收", "营业", "净利", "利润", "披露",
                "公告", "年度", "季", "半年", "业绩", "有哪些", "如何", "怎么",
                "分业务", "构成", "同比", "数据", "核心", "情况", "的", "及",
                "与", "和", "在", "于")


def _lead_entity(q: str) -> str:
    """从长子问题抽句首公司/实体名(给 topSearch 联想用)。"""
    s = (q or "").strip()
    for f in _CNINFO_FILLERS:
        if s.startswith(f):
            s = s[len(f):]
            break
    m = re.search(r"\d", s)
    if m:
        s = s[:m.start()]
    for w in _CNINFO_STOP:
        i = s.find(w)
        if i > 0:
            s = s[:i]
    return s.strip("的，,。 \t（）()、")


async def _cninfo_resolve(candidate: str, cli: httpx.AsyncClient) -> tuple[str, str]:
    """topSearch 公司联想 → (简称, 'code,orgId')。优先 A股。失败/无果退 (candidate,'')。

    解析到精确 stock 后,用 ``stock=code,orgId`` 查公告比模糊 searchkey 稳得多
    (长句噪声 searchkey 会命中 0)。"""
    cand = (candidate or "").strip()
    if not cand:
        return "", ""
    try:
        r = await cli.post(
            _CNINFO_TOPSEARCH, data={"keyWord": cand, "maxNum": 5},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        arr = r.json()
        if not isinstance(arr, list) or not arr:
            return cand, ""
        best = next((x for x in arr if x.get("category") == "A股"), arr[0])
        zwjc = best.get("zwjc") or cand
        code = best.get("code") or ""
        org = best.get("orgId") or ""
        stock = f"{code},{org}" if code and org else ""
        return zwjc, stock
    except Exception as exc:  # noqa: BLE001
        log.debug("cninfo topSearch failed for %r: %s", cand, exc)
        return cand, ""


# ── 巨潮资讯 ──────────────────────────────────────────────────────────
def _extract_pdf_text(data: bytes, *, want_financials: bool = False) -> str:
    """pypdf 抽 PDF 文本(capped)。失败/无库 → 空串。

    ``want_financials=True``(财报类查询): 先在前 ``_PDF_SCAN_PAGES`` 页里定位
    "主要会计数据和财务指标"那张表(营收+净利润+总资产+净资产 全在这一处),把它放
    正文**最前**,保证不被字数上限截掉 —— 否则年报 300 页里这张表常排在第 4~8 页后,
    8 页上限正好漏掉净利润行(真机核查 BYD 报告暴露的就是这个召回缺口)。"""
    try:
        import pypdf  # type: ignore
    except Exception:  # noqa: BLE001
        return ""
    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001
        log.debug("pypdf open failed: %s", exc)
        return ""
    n = len(reader.pages)
    _cache: dict[int, str] = {}

    def _pg(i: int) -> str:
        if i not in _cache:
            try:
                _cache[i] = reader.pages[i].extract_text() or ""
            except Exception:  # noqa: BLE001
                _cache[i] = ""
        return _cache[i]

    # 财务类: 定位主要会计数据表页 → 置顶(它就是营收+净利润所在表)
    fin_block = ""
    if want_financials:
        for i in range(min(_PDF_SCAN_PAGES, n)):
            t = _pg(i)
            if any(m in t for m in _FIN_TABLE_MARKERS) or (
                "营业收入" in t and "净利润" in t
            ):
                fin_block = "[主要会计数据和财务指标]\n" + t.strip() + "\n\n"
                break

    parts: list[str] = [fin_block] if fin_block else []
    total = len(fin_block)
    for i in range(min(_PDF_MAX_PAGES, n)):
        t = _pg(i)
        parts.append(t)
        total += len(t)
        if total >= _PDF_MAX_CHARS:
            break
    return "\n".join(parts)[:_PDF_MAX_CHARS].strip()


def _rank_cninfo_anns(anns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """财报类: 把"年报摘要"排到正报之前 —— 摘要短、"主要会计数据"表集中在头一两页,
    召回性价比最高(正报数百页 pypdf 只抽得到前部)。摘要里没有的细分项再靠正报兜底。"""
    summ = [a for a in anns if "摘要" in (a.get("announcementTitle") or "")]
    rest = [a for a in anns if "摘要" not in (a.get("announcementTitle") or "")]
    return summ + rest


async def cninfo_search(
    keyword: str, *, max_results: int = 4,
    client: Optional[httpx.AsyncClient] = None,
) -> list[dict[str, Any]]:
    """巨潮公告直连 → passages(含 PDF 抽取正文)。best-effort,失败返 []。"""
    if not (keyword or "").strip():
        return []
    candidate = _lead_entity(keyword) or _clean_cninfo_kw(keyword)
    category = _cninfo_category(keyword)   # 用原始问题判类别(年报/季报/...)
    owns = client is None
    cli = client or httpx.AsyncClient(
        headers={"User-Agent": _UA}, timeout=_TIMEOUT, follow_redirects=True
    )
    try:
        try:
            # 先 topSearch 解析精确公司 → stock=code,orgId 查公告(比模糊 searchkey
            # 稳:长句噪声 searchkey 命中 0)。解析不到则退回 searchkey。
            name, stock = await _cninfo_resolve(candidate, cli)
            data = {"pageNum": 1, "pageSize": max(max_results, 5),
                    "column": "szse", "tabName": "fulltext",
                    "category": category, "seDate": "", "sortName": "",
                    "sortType": "", "isHLtitle": "false"}
            if stock:
                data["stock"] = stock
            else:
                data["searchkey"] = name or candidate
            resp = await cli.post(
                _CNINFO_QUERY, data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            resp.raise_for_status()
            anns = (resp.json() or {}).get("announcements") or []
        except Exception as exc:  # noqa: BLE001
            log.debug("cninfo query failed for %r: %s", candidate, exc)
            return []
        # 财报类(category 命中年报/季报等): 摘要排到正报前(摘要"主要会计数据"表集中,
        # pypdf 召回性价比最高),且抽取时定位财务表置顶。want_fin 控这两条增强。
        want_fin = bool(category)
        if want_fin:
            anns = _rank_cninfo_anns(anns)
        out: list[dict[str, Any]] = []
        for a in anns[:max_results]:
            adj = a.get("adjunctUrl") or ""
            if not adj:
                continue
            pdf_url = _CNINFO_STATIC + adj.lstrip("/")
            sec = _strip_tags(a.get("secName") or "")
            title = _strip_tags(a.get("announcementTitle") or "")
            full_title = (sec + " " + title).strip() or title or sec
            text = ""
            try:
                pr = await cli.get(pdf_url)
                if pr.status_code == 200 and pr.content:
                    text = _extract_pdf_text(pr.content, want_financials=want_fin)
            except Exception as exc:  # noqa: BLE001
                log.debug("cninfo pdf fetch failed %s: %s", pdf_url, exc)
            # PDF 抽不到正文时退化为元数据(公司+标题仍是一手信号)
            if not text:
                text = f"{full_title}（巨潮资讯公告，PDF：{pdf_url}）"
            out.append({
                "ok": True, "url": pdf_url, "title": full_title[:200],
                "text": text, "fetched_at": time.time(), "source": "cninfo",
            })
        return out
    finally:
        if owns:
            await cli.aclose()


# ── 国家标准全文公开系统 ──────────────────────────────────────────────
# openstd std_list 的 p.p2 是关键词匹配:长子问题("这些 GB/T 标准由哪些主管部门…")
# 命中 0,核心技术词("钠离子电池")命中。下面把长子问题压成核心词。
_OPENSTD_FILLERS = ("围绕", "关于", "这些", "截至目前", "截至", "目前", "梳理",
                    "分析", "请", "调研", "查一下", "查", "中国", "我国", "国内")
_OPENSTD_CUT = ("有哪些", "国家标准", "标准号", "强制性标准", "推荐性标准", "标准",
                "规范", "gb/t", "gb ", "由哪些", "仍存在", "相关", "制定", "进展",
                "争议", "技术委员会", "主管部门", "怎么", "如何", "最新", "动态",
                "的", "有")


def _openstd_keyword(q: str) -> str:
    """长子问题 → 核心技术词(给 std_list p.p2 用)。无技术词则返回空(跳过)。"""
    s = (q or "").strip()
    changed = True
    while changed:                       # 循环剥离句首填充词(围绕/中国/截至…)
        changed = False
        for f in _OPENSTD_FILLERS:
            if s.startswith(f):
                s = s[len(f):]
                changed = True
                break
    low = s.lower()
    idxs = [low.find(w) for w in _OPENSTD_CUT if 0 < low.find(w)]
    if idxs:
        s = s[:min(idxs)]
    return s.strip("的，,。 \t（）()、 ")


_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")          # 发布/实施日期 cell
_STATUS_WORDS = ("推标", "国标", "现行", "废止", "即将实施", "查看详细",
                 "查看", "详细", "强标")


def _extract_hcno(row: Any) -> str:
    """从行内 <a> 的 onclick/href 抽内部 hcno(newGbInfo 真正需要的 hash)。"""
    for a in row.css("a"):
        blob = (a.attributes.get("onclick") or "") + " " + (a.attributes.get("href") or "")
        m = (re.search(r"hcno=([0-9A-Za-z]+)", blob)
             or re.search(r"showInfo\w*\(\s*['\"]([0-9A-Za-z]+)", blob))
        if m:
            return m.group(1)
    return ""


def parse_openstd(html: str, *, max_results: int) -> list[dict[str, Any]]:
    """解析 openstd std_list 表格 → [{std_no, name, hcno}]。全文在 JS viewer 后,
    只取元数据(标准号 + 名称 + 内部 hcno)。纯解析,可单测。

    name 选取要排除**日期 cell**(``2024-08-23 00:00:00.0`` 比真名长会误选)和
    状态词(推标/现行/查看详细)。hcno 来自行内 ``<a onclick>``,是 newGbInfo 真正
    需要的 hash;抽不到则退回 std_no(链接退化但元数据仍可用)。
    """
    out: list[dict[str, Any]] = []
    try:
        from selectolax.parser import HTMLParser  # type: ignore
    except ImportError:
        return out
    tree = HTMLParser(html)
    for row in tree.css("table tr"):
        if len(out) >= max_results:
            break
        tds = row.css("td")
        if len(tds) < 3:
            continue
        cells = [(td.text() or "").strip() for td in tds]
        # 标准号形如 GB/T 32895-2016;在某个 cell 里
        num = next((c for c in cells if re.search(r"GB[/ ]?T?\s*\d{3,}", c)), "")
        if not num:
            continue
        # 名称: 排除标准号/日期/状态词/纯数字后,取最长且含中文的 cell
        name = ""
        for c in cells:
            if not c or c == num or c.isdigit():
                continue
            if _DATE_RE.search(c) or c in _STATUS_WORDS:
                continue
            if not re.search(r"[一-鿿]", c):  # 名称必含中文
                continue
            if len(c) > len(name):
                name = c
        out.append({"std_no": num.strip(), "name": name.strip(),
                    "hcno": _extract_hcno(row)})
    return out


async def openstd_search(
    keyword: str, *, max_results: int = 4,
    client: Optional[httpx.AsyncClient] = None,
) -> list[dict[str, Any]]:
    """国标系统搜索 → 标准元数据 passages(标准号+名称)。best-effort,失败返 []。"""
    kw = _openstd_keyword(keyword)   # 长子问题压成核心技术词,否则 std_list 命中 0
    if not kw:
        return []
    owns = client is None
    cli = client or httpx.AsyncClient(
        headers={"User-Agent": _UA}, timeout=_TIMEOUT, follow_redirects=True
    )
    try:
        try:
            resp = await cli.get(_OPENSTD_LIST, params={"p.p1": "2", "p.p2": kw})
            resp.raise_for_status()
            rows = parse_openstd(resp.text, max_results=max_results)
        except Exception as exc:  # noqa: BLE001
            log.debug("openstd search failed for %r: %s", kw, exc)
            return []
        out: list[dict[str, Any]] = []
        for row in rows:
            std_no = row["std_no"]
            name = row["name"] or std_no
            # newGbInfo 需要内部 hcno hash;抽到就用它,抽不到退回标准号(链接退化)。
            hcno = row.get("hcno") or std_no
            text = (f"国家标准 {std_no}：{name}。（来源：国家标准全文公开系统 "
                    f"openstd.samr.gov.cn，全文可在该系统在线查阅。）")
            out.append({
                "ok": True,
                "url": f"https://openstd.samr.gov.cn/bzgk/gb/newGbInfo?hcno={hcno}",
                "title": f"{std_no} {name}"[:200],
                "text": text, "fetched_at": time.time(), "source": "openstd",
            })
        return out
    finally:
        if owns:
            await cli.aclose()


# ── SEC EDGAR(美股一手源,A股直连兜不住时的 fallback)──────────────────
# 常见美股公司中文/别名 → ticker。覆盖中国用户最常问的那批;命不中再靠英文名
# 在 company_tickers.json 的 title 里模糊匹配。
_US_NAME_TICKER = {
    "特斯拉": "TSLA", "tesla": "TSLA", "苹果": "AAPL", "apple": "AAPL",
    "微软": "MSFT", "microsoft": "MSFT", "英伟达": "NVDA", "nvidia": "NVDA",
    "谷歌": "GOOGL", "google": "GOOGL", "alphabet": "GOOGL",
    "亚马逊": "AMZN", "amazon": "AMZN", "脸书": "META", "meta": "META",
    "facebook": "META", "奈飞": "NFLX", "网飞": "NFLX", "netflix": "NFLX",
    "英特尔": "INTC", "intel": "INTC", "高通": "QCOM", "美光": "MU",
    "博通": "AVGO", "甲骨文": "ORCL", "oracle": "ORCL", "思科": "CSCO",
    "可口可乐": "KO", "百事": "PEP", "星巴克": "SBUX", "麦当劳": "MCD",
    "耐克": "NKE", "nike": "NKE", "迪士尼": "DIS", "disney": "DIS",
    "波音": "BA", "boeing": "BA", "沃尔玛": "WMT", "walmart": "WMT",
    "摩根大通": "JPM", "高盛": "GS", "伯克希尔": "BRK-B", "辉瑞": "PFE",
    "强生": "JNJ", "埃克森美孚": "XOM", "雪佛龙": "CVX", "福特": "F",
    "通用汽车": "GM", "超威": "AMD", "amd": "AMD", "派拉蒙": "PARA",
    "优步": "UBER", "uber": "UBER", "爱彼迎": "ABNB", "airbnb": "ABNB",
    "帕兰提尔": "PLTR", "palantir": "PLTR", "礼来": "LLY",
}

# 我们关心的 XBRL 概念(us-gaap) → 中文label。营收两种口径都试。
_EDGAR_CONCEPTS = [
    ("RevenueFromContractWithCustomerExcludingAssessedTax", "营业收入"),
    ("Revenues", "营业收入"),
    ("NetIncomeLoss", "净利润"),
    ("Assets", "总资产"),
    ("StockholdersEquity", "股东权益"),
    ("EarningsPerShareDiluted", "稀释每股收益"),
]

_EDGAR_TICKERS_CACHE: Optional[dict[str, str]] = None   # ticker(大写) → CIK(10位)
_EDGAR_TITLES_CACHE: list[tuple[str, str]] = []          # (title小写, CIK)


def _extract_us_ticker(q: str) -> str:
    """从子问题抽美股 ticker。命中中文/英文别名表优先;否则找显式大写 ticker。"""
    t = (q or "").lower()
    for name, tk in _US_NAME_TICKER.items():
        if name.lower() in t:
            return tk
    # 显式写了 ticker(独立 2-5 大写字母,排除常见非 ticker 词)
    for m in re.findall(r"\b([A-Z]{2,5})\b", q or ""):
        if m not in ("GB", "USD", "CNY", "SEC", "GAAP", "IPO", "CEO", "ETF",
                     "AI", "EV", "HK", "US", "FY", "TLDR", "PDF", "API"):
            return m
    return ""


async def _edgar_load_tickers(cli: httpx.AsyncClient) -> None:
    """拉一次 company_tickers.json 建 ticker→CIK + title→CIK 映射(进程缓存)。"""
    global _EDGAR_TICKERS_CACHE, _EDGAR_TITLES_CACHE
    if _EDGAR_TICKERS_CACHE is not None:
        return
    r = await cli.get(_EDGAR_TICKERS)
    r.raise_for_status()
    j = r.json()
    rows = j.values() if isinstance(j, dict) else j
    tk_map: dict[str, str] = {}
    titles: list[tuple[str, str]] = []
    for row in rows:
        cik = str(row.get("cik_str", "")).zfill(10)
        tk = str(row.get("ticker", "")).upper()
        title = str(row.get("title", "")).lower()
        if tk:
            tk_map.setdefault(tk, cik)
        if title:
            titles.append((title, cik))
    _EDGAR_TICKERS_CACHE = tk_map
    _EDGAR_TITLES_CACHE = titles


async def _edgar_resolve_cik(keyword: str, cli: httpx.AsyncClient) -> tuple[str, str]:
    """(CIK, ticker)。解析不到返 ("","")。"""
    tk = _extract_us_ticker(keyword)
    await _edgar_load_tickers(cli)
    cache = _EDGAR_TICKERS_CACHE or {}
    if tk and tk in cache:
        return cache[tk], tk
    # 英文名在 title 里模糊匹配(取最短匹配,避免命中超长子公司名)
    low = (keyword or "").lower()
    best: tuple[str, str] = ("", "")
    for title, cik in _EDGAR_TITLES_CACHE:
        head = title.split(",")[0].split(" inc")[0].strip()
        if len(head) >= 3 and head in low:
            if not best[0] or len(head) > len(best[1]):
                best = (cik, head)
    return (best[0], tk or "") if best[0] else ("", "")


def _edgar_recent_fys(units: list[dict[str, Any]], n: int = 3) -> list[dict[str, Any]]:
    """从 concept 的 USD units 里取最近 n 个**不同财年**的年报(10-K, 全年 FY)值,
    新→旧。返回多年是为了:① 用户问的具体年份(如 2024)一定在内,不被"最新年报"挤掉;
    ② 顺带给同比趋势。"""
    fy = [u for u in units
          if u.get("form") in ("10-K", "10-K/A") and u.get("fp") == "FY"
          and u.get("val") is not None]
    fy.sort(key=lambda u: str(u.get("end", "")), reverse=True)
    out: list[dict[str, Any]] = []
    seen_years: set[str] = set()
    for u in fy:
        yr = str(u.get("fy") or str(u.get("end", ""))[:4])
        if yr in seen_years:
            continue
        seen_years.add(yr)
        out.append(u)
        if len(out) >= n:
            break
    return out


async def _edgar_fetch(keyword: str, cli: httpx.AsyncClient,
                       max_results: int) -> list[dict[str, Any]]:
    """单次尝试(给定 client)。解析不到/无数据返 []。异常向上抛(由 edgar_search 切换网络)。"""
    cik, tk = await _edgar_resolve_cik(keyword, cli)
    if not cik:
        return []
    company = ""
    lines: list[str] = []
    seen_labels: set[str] = set()
    for concept, label in _EDGAR_CONCEPTS:
        if label in seen_labels:
            continue
        try:
            r = await cli.get(_EDGAR_CONCEPT.format(cik=cik, concept=concept))
            if r.status_code != 200:
                continue
            j = r.json()
            company = company or str(j.get("entityName") or "")
            units = (j.get("units") or {}).get("USD") or []
        except Exception as exc:  # noqa: BLE001
            log.debug("edgar concept %s failed: %s", concept, exc)
            continue
        recent = _edgar_recent_fys(units, n=3)
        if not recent:
            continue
        seen_labels.add(label)
        vals = "; ".join(
            f"FY{u.get('fy') or str(u.get('end',''))[:4]} {u['val']:,}" for u in recent
        )
        lines.append(f"- {label}（美元）: {vals}")
    if not lines:
        return []
    title = f"{company or tk or keyword} SEC 10-K 主要财务数据"
    text = (
        f"{title}（来源：美国证券交易委员会 SEC EDGAR 官方 XBRL 披露，"
        f"取自年报 Form 10-K，单位美元）：\n" + "\n".join(lines) +
        "\n（数据来自 SEC 官方结构化披露，为一手权威来源。）"
    )
    return [{
        "ok": True, "url": _EDGAR_FILINGS.format(cik=cik),
        "title": title[:200], "text": text,
        "fetched_at": time.time(), "source": "edgar",
    }][:max_results]


async def edgar_search(
    keyword: str, *, max_results: int = 1,
    client: Optional[httpx.AsyncClient] = None,
) -> list[dict[str, Any]]:
    """SEC EDGAR 美股一手财务直连 → 1 条结构化 passage(营收/净利润/总资产等,
    取自 XBRL companyconcept,逐位官方数据)。best-effort:连不上/解析不到返 []
    (降级到普通搜索)。SEC 是美国站,中国访问可能慢/不通 → **直连与系统代理双试**,
    两者都不行就降级。UA 必须带联系方式否则 403。"""
    if not (keyword or "").strip():
        return []
    if client is not None:                       # 测试注入: 单次,不切网络
        return await _edgar_fetch(keyword, client, max_results)
    headers = {"User-Agent": _EDGAR_UA, "Accept-Encoding": "gzip, deflate"}
    # 直连优先(实测更快),失败再走系统代理(GFW 后用户靠代理)。
    for trust_env in (False, True):
        cli = httpx.AsyncClient(headers=headers, timeout=_EDGAR_TIMEOUT,
                                follow_redirects=True, trust_env=trust_env)
        try:
            return await _edgar_fetch(keyword, cli, max_results)
        except Exception as exc:  # noqa: BLE001 — ConnectError/403/超时 → 试下一种网络
            log.debug("edgar attempt trust_env=%s failed: %s", trust_env, exc)
        finally:
            await cli.aclose()
    return []


__all__ = ["direct_source_for", "cninfo_search", "openstd_search",
           "parse_openstd", "edgar_search"]
