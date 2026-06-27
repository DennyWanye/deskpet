# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""PPT 模板库 — 结构原语 + 预览图视觉选模板。

模板源不再是 git 跟踪的少数精选,而是外部大库
``<repo>/resources/PPT_Template``(env ``DESKPET_PPT_TEMPLATE_ROOT`` 可覆盖):

    <root>/
      01 高级色/        (*.pptx)  + 预览图/(*.jpg)
      02 高级简约/      (*.pptx)  + 预览图/(*.png)

每个 ``.pptx`` 是一套完整设计 deck(纯设计页,无 placeholder),由
``ppt_tools._render_with_design_pages`` 填充。每套自带同 stem 的预览图。

因为模板数以百计,没法塞进 LLM 工具 schema 让其按名精确选。改为:
LLM 只挑一个**大类**,引擎把该类预览图拼成网格(contact sheet),用一次
多模态调用(复用 ``ppt_visual_review.vision_chat``)按主题挑出最贴的一套。

设计约束:**从不抛异常**。库不存在/无预览/vision 不可用/解析失败 → 随机
回退该类一套;该类为空 → 返回 None(上层回落 from-scratch 引擎)。
"""
from __future__ import annotations

import logging
import os
import random
import re
import tempfile
from io import BytesIO
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)

_PREVIEW_DIRNAME = "预览图"
_PREVIEW_EXTS = (".jpg", ".jpeg", ".png", ".webp")

# 大类风格描述(给 LLM 按主题选)。键 = 规范化后的大类名(去数字前缀)。
# 库里多一个大类时这里没有也不报错 —— 描述缺省回退空串。
CATEGORY_STYLE_HINTS = {
    "高级色": "彩色高级感、视觉冲击强、配色饱满。适合科技/商业/产品发布/营销/通用职场汇报。",
    "高级简约": "极简留白、克制高级、以排版取胜。适合设计/品牌/方案/学术/高端通用场合。",
    "通用商务": "通用商务风、专业稳妥。外部大库不可用时的兜底,适合各类职场汇报/工作总结/计划。",
}


# ---------------------------------------------------------------------
# 库结构原语
# ---------------------------------------------------------------------
def _has_category_subdir(root: Path) -> bool:
    """``root`` 下是否有至少一个含 .pptx 的子目录(= 一个大类)。"""
    try:
        for sub in root.iterdir():
            if sub.is_dir() and any(sub.glob("*.pptx")):
                return True
    except Exception:  # noqa: BLE001
        return False
    return False


def _external_root() -> Optional[Path]:
    """外部大库根。env ``DESKPET_PPT_TEMPLATE_ROOT`` 优先(无效则忽略),
    否则 ``<repo>/resources/PPT_Template``。不存在 → None。"""
    try:
        env = (os.environ.get("DESKPET_PPT_TEMPLATE_ROOT", "") or "").strip()
        if env:
            root = Path(env).expanduser()
            return root if root.is_dir() else None
        # 本文件: <repo>/backend/deskpet/tools/ppt_template_picker.py
        repo = Path(__file__).resolve().parents[3]
        root = repo / "resources" / "PPT_Template"
        return root if root.is_dir() else None
    except Exception:  # noqa: BLE001
        return None


def _bundled_fallback_root() -> Optional[Path]:
    """随仓库提交的兜底模板根(``<this_dir>/ppt_templates``)。外部大库不可用时
    (打包 app / 新机器 / 未放置大库)用它保证模板功能不彻底失效。"""
    try:
        root = Path(__file__).parent / "ppt_templates"
        return root if (root.is_dir() and _has_category_subdir(root)) else None
    except Exception:  # noqa: BLE001
        return None


def template_library_root() -> Optional[Path]:
    """生效的模板库根:外部大库优先,缺失则回退到 bundled 兜底库。
    都没有 → None。从不抛异常。"""
    return _external_root() or _bundled_fallback_root()


def _normalize_category(name: str) -> str:
    """目录名 → 干净大类名: 去掉前导数字编号与空白。``01 高级色`` → ``高级色``。"""
    return re.sub(r"^\s*\d+\s*[\.\-_、]?\s*", "", str(name or "")).strip()


def list_categories() -> list[dict[str, Any]]:
    """列出库根下含 .pptx 的大类。返回 ``[{key, dir, count}]``,key 已规范化。
    库不存在 → []。从不抛异常。"""
    root = template_library_root()
    if root is None:
        return []
    out: list[dict[str, Any]] = []
    try:
        for sub in sorted(root.iterdir()):
            if not sub.is_dir():
                continue
            count = sum(1 for _ in sub.glob("*.pptx"))
            if count <= 0:
                continue
            out.append({"key": _normalize_category(sub.name), "dir": sub, "count": count})
    except Exception as exc:  # noqa: BLE001
        log.debug("list_categories failed: %s", exc)
    return out


def _resolve_category_dir(category_key: str) -> Optional[Path]:
    """按规范化大类名找目录(传原始目录名也认)。找不到 → None。"""
    want = _normalize_category(category_key)
    for cat in list_categories():
        if cat["key"] == want:
            return cat["dir"]  # type: ignore[return-value]
    # 容忍直接传原始目录名
    root = template_library_root()
    if root is not None:
        direct = root / str(category_key or "")
        if direct.is_dir() and any(direct.glob("*.pptx")):
            return direct
    return None


def category_pptx(category_key: str) -> list[Path]:
    """某大类下全部 .pptx(排序)。找不到大类 → []。从不抛异常。"""
    cat_dir = _resolve_category_dir(category_key)
    if cat_dir is None:
        return []
    try:
        return sorted(cat_dir.glob("*.pptx"))
    except Exception:  # noqa: BLE001
        return []


def preview_for(pptx_path: Path) -> Optional[Path]:
    """按 stem 在同大类 ``预览图/`` 下找预览图(.jpg/.png/...)。无 → None。"""
    try:
        preview_dir = pptx_path.parent / _PREVIEW_DIRNAME
        if not preview_dir.is_dir():
            return None
        stem = pptx_path.stem
        for ext in _PREVIEW_EXTS:
            cand = preview_dir / f"{stem}{ext}"
            if cand.is_file():
                return cand
    except Exception:  # noqa: BLE001
        return None
    return None


def is_category(name: Optional[str]) -> bool:
    """``name`` 是否是一个已知大类(规范化后命中)。"""
    if not name:
        return False
    return _resolve_category_dir(str(name)) is not None


# ---------------------------------------------------------------------
# 预览图 contact sheet + 视觉选模板
# ---------------------------------------------------------------------
def _load_font(size: int):
    """尽量取一个可读字体,失败回退 PIL 默认。"""
    from PIL import ImageFont  # type: ignore

    for name in ("arial.ttf", "DejaVuSans.ttf", "msyh.ttc", "simhei.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:  # noqa: BLE001
            continue
    try:
        return ImageFont.load_default()
    except Exception:  # noqa: BLE001
        return None


def _build_contact_sheets(
    pairs: list[tuple[int, Path]],
    *,
    cols: int = 5,
    rows: int = 6,
    cell_w: int = 220,
    cell_h: int = 150,
) -> list[Path]:
    """把 ``(id, preview_path)`` 拼成网格图,每格左上角标 id。返回拼好的图路径列表。
    缓存到临时目录,内容签名命中则复用。失败返回 []。从不抛异常。"""
    try:
        from PIL import Image, ImageDraw  # type: ignore

        per_sheet = cols * rows
        n_sheets = (len(pairs) + per_sheet - 1) // per_sheet
        cache_dir = Path(tempfile.gettempdir()) / "deskpet-ppt-montage"
        cache_dir.mkdir(parents=True, exist_ok=True)
        sig = "_".join(str(i) for i, _ in pairs[:per_sheet * n_sheets])
        sig = str(abs(hash((cols, rows, cell_w, cell_h, sig))))
        font = _load_font(26)

        sheet_paths: list[Path] = []
        pad = 6
        sheet_w = cols * cell_w
        sheet_h = rows * cell_h
        for s in range(n_sheets):
            cache = cache_dir / f"sheet_{sig}_{s}.jpg"
            if cache.is_file():
                sheet_paths.append(cache)
                continue
            sheet = Image.new("RGB", (sheet_w, sheet_h), (245, 245, 247))
            draw = ImageDraw.Draw(sheet)
            chunk = pairs[s * per_sheet:(s + 1) * per_sheet]
            for idx, (pid, ppath) in enumerate(chunk):
                r, c = divmod(idx, cols)
                x0, y0 = c * cell_w, r * cell_h
                try:
                    with Image.open(ppath) as im:
                        im = im.convert("RGB")
                        tw, th = cell_w - 2 * pad, cell_h - 2 * pad
                        im.thumbnail((tw, th))
                        ox = x0 + (cell_w - im.width) // 2
                        oy = y0 + (cell_h - im.height) // 2
                        sheet.paste(im, (ox, oy))
                except Exception:  # noqa: BLE001
                    pass
                # id 角标(实心底 + 文字),保证可读
                label = str(pid)
                draw.rectangle([x0 + 2, y0 + 2, x0 + 2 + 14 * len(label) + 8, y0 + 30],
                               fill=(20, 20, 22))
                if font is not None:
                    draw.text((x0 + 8, y0 + 4), label, fill=(255, 255, 255), font=font)
                else:
                    draw.text((x0 + 8, y0 + 4), label, fill=(255, 255, 255))
            sheet.save(cache, format="JPEG", quality=82)
            sheet_paths.append(cache)
        return sheet_paths
    except Exception as exc:  # noqa: BLE001
        log.debug("build contact sheets failed: %s", exc)
        return []


def _b64_of(path: Path, *, max_w: int = 1024) -> Optional[str]:
    """图 → 缩到 max_w → JPEG base64。失败 None。"""
    try:
        import base64

        from PIL import Image  # type: ignore

        with Image.open(path) as im:
            im = im.convert("RGB")
            if im.width > max_w:
                im = im.resize((max_w, int(im.height * max_w / im.width)))
            buf = BytesIO()
            im.save(buf, format="JPEG", quality=82)
        return base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:  # noqa: BLE001
        return None


def _parse_pick_id(text: str, valid_ids: set[int]) -> Optional[int]:
    """从模型回复解析 ``{"id": N}``,容忍杂文/围栏;回退抓首个合法整数。"""
    if not text:
        return None
    try:
        import json

        m = re.search(r"\{[^{}]*\}", text, re.DOTALL)
        if m:
            obj = json.loads(m.group(0))
            pid = int(obj.get("id"))
            if pid in valid_ids:
                return pid
    except Exception:  # noqa: BLE001
        pass
    for tok in re.findall(r"\d+", text):
        try:
            pid = int(tok)
            if pid in valid_ids:
                return pid
        except Exception:  # noqa: BLE001
            continue
    return None


def pick_template_by_preview(
    category_key: str,
    topic: str,
    *,
    max_sheets: int = 3,
    cols: int = 5,
    rows: int = 6,
    seed: Optional[int] = None,
) -> Optional[Path]:
    """按预览图为主题挑该大类里最合适的一套模板 .pptx。

    流程: 取 (pptx, preview) 对 → 拼 contact sheet → 一次 vision 调用挑 id →
    映射回 .pptx。**从不抛异常**;vision 不可用/解析失败 → 随机回退该类一套;
    该类为空 → None(上层回落 from-scratch)。
    """
    rnd = random.Random(seed)
    try:
        pptx_all = category_pptx(category_key)
        pairs_all: list[tuple[Path, Path]] = []
        for p in pptx_all:
            prev = preview_for(p)
            if prev is not None:
                pairs_all.append((p, prev))
        if not pairs_all:
            # 没有任何带预览图的模板 → 退而用任意 .pptx(若有)
            if pptx_all:
                return rnd.choice(pptx_all)
            log.info("pick_template: category %r empty", category_key)
            return None
        if len(pairs_all) == 1:
            return pairs_all[0][0]

        # 容量受限时随机采样并打日志(不静默截断)
        capacity = max_sheets * cols * rows
        chosen = pairs_all
        if len(pairs_all) > capacity:
            chosen = rnd.sample(pairs_all, capacity)
            log.info(
                "pick_template: %d candidates > capacity %d, sampled %d (dropped %d)",
                len(pairs_all), capacity, capacity, len(pairs_all) - capacity,
            )

        # id(1-based) → pptx 映射
        id_to_pptx = {i + 1: pptx for i, (pptx, _) in enumerate(chosen)}
        id_pairs = [(i + 1, prev) for i, (_, prev) in enumerate(chosen)]

        sheets = _build_contact_sheets(id_pairs, cols=cols, rows=rows)
        if not sheets:
            return rnd.choice([p for p, _ in chosen])

        from .ppt_visual_review import vision_chat

        content: list[dict[str, Any]] = [{
            "type": "text",
            "text": (
                f"下面是 {len(id_pairs)} 套 PPT 模板的预览缩略图,每张左上角有【编号】。"
                f"请按演示主题《{topic or '通用商务汇报'}》挑选风格/配色/版式最契合的"
                f"【一套】,只返回 JSON:{{\"id\": 编号}},不要任何其它文字。"
            ),
        }]
        for sp in sheets:
            b64 = _b64_of(sp)
            if b64:
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                })
        if len(content) <= 1:  # 没拼出任何图
            return rnd.choice([p for p, _ in chosen])

        text = vision_chat(content, timeout=90.0, max_tokens=120)
        pid = _parse_pick_id(text, set(id_to_pptx.keys()))
        if pid is not None:
            picked = id_to_pptx[pid]
            log.info("pick_template: vision chose id=%d → %s", pid, picked.name)
            return picked
        log.info("pick_template: vision unavailable/unparsable → random fallback")
        return rnd.choice([p for p, _ in chosen])
    except Exception as exc:  # noqa: BLE001
        log.warning("pick_template_by_preview failed: %s", str(exc)[:200])
        try:
            pptx_all = category_pptx(category_key)
            return rnd.choice(pptx_all) if pptx_all else None
        except Exception:  # noqa: BLE001
            return None
