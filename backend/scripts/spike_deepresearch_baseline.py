# SPDX-License-Identifier: BUSL-1.1
"""Phase 0 — DeepResearch 质量基线 spike（一次性度量脚本，不进产线）。

按 plans/deepresearch-upgrade/00-upgrade-plan.md §3 执行：
- 代表性主题集（routing-evals should_trigger 6 个 + 1 财报）跑真 LLM deepresearch；
- standard 全跑，deep 跑子集做 deep-vs-standard 对比；
- 注入主 LLM + 廉价精排（产线等价管线）；
- 每份报告用 evaluator-prompt 评分卡（LLM-judge，5 维加权）打分；
- 采集耗时/来源/域名/cite_check/rounds/velocity/errors/coverage 新字段；
- 聚合出阈值判定 + 瓶颈归类，写 01-baseline-spike-report.md + 原始 JSON。

运行：DESKPET_CLOUD_API_KEY 须在环境（Tauri 注入口径），cwd=backend。
"""
from __future__ import annotations
import asyncio
import json
import os
import sys
import time
import traceback

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from config import load_config, resolve_cloud_api_key  # type: ignore
from providers.openai_compatible import OpenAICompatibleProvider  # type: ignore
from deskpet.tools import research_tools as rt  # type: ignore
from deskpet.tools.research_tools import deepresearch, _DEPTH_PRESETS  # type: ignore

OUT_DIR = os.path.join(_BACKEND, "..", "plans", "deepresearch-upgrade")

# 代表性主题集（plan §3：routing-evals should_trigger + 财报）
# run2：重跑 run1 因搜索限流(no search results)失败的 5 主题，主题间加延迟让限流恢复。
DELAY_S = float(os.environ.get("SPIKE_DELAY_S", "0"))
TOPICS = [
    ("rag-eval", "RAG 评估方法的综述报告，要有来源、有局限性", ["standard"]),
    ("oai-vs-anthropic", "比较 OpenAI 和 Anthropic 在 agent skills 上的思路，带引用分析", ["standard"]),
    ("cs-ai-vendors", "竞品研究：对比 5 家客服 AI 厂商的定位、能力、局限", ["standard"]),
    ("browser-use", "深度研究 AI browser use 的真实落地边界，别只讲 demo", ["standard"]),
    ("catl-2024", "宁德时代 2024 年财报的关键经营数据（营收/净利/研发/海外占比）", ["standard"]),
]

JUDGE_PROMPT = """你是独立的研究产出评审，像挑剔的编辑，不是啦啦队。只输出 JSON，不要任何多余文字。

评审维度（各 1-10）：
- evidence: 证据与 grounding（核心事实是否有引用、引用是否贴切、强结论是否有强证据）
- synthesis: 综合质量（是否超越拼接摘要、有无真正的模式/权衡/取舍）
- coverage: 覆盖与局限（是否覆盖请求主体、有无真实的局限/取舍小节）
- coherence: 连贯与可用（结构是否清晰、结论是否回答了问题）
- calibration: 校准与洞察（信心是否与证据匹配、有无 ≥1 个决策相关的非显然洞察）
composite = evidence*0.30 + synthesis*0.20 + coverage*0.20 + coherence*0.15 + calibration*0.15
verdict = "PASS" 当 composite >= 6 否则 "FAIL"

研究问题：{question}
档位：{mode}

报告正文：
{draft}

只输出 JSON：{{"evidence":N,"synthesis":N,"coverage":N,"coherence":N,"calibration":N,"composite":N.N,"verdict":"PASS|FAIL","one_line":"一句话评语"}}"""


_PLACEHOLDERS = {"ollama", "from-keychain", "from-env", "", "your-key-here"}


def _build_providers():
    """镜像 main.py:184-257 的统一 [llm] 口径：config.llm.local 被 llm_runtime.json
    覆盖为 relay；key 走 placeholder→keychain(env DESKPET_CLOUD_API_KEY)。"""
    config = load_config()
    # 1) 应用 llm_runtime.json 覆盖（mirror main.py:184-213）
    rt_path = os.path.join(os.environ.get("APPDATA", ""), "deskpet", "llm_runtime.json")
    ov = {}
    try:
        with open(rt_path, encoding="utf-8") as f:
            ov = json.load(f)
    except Exception:  # noqa: BLE001
        ov = {}
    if ov.get("base_url"):
        config.llm.local.base_url = ov["base_url"]
    if ov.get("model"):
        config.llm.local.model = ov["model"]
    if ov.get("temperature") is not None:
        config.llm.local.temperature = float(ov["temperature"])
    if ov.get("api_key"):
        config.llm.local.api_key = ov["api_key"]
    # 2) 解析 api_key（placeholder → keychain via env）
    api_key = config.llm.local.api_key
    if api_key in _PLACEHOLDERS:
        api_key = resolve_cloud_api_key() or api_key
    base_url = config.llm.local.base_url
    model = config.llm.local.model
    if "localhost" in base_url or "127.0.0.1" in base_url:
        raise SystemExit(f"endpoint 仍是本地 ({base_url}) — llm_runtime.json 覆盖未生效，拒绝跑（要 relay 真基线）")
    if not api_key or api_key in _PLACEHOLDERS:
        raise SystemExit("api_key 未解析到（DESKPET_CLOUD_API_KEY 未注入？）")
    main = OpenAICompatibleProvider(base_url=base_url, api_key=api_key, model=model)
    rerank = OpenAICompatibleProvider(base_url=base_url, api_key=api_key, model="gpt-4.1-mini")
    print(f"[spike] endpoint={base_url} model={model}", flush=True)
    return main, rerank, model


async def _wrap(provider):
    async def _call(prompt: str) -> str:
        out = await provider.chat_with_tools(
            messages=[{"role": "user", "content": prompt}], tools=[], max_tokens=4096
        )
        return (out or {}).get("content") or ""
    return _call


async def _judge(llm_call, question: str, mode: str, draft: str) -> dict:
    try:
        raw = await llm_call(JUDGE_PROMPT.format(question=question, mode=mode, draft=draft[:12000]))
        s = raw.strip()
        i, j = s.find("{"), s.rfind("}")
        return json.loads(s[i : j + 1]) if i >= 0 and j > i else {"error": "judge parse", "raw": s[:200]}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"judge: {exc}"}


async def main() -> None:
    main_p, rerank_p, model_id = _build_providers()
    llm_call = await _wrap(main_p)
    rerank_call = await _wrap(rerank_p)
    rt.set_rerank_llm_call(rerank_call)  # 开精排 = 产线等价

    results = []
    for tid, topic, depths in TOPICS:
        for mode in depths:
            sub_q, urls, passages, rounds = _DEPTH_PRESETS[mode]
            print(f"\n===== {tid} [{mode}] =====\n{topic}", flush=True)
            rec = {"id": tid, "mode": mode, "topic": topic}
            t0 = time.perf_counter()
            try:
                report = await deepresearch(
                    topic, llm_call=llm_call, max_sub_questions=sub_q,
                    max_urls_per_query=urls, max_total_passages=passages,
                    max_rounds=rounds, mode=mode,
                )
                elapsed = time.perf_counter() - t0
                cov = report.coverage or {}
                rec.update({
                    "elapsed_s": round(elapsed, 1),
                    "n_sources": cov.get("n_sources"),
                    "n_domains": cov.get("n_domains"),
                    "rounds": cov.get("rounds"),
                    "velocity": cov.get("topic_velocity"),
                    "reranker": cov.get("reranker"),
                    "route": cov.get("route"),
                    "n_dropped_by_reason": cov.get("n_dropped_by_reason"),
                    "elapsed_ms_per_stage": cov.get("elapsed_ms_per_stage"),
                    "n_citations": len(report.citations or []),
                    "cite_ok": "cite_check failed" not in " ".join(report.errors or []),
                    "errors": (report.errors or [])[:8],
                    "report_chars": len(report.report_md or ""),
                })
                rec["judge"] = await _judge(llm_call, topic, mode, report.report_md or "")
            except Exception as exc:  # noqa: BLE001
                rec.update({"elapsed_s": round(time.perf_counter() - t0, 1),
                            "fatal": f"{exc}", "trace": traceback.format_exc()[-600:]})
            results.append(rec)
            print(json.dumps({k: rec.get(k) for k in ("elapsed_s", "n_sources", "n_domains", "cite_ok", "judge")},
                             ensure_ascii=False), flush=True)
        if DELAY_S > 0:
            print(f"[spike] sleep {DELAY_S}s (避免搜索限流)", flush=True)
            await asyncio.sleep(DELAY_S)

    os.makedirs(OUT_DIR, exist_ok=True)
    raw_name = os.environ.get("SPIKE_RAW", "01-baseline-spike-raw.json")
    with open(os.path.join(OUT_DIR, raw_name), "w", encoding="utf-8") as f:
        json.dump({"model": model_id, "results": results}, f, ensure_ascii=False, indent=2)
    _write_report(model_id, results)
    print("\n=== DONE. raw -> 01-baseline-spike-raw.json, report -> 01-baseline-spike-report.md ===", flush=True)


def _med(xs):
    xs = sorted([x for x in xs if x is not None])
    return None if not xs else (xs[len(xs) // 2] if len(xs) % 2 else (xs[len(xs) // 2 - 1] + xs[len(xs) // 2]) / 2)


def _write_report(model_id, results):
    ok = [r for r in results if "fatal" not in r and isinstance(r.get("judge"), dict) and "composite" in r["judge"]]
    std = [r for r in ok if r["mode"] == "standard"]
    comps = [r["judge"]["composite"] for r in ok]
    std_comps = [r["judge"]["composite"] for r in std]
    cite_rate = (sum(1 for r in ok if r.get("cite_ok")) / len(ok)) if ok else 0
    # deep-vs-standard：同主题对比
    pairs = []
    for tid in set(r["id"] for r in results):
        s = next((r for r in ok if r["id"] == tid and r["mode"] == "standard"), None)
        d = next((r for r in ok if r["id"] == tid and r["mode"] == "deep"), None)
        if s and d:
            gain = d["judge"]["composite"] - s["judge"]["composite"]
            ratio = (d["elapsed_s"] / s["elapsed_s"]) if s["elapsed_s"] else None
            pairs.append((tid, gain, ratio, s["elapsed_s"], d["elapsed_s"]))
    median_std = _med(std_comps)
    lines = []
    lines.append("# DeepResearch 质量基线 spike 报告（Phase 0）\n")
    lines.append(f"> 日期 2026-06-20 · 模型 `{model_id}`（精排 gpt-4.1-mini）· 真 LLM 真链路 · {len(results)} 次运行\n")
    lines.append("## 1. 逐主题结果\n")
    lines.append("| id | 档 | 耗时s | 源/域 | 引用 | cite | 质量(comp) | verdict | 一句话 |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for r in results:
        j = r.get("judge") or {}
        if "fatal" in r:
            lines.append(f"| {r['id']} | {r['mode']} | {r.get('elapsed_s')} | — | — | — | FATAL | — | {r.get('fatal','')[:40]} |")
        else:
            lines.append(f"| {r['id']} | {r['mode']} | {r.get('elapsed_s')} | {r.get('n_sources')}/{r.get('n_domains')} "
                         f"| {r.get('n_citations')} | {'✓' if r.get('cite_ok') else '✗'} | {j.get('composite','?')} "
                         f"| {j.get('verdict','?')} | {str(j.get('one_line',''))[:36]} |")
    lines.append("\n## 2. 聚合 + 阈值判定（plan §3 验收门）\n")
    lines.append(f"- **基线质量**：standard 档 composite 中位数 = **{median_std}** / 10（门槛 ≥6.0 → "
                 f"{'PASS' if (median_std or 0) >= 6 else 'FAIL'}）")
    lines.append(f"- **cite-check 通过率** = **{cite_rate:.0%}**（门槛 ≥90% → {'PASS' if cite_rate >= 0.9 else 'FAIL'}）")
    lines.append(f"- 全档 composite 中位数 = {_med(comps)}；运行成功 {len(ok)}/{len(results)}")
    lines.append("\n**deep vs standard（同主题）**：")
    if pairs:
        for tid, gain, ratio, se, de in pairs:
            verdict = "deep 不值（增益<0.6 且耗时>2×）" if (gain < 0.6 and ratio and ratio > 2) else "deep 有增量"
            lines.append(f"- {tid}: 质量增益 {gain:+.1f}，耗时 {se}s→{de}s（{ratio:.1f}×）→ {verdict}")
    else:
        lines.append("- 无成功的 deep/standard 配对")
    lines.append("\n## 3. 瓶颈归类（决定 Phase 3 取舍）\n")
    avg_src = _med([r.get("n_sources") for r in ok])
    lines.append(f"- 平均来源数 ≈ {avg_src}；逐主题 errors 见原始 JSON。")
    lines.append("- 归类规则：失败/低分主要源于「召回差/源质量低」→ 瓶颈在**检索**（Phase3 reranker/fetch）；"
                 "源 OK 但综合差 → 瓶颈在**综合**（改 synth/分离 synthesis）。**结论见下方人工判读。**")
    lines.append("\n（人工判读：对照 §1 表里 evidence vs synthesis 维度哪个系统性偏低 + errors 分布填写。）")
    with open(os.path.join(OUT_DIR, "01-baseline-spike-report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    asyncio.run(main())
