# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""PPT 视觉评估 — 把每页渲染图喂给多模态 LLM「亲眼看」,返回结构化问题与修复动作。

视觉闭环(问题1)的「眼睛+大脑」: ppt_create 生成 → 渲染 PNG → 本模块
review_slides() 让 gpt-5.5 看图评审(文字溢出/截断/压主体/对比度/版式
合适度) → ppt_tools._apply_review_actions 应用可自动修的动作 → 重渲染。

设计约束:
- 同步阻塞(跑在 ppt_create 所在 executor 线程;异步图文任务本就在后台)。
- 复用 image_tools 的 endpoint/key 解析与 trust_env 直连(同一 relay)。
- 从不抛异常: vision 不可用/解析失败 → 返回 [](调用方跳过闭环,主流程零影响)。
- 成本可控: 图缩到宽 768 再 base64;一次评审 = 1 个 vision 调用。
"""
from __future__ import annotations

import base64
import json
import logging
import re
from io import BytesIO
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_REVIEW_CHECKS = """你是严格的 PPT 视觉质检员。逐页检查幻灯片渲染截图,只关注【视觉/排版】问题:
1. overflow: 文字溢出容器、被截断、折行成孤字、文字挤成一团
2. occlusion: 文字与装饰元素/图片重叠错位,或对比度不足难以阅读
3. misfit: 版式与内容不匹配(如要点太多挤爆容器、内容放错位置)
4. blank: 大面积异常空白/元素缺失"""

_REVIEW_SYSTEM = _REVIEW_CHECKS + """

每页给出判定与【修复动作】(只能从下面选):
- "ok": 本页合格
- "change_variant": 换版式,同时给 "variant" 字段,取值 cover/split_left/split_right/top/card/quote
- "shrink_text": 文字过大/溢出 → 缩小该页字号

输出严格 JSON 数组,不要任何其它文字:
[{"page":1,"ok":true,"issues":[],"action":"ok"},
 {"page":2,"ok":false,"issues":["标题压在人脸上"],"action":"change_variant","variant":"split_left"}]"""

_REVIEW_SYSTEM_TEMPLATE = _REVIEW_CHECKS + """

这是【模板填充】的 PPT(设计页来自现成模板,内容是填进去的)。每页给出判定与【修复动作】(只能从下面选):
- "ok": 本页合格
- "shrink_text": 文字溢出/挤爆容器 → 缩小该页填充文字字号
- "change_page": 文字与设计元素重叠错位/排版结构性混乱(缩字救不了) → 给本页换一张设计页重新填充

输出严格 JSON 数组,不要任何其它文字:
[{"page":1,"ok":true,"issues":[],"action":"ok"},
 {"page":2,"ok":false,"issues":["目录数字与文字重叠"],"action":"change_page"}]"""


def _b64_image(path: str, *, max_w: int = 768) -> str | None:
    """读图→缩到 max_w→JPEG base64(控 token)。失败 None。"""
    try:
        from PIL import Image  # type: ignore

        with Image.open(path) as im:
            im = im.convert("RGB")
            if im.width > max_w:
                im = im.resize((max_w, int(im.height * max_w / im.width)))
            buf = BytesIO()
            im.save(buf, format="JPEG", quality=80)
        return base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception as exc:  # noqa: BLE001
        log.debug("b64 image failed %s: %s", path, exc)
        return None


def _parse_review_json(text: str) -> list[dict[str, Any]]:
    """LLM 输出 → 评审列表。容忍 ```json 围栏/前后杂文。失败 []。"""
    try:
        m = re.search(r"\[.*\]", text, re.DOTALL)
        if not m:
            return []
        data = json.loads(m.group(0))
        if not isinstance(data, list):
            return []
        out = []
        for item in data:
            if not isinstance(item, dict):
                continue
            out.append({
                "page": int(item.get("page") or 0),
                "ok": bool(item.get("ok", True)),
                "issues": [str(x) for x in (item.get("issues") or [])][:5],
                "action": str(item.get("action") or "ok"),
                "variant": str(item.get("variant") or ""),
            })
        return out
    except Exception as exc:  # noqa: BLE001
        log.debug("parse review json failed: %s", exc)
        return []


def review_slides(
    png_paths: list[str],
    pages_meta: list[dict[str, Any]],
    *,
    mode: str = "image",
    max_pages: int = 12,
    timeout: float = 120.0,
) -> list[dict[str, Any]]:
    """让多模态 LLM 看每页截图,返回评审动作列表。

    pages_meta: 每页 {"title": ..., "variant": ..., "n_bullets": ...}
    (给模型上下文,judge 版式是否匹配内容)。

    返回 [] = 评审不可用(无 key/vision 不支持/解析失败),调用方直接跳过。
    从不抛异常。
    """
    try:
        from .image_tools import _resolve_endpoint, _trust_env_proxy  # type: ignore

        base_url, api_key = _resolve_endpoint()
        if not base_url or not api_key:
            log.info("visual review skipped: no endpoint/key")
            return []

        import httpx

        content: list[dict[str, Any]] = []
        n = min(len(png_paths), max_pages)
        for i in range(n):
            b64 = _b64_image(png_paths[i])
            if not b64:
                continue
            meta = pages_meta[i] if i < len(pages_meta) else {}
            content.append({
                "type": "text",
                "text": (
                    f"第 {i + 1} 页 | 版式={meta.get('variant', '?')} | "
                    f"标题={meta.get('title', '')!r} | 要点数={meta.get('n_bullets', 0)}"
                ),
            })
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
            })
        if not content:
            return []
        content.append({
            "type": "text",
            "text": f"共 {n} 页。请逐页质检并按规定格式输出 JSON 数组。",
        })

        # P-B 修复: 原写法 `_cfg.config.raw` 恒 AttributeError(config 模块无 config 属性,
        # 同 TC-P2-05 坑)→ 被 except 吞 → 一直回落 hardcode "gpt-5.5",换模型时读不到有效值。
        # 改走 standalone 访问器(直读 llm_runtime.json 的有效出站模型,不依赖 main 单例)。
        model = "gpt-5.5"
        try:
            from config import effective_llm_model_standalone  # type: ignore
            model = effective_llm_model_standalone() or model
        except Exception:  # noqa: BLE001
            pass

        system_prompt = (
            _REVIEW_SYSTEM_TEMPLATE if mode == "template" else _REVIEW_SYSTEM
        )
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content},
            ],
            "max_tokens": 1500,
            "temperature": 0.0,
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        with httpx.Client(
            timeout=httpx.Timeout(connect=10.0, read=timeout, write=30.0, pool=30.0),
            trust_env=_trust_env_proxy(),
        ) as cli:
            resp = cli.post(f"{base_url}/chat/completions", json=payload, headers=headers)
        if resp.status_code != 200:
            log.warning("visual review HTTP %d: %s", resp.status_code, resp.text[:200])
            return []
        text = (
            (resp.json().get("choices") or [{}])[0]
            .get("message", {})
            .get("content", "")
        ) or ""
        reviews = _parse_review_json(text)
        log.info(
            "visual_review done pages=%d issues=%d",
            len(reviews),
            sum(1 for r in reviews if not r.get("ok", True)),
        )
        return reviews
    except Exception as exc:  # noqa: BLE001
        log.warning("visual review failed: %s", str(exc)[:200])
        return []
