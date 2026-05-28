# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""PPT generation tool — python-pptx wrapper with themes + layouts.

Architecture
------------
LLM produces a JSON outline (list of :class:`SlideOutline`); this
module turns it into a real ``.pptx`` file on disk. Three themes and
seven layouts are baked in — the agent picks layouts per slide, never
fiddles with low-level XML.

Themes
~~~~~~
* ``minimal``  — white / blue / charcoal, sans-serif. The default.
* ``dark``     — near-black background, cyan accents, light text.
* ``playful``  — cream background, coral accents, rounded shapes.

Each theme is a dataclass that exposes ``background_rgb``,
``primary_rgb``, ``text_rgb``, ``font_family``. Layouts pick from these
so visuals stay consistent across slides.

Layouts
~~~~~~~
``title`` / ``section`` / ``bullet`` / ``two_column`` / ``image`` /
``quote`` / ``toc``. Each is a small function in this module that takes
a :class:`pptx.Presentation` slide + the outline data and lays it out.

Failure modes
-------------
* ``python-pptx`` not installed → :func:`ppt_create` returns
  ``{"error": ..., "markdown_fallback": "<best-effort .md>"}``. Caller
  (the agent) can still hand the user a readable outline.
* Output path unwritable / invalid → same fallback.
* Image path missing → that slide degrades to a bullet layout with a
  warning embedded as a footnote text.

The whole module is purely synchronous and CPU-cheap — no LLM calls
here. Producing the outline is the LLM's job, layout is ours.
"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Literal, Optional, Sequence

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------
# python-pptx availability — defer the import so callers without the
# dep get a graceful fallback instead of an ImportError at module load.
# ---------------------------------------------------------------------
try:  # pragma: no cover — import probe
    from pptx import Presentation as _Presentation
    from pptx.util import Inches, Pt, Emu
    from pptx.dml.color import RGBColor
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
    from pptx.enum.dml import MSO_FILL_TYPE
    from pptx.chart.data import CategoryChartData
    from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION
    _HAS_PPTX = True
except ImportError:  # pragma: no cover
    _Presentation = None  # type: ignore
    Inches = Pt = Emu = RGBColor = MSO_SHAPE = PP_ALIGN = MSO_ANCHOR = MSO_FILL_TYPE = None  # type: ignore
    CategoryChartData = XL_CHART_TYPE = XL_LEGEND_POSITION = None  # type: ignore
    _HAS_PPTX = False


VALID_LAYOUTS = (
    "title", "section", "bullet", "two_column", "image", "quote", "toc", "chart",
)
VALID_THEMES = ("minimal", "dark", "playful")


# ---------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------


@dataclass
class SlideOutline:
    """One slide spec. LLM produces a list of these as JSON."""

    layout: str = "bullet"
    title: str = ""
    subtitle: str = ""
    bullets: list[str] = field(default_factory=list)
    # two_column
    left: list[str] = field(default_factory=list)
    right: list[str] = field(default_factory=list)
    left_title: str = ""
    right_title: str = ""
    # image
    image_path: Optional[str] = None
    caption: str = ""
    # quote
    quote: str = ""
    cite: str = ""
    # toc — uses bullets[]
    # chart: {"type": "bar"|"line"|"pie", "categories": [...],
    #         "series": [{"name": "...", "values": [...]}, ...]}
    chart: Optional[dict] = None
    # generic
    notes: str = ""  # speaker notes

    def normalize(self) -> "SlideOutline":
        """Coerce raw LLM output into the strict schema."""
        layout = (self.layout or "bullet").strip().lower()
        if layout not in VALID_LAYOUTS:
            log.debug("unknown layout %r → falling back to bullet", layout)
            layout = "bullet"
        return SlideOutline(
            layout=layout,
            title=(self.title or "").strip(),
            subtitle=(self.subtitle or "").strip(),
            bullets=[str(b).strip() for b in (self.bullets or []) if str(b).strip()],
            left=[str(b).strip() for b in (self.left or []) if str(b).strip()],
            right=[str(b).strip() for b in (self.right or []) if str(b).strip()],
            left_title=(self.left_title or "").strip(),
            right_title=(self.right_title or "").strip(),
            image_path=(self.image_path or None),
            caption=(self.caption or "").strip(),
            quote=(self.quote or "").strip(),
            cite=(self.cite or "").strip(),
            chart=(self.chart if isinstance(self.chart, dict) else None),
            notes=(self.notes or "").strip(),
        )


# ---------------------------------------------------------------------
# Themes
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class Theme:
    name: str
    background_rgb: tuple[int, int, int]
    primary_rgb: tuple[int, int, int]
    accent_rgb: tuple[int, int, int]
    text_rgb: tuple[int, int, int]
    muted_text_rgb: tuple[int, int, int]
    font_heading: str
    font_body: str


_THEMES: dict[str, Theme] = {
    "minimal": Theme(
        name="minimal",
        background_rgb=(255, 255, 255),
        primary_rgb=(15, 23, 42),     # slate-900
        accent_rgb=(37, 99, 235),     # blue-600
        text_rgb=(15, 23, 42),
        muted_text_rgb=(100, 116, 139),  # slate-500
        font_heading="Microsoft YaHei UI",
        font_body="Microsoft YaHei UI",
    ),
    "dark": Theme(
        name="dark",
        background_rgb=(17, 24, 39),  # gray-900
        primary_rgb=(248, 250, 252),  # slate-50
        accent_rgb=(34, 211, 238),    # cyan-400
        text_rgb=(241, 245, 249),
        muted_text_rgb=(148, 163, 184),
        font_heading="Microsoft YaHei UI",
        font_body="Microsoft YaHei UI",
    ),
    "playful": Theme(
        name="playful",
        background_rgb=(254, 247, 234),  # cream
        primary_rgb=(120, 53, 15),       # amber-900
        accent_rgb=(244, 114, 91),       # coral
        text_rgb=(68, 47, 30),
        muted_text_rgb=(146, 100, 64),
        font_heading="Microsoft YaHei UI",
        font_body="Microsoft YaHei UI",
    ),
}


def get_theme(name: str) -> Theme:
    return _THEMES.get(name.lower(), _THEMES["minimal"])


# ---------------------------------------------------------------------
# Outline parsing
# ---------------------------------------------------------------------


def parse_outline(raw: Any) -> list[SlideOutline]:
    """Tolerant LLM-output → list[SlideOutline] converter.

    Accepts:
      * JSON string of a list
      * JSON string wrapped in code fences
      * Python list of dicts
      * Single dict (treated as one-slide deck)

    Each slide dict is fed through :class:`SlideOutline` constructor
    (unknown keys are silently ignored). Empty / unparseable input
    returns ``[]``.
    """
    if raw is None:
        return []
    data: Any = raw
    if isinstance(data, str):
        text = data.strip()
        if not text:
            return []
        # Strip code fences
        if text.startswith("```"):
            nl = text.find("\n")
            if nl != -1:
                text = text[nl + 1:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()
        # Try to extract first JSON array / object
        lb_arr, rb_arr = text.find("["), text.rfind("]")
        lb_obj, rb_obj = text.find("{"), text.rfind("}")
        slice_arr = text[lb_arr:rb_arr + 1] if 0 <= lb_arr < rb_arr else None
        slice_obj = text[lb_obj:rb_obj + 1] if 0 <= lb_obj < rb_obj else None
        for candidate in (slice_arr, slice_obj, text):
            if not candidate:
                continue
            try:
                data = json.loads(candidate)
                break
            except json.JSONDecodeError:
                continue
        else:
            return []
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return []
    out: list[SlideOutline] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        try:
            so = SlideOutline(**{
                k: v for k, v in item.items()
                if k in SlideOutline.__dataclass_fields__
            })
        except (TypeError, ValueError):
            continue
        out.append(so.normalize())
    return out


# ---------------------------------------------------------------------
# Markdown fallback (when python-pptx unavailable)
# ---------------------------------------------------------------------


def render_markdown_fallback(
    outline: Sequence[SlideOutline], *, title: str = "", author: str = "",
) -> str:
    """Render a slide outline as a Markdown document for the
    no-python-pptx fallback path. Used by callers as the safe ``markdown_fallback``
    field in the error result.
    """
    lines: list[str] = []
    if title:
        lines.append(f"# {title}")
    if author:
        lines.append(f"_作者: {author}_\n")
    for i, slide in enumerate(outline, start=1):
        lines.append(f"\n## 第 {i} 页 — {slide.title or '(无标题)'}")
        if slide.subtitle:
            lines.append(f"_{slide.subtitle}_")
        if slide.layout == "two_column":
            if slide.left_title or slide.left:
                lines.append(f"\n**左 — {slide.left_title}**")
                for b in slide.left:
                    lines.append(f"- {b}")
            if slide.right_title or slide.right:
                lines.append(f"\n**右 — {slide.right_title}**")
                for b in slide.right:
                    lines.append(f"- {b}")
        elif slide.layout == "quote":
            lines.append(f"\n> {slide.quote}")
            if slide.cite:
                lines.append(f"\n— {slide.cite}")
        elif slide.layout == "image":
            if slide.image_path:
                lines.append(f"\n![{slide.caption}]({slide.image_path})")
            if slide.caption:
                lines.append(f"\n_{slide.caption}_")
        else:
            for b in slide.bullets:
                lines.append(f"- {b}")
        if slide.notes:
            lines.append(f"\n> 备注: {slide.notes}")
    return "\n".join(lines).strip() + "\n"


# ---------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------


# 16:9 default canvas. EMU = English Metric Units; 914400 = 1 inch.
_SLIDE_WIDTH = 9144000   # 10 inches
_SLIDE_HEIGHT = 5143500  # 5.625 inches → 16:9


def _rgb(triple: tuple[int, int, int]):
    return RGBColor(*triple)


def _fill_slide_bg(slide, theme: Theme) -> None:
    bg = slide.background
    fill = bg.fill
    fill.solid()
    fill.fore_color.rgb = _rgb(theme.background_rgb)


def _add_text(
    slide,
    text: str,
    *,
    left: int,
    top: int,
    width: int,
    height: int,
    font_size: int,
    bold: bool = False,
    color: tuple[int, int, int] = (0, 0, 0),
    font_name: str = "Microsoft YaHei UI",
    align: str = "left",
    anchor: str = "top",
):
    tb = slide.shapes.add_textbox(left, top, width, height)
    tf = tb.text_frame
    tf.word_wrap = True
    tf.margin_left = Inches(0.1)
    tf.margin_right = Inches(0.1)
    tf.margin_top = Inches(0.05)
    tf.margin_bottom = Inches(0.05)
    tf.vertical_anchor = {
        "top": MSO_ANCHOR.TOP,
        "middle": MSO_ANCHOR.MIDDLE,
        "bottom": MSO_ANCHOR.BOTTOM,
    }.get(anchor, MSO_ANCHOR.TOP)
    p = tf.paragraphs[0]
    p.alignment = {
        "left": PP_ALIGN.LEFT,
        "center": PP_ALIGN.CENTER,
        "right": PP_ALIGN.RIGHT,
    }.get(align, PP_ALIGN.LEFT)
    run = p.add_run()
    run.text = text
    f = run.font
    f.name = font_name
    f.size = Pt(font_size)
    f.bold = bold
    f.color.rgb = _rgb(color)
    return tb


def _add_bullet_text(
    slide,
    bullets: Iterable[str],
    *,
    left: int,
    top: int,
    width: int,
    height: int,
    font_size: int,
    color: tuple[int, int, int],
    font_name: str,
    accent_color: tuple[int, int, int] | None = None,
):
    """Render bullets with a coloured dot prefix.

    python-pptx doesn't expose first-class bullet formatting reliably
    across themes, so we draw the bullet character ourselves (●)
    coloured with the accent colour.
    """
    tb = slide.shapes.add_textbox(left, top, width, height)
    tf = tb.text_frame
    tf.word_wrap = True
    tf.margin_left = Inches(0.1)
    tf.margin_right = Inches(0.1)
    tf.margin_top = Inches(0.05)
    tf.margin_bottom = Inches(0.05)
    bullets = list(bullets)
    if not bullets:
        # Empty — leave an invisible placeholder so layout stays stable.
        tf.paragraphs[0].text = ""
        return tb
    for i, line in enumerate(bullets):
        para = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        para.alignment = PP_ALIGN.LEFT
        para.space_after = Pt(8)
        dot = para.add_run()
        dot.text = "● "
        dot.font.size = Pt(font_size)
        dot.font.name = font_name
        dot.font.color.rgb = _rgb(accent_color or color)
        body = para.add_run()
        body.text = str(line)
        body.font.size = Pt(font_size)
        body.font.name = font_name
        body.font.color.rgb = _rgb(color)
    return tb


def _add_accent_bar(slide, theme: Theme, *, top: int = 0) -> None:
    """Decorative left-edge accent bar for non-title slides."""
    bar = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE,
        Inches(0), Emu(top),
        Inches(0.18), Emu(_SLIDE_HEIGHT - top),
    )
    bar.line.fill.background()
    bar.fill.solid()
    bar.fill.fore_color.rgb = _rgb(theme.accent_rgb)


# ---- Per-layout renderers ------------------------------------------


def _render_title(slide, outline: SlideOutline, theme: Theme) -> None:
    _fill_slide_bg(slide, theme)
    # Big horizontal accent bar at top-third
    accent = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE,
        Inches(0), Inches(2.2),
        Inches(10), Inches(0.08),
    )
    accent.line.fill.background()
    accent.fill.solid()
    accent.fill.fore_color.rgb = _rgb(theme.accent_rgb)
    _add_text(
        slide, outline.title or "(无标题)",
        left=Inches(0.6), top=Inches(1.3),
        width=Inches(8.8), height=Inches(0.9),
        font_size=44, bold=True,
        color=theme.primary_rgb,
        font_name=theme.font_heading,
        align="left", anchor="top",
    )
    if outline.subtitle:
        _add_text(
            slide, outline.subtitle,
            left=Inches(0.6), top=Inches(2.4),
            width=Inches(8.8), height=Inches(0.6),
            font_size=20,
            color=theme.muted_text_rgb,
            font_name=theme.font_body,
            align="left",
        )


def _render_section(slide, outline: SlideOutline, theme: Theme) -> None:
    _fill_slide_bg(slide, theme)
    # Centered large title
    _add_text(
        slide, outline.title or "(章节)",
        left=Inches(0.5), top=Inches(2.0),
        width=Inches(9), height=Inches(1.0),
        font_size=40, bold=True,
        color=theme.accent_rgb,
        font_name=theme.font_heading,
        align="center", anchor="middle",
    )
    if outline.subtitle:
        _add_text(
            slide, outline.subtitle,
            left=Inches(0.5), top=Inches(3.0),
            width=Inches(9), height=Inches(0.6),
            font_size=18,
            color=theme.muted_text_rgb,
            font_name=theme.font_body,
            align="center",
        )


def _render_bullet(slide, outline: SlideOutline, theme: Theme) -> None:
    _fill_slide_bg(slide, theme)
    _add_accent_bar(slide, theme)
    _add_text(
        slide, outline.title or "(无标题)",
        left=Inches(0.6), top=Inches(0.4),
        width=Inches(8.8), height=Inches(0.7),
        font_size=28, bold=True,
        color=theme.primary_rgb,
        font_name=theme.font_heading,
    )
    if outline.subtitle:
        _add_text(
            slide, outline.subtitle,
            left=Inches(0.6), top=Inches(1.05),
            width=Inches(8.8), height=Inches(0.4),
            font_size=14,
            color=theme.muted_text_rgb,
            font_name=theme.font_body,
        )
    _add_bullet_text(
        slide, outline.bullets,
        left=Inches(0.7), top=Inches(1.6),
        width=Inches(8.6), height=Inches(3.8),
        font_size=18,
        color=theme.text_rgb,
        font_name=theme.font_body,
        accent_color=theme.accent_rgb,
    )


def _render_two_column(slide, outline: SlideOutline, theme: Theme) -> None:
    _fill_slide_bg(slide, theme)
    _add_accent_bar(slide, theme)
    _add_text(
        slide, outline.title or "(对比)",
        left=Inches(0.6), top=Inches(0.4),
        width=Inches(8.8), height=Inches(0.7),
        font_size=28, bold=True,
        color=theme.primary_rgb,
        font_name=theme.font_heading,
    )
    # Two equal columns
    col_w = Inches(4.2)
    col_top = Inches(1.5)
    col_h = Inches(3.8)
    # Left column header
    _add_text(
        slide, outline.left_title or "左",
        left=Inches(0.6), top=col_top,
        width=col_w, height=Inches(0.5),
        font_size=18, bold=True,
        color=theme.accent_rgb,
        font_name=theme.font_heading,
    )
    _add_bullet_text(
        slide, outline.left,
        left=Inches(0.6), top=Inches(2.0),
        width=col_w, height=col_h,
        font_size=16,
        color=theme.text_rgb,
        font_name=theme.font_body,
        accent_color=theme.accent_rgb,
    )
    # Right column header
    _add_text(
        slide, outline.right_title or "右",
        left=Inches(5.2), top=col_top,
        width=col_w, height=Inches(0.5),
        font_size=18, bold=True,
        color=theme.accent_rgb,
        font_name=theme.font_heading,
    )
    _add_bullet_text(
        slide, outline.right,
        left=Inches(5.2), top=Inches(2.0),
        width=col_w, height=col_h,
        font_size=16,
        color=theme.text_rgb,
        font_name=theme.font_body,
        accent_color=theme.accent_rgb,
    )


def _render_image(slide, outline: SlideOutline, theme: Theme) -> None:
    _fill_slide_bg(slide, theme)
    _add_accent_bar(slide, theme)
    _add_text(
        slide, outline.title or "",
        left=Inches(0.6), top=Inches(0.4),
        width=Inches(8.8), height=Inches(0.7),
        font_size=24, bold=True,
        color=theme.primary_rgb,
        font_name=theme.font_heading,
    )
    img = outline.image_path
    if img and Path(img).is_file():
        try:
            slide.shapes.add_picture(
                img,
                Inches(2.0), Inches(1.3),
                width=Inches(6.0),
            )
            if outline.caption:
                _add_text(
                    slide, outline.caption,
                    left=Inches(0.6), top=Inches(4.8),
                    width=Inches(8.8), height=Inches(0.4),
                    font_size=12,
                    color=theme.muted_text_rgb,
                    font_name=theme.font_body,
                    align="center",
                )
            return
        except Exception as exc:  # noqa: BLE001
            log.debug("image insert failed (%s); falling back to bullet", exc)
    # Fallback to bullet layout with caption + warning marker
    fallback_bullets = (
        [f"[image missing: {img}]"] if img else []
    ) + ([outline.caption] if outline.caption else [])
    _add_bullet_text(
        slide, fallback_bullets or ["(图片缺失)"],
        left=Inches(0.7), top=Inches(1.6),
        width=Inches(8.6), height=Inches(3.5),
        font_size=18,
        color=theme.muted_text_rgb,
        font_name=theme.font_body,
        accent_color=theme.accent_rgb,
    )


def _render_quote(slide, outline: SlideOutline, theme: Theme) -> None:
    _fill_slide_bg(slide, theme)
    quote_text = outline.quote or outline.title or "(无引述)"
    # Big left quote mark
    _add_text(
        slide, "“",
        left=Inches(0.6), top=Inches(0.7),
        width=Inches(1.2), height=Inches(1.5),
        font_size=110, bold=True,
        color=theme.accent_rgb,
        font_name=theme.font_heading,
    )
    _add_text(
        slide, quote_text,
        left=Inches(1.4), top=Inches(1.4),
        width=Inches(7.8), height=Inches(2.8),
        font_size=24,
        color=theme.text_rgb,
        font_name=theme.font_body,
        anchor="middle",
    )
    if outline.cite:
        _add_text(
            slide, f"— {outline.cite}",
            left=Inches(1.4), top=Inches(4.4),
            width=Inches(7.8), height=Inches(0.5),
            font_size=14,
            color=theme.muted_text_rgb,
            font_name=theme.font_body,
        )


def _render_toc(slide, outline: SlideOutline, theme: Theme) -> None:
    _fill_slide_bg(slide, theme)
    _add_accent_bar(slide, theme)
    _add_text(
        slide, outline.title or "目录",
        left=Inches(0.6), top=Inches(0.4),
        width=Inches(8.8), height=Inches(0.7),
        font_size=32, bold=True,
        color=theme.primary_rgb,
        font_name=theme.font_heading,
    )
    # Numbered list rendered manually for visual weight
    tb = slide.shapes.add_textbox(
        Inches(0.7), Inches(1.5), Inches(8.6), Inches(3.8),
    )
    tf = tb.text_frame
    tf.word_wrap = True
    for i, item in enumerate(outline.bullets):
        para = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        para.alignment = PP_ALIGN.LEFT
        para.space_after = Pt(10)
        num = para.add_run()
        num.text = f"{i + 1:02d}.  "
        num.font.size = Pt(22)
        num.font.bold = True
        num.font.name = theme.font_heading
        num.font.color.rgb = _rgb(theme.accent_rgb)
        body = para.add_run()
        body.text = str(item)
        body.font.size = Pt(20)
        body.font.name = theme.font_body
        body.font.color.rgb = _rgb(theme.text_rgb)


def _render_chart(slide, outline: SlideOutline, theme: Theme) -> None:
    """Native PowerPoint chart slide (bar / line / pie).

    ``outline.chart`` shape::

        {"type": "bar"|"line"|"pie",
         "categories": ["Q1", "Q2", ...],
         "series": [{"name": "营收", "values": [10, 20, ...]}, ...]}

    Missing / malformed data degrades to a bullet layout so the deck
    never aborts on one bad slide.
    """
    _fill_slide_bg(slide, theme)
    _add_accent_bar(slide, theme)
    _add_text(
        slide, outline.title or "(图表)",
        left=Inches(0.6), top=Inches(0.4),
        width=Inches(8.8), height=Inches(0.7),
        font_size=28, bold=True,
        color=theme.primary_rgb,
        font_name=theme.font_heading,
    )

    spec = outline.chart or {}
    categories = spec.get("categories") or []
    series = [s for s in (spec.get("series") or []) if isinstance(s, dict)]
    if not categories or not series:
        _add_bullet_text(
            slide, outline.bullets or ["（图表数据缺失，已降级为要点）"],
            left=Inches(0.7), top=Inches(1.6),
            width=Inches(8.6), height=Inches(3.8),
            font_size=18, color=theme.text_rgb,
            font_name=theme.font_body, accent_color=theme.accent_rgb,
        )
        return

    ctype = str(spec.get("type") or "bar").lower()
    xl_type = {
        "bar": XL_CHART_TYPE.COLUMN_CLUSTERED,
        "column": XL_CHART_TYPE.COLUMN_CLUSTERED,
        "line": XL_CHART_TYPE.LINE_MARKERS,
        "pie": XL_CHART_TYPE.PIE,
    }.get(ctype, XL_CHART_TYPE.COLUMN_CLUSTERED)

    chart_data = CategoryChartData()
    chart_data.categories = [str(c) for c in categories]
    for s in series:
        name = str(s.get("name") or "系列")
        raw_values = s.get("values") or []
        values: list[float] = []
        for v in raw_values:
            try:
                values.append(float(v))
            except (TypeError, ValueError):
                values.append(0.0)
        chart_data.add_series(name, tuple(values))

    try:
        gframe = slide.shapes.add_chart(
            xl_type,
            Inches(0.8), Inches(1.5), Inches(8.4), Inches(3.5),
            chart_data,
        )
        chart = gframe.chart
        chart.has_legend = True
        chart.legend.position = XL_LEGEND_POSITION.BOTTOM
        chart.legend.include_in_layout = False
    except Exception as exc:  # noqa: BLE001 — degrade rather than abort
        log.warning("chart render failed, degrading to bullets: %s", exc)
        _add_bullet_text(
            slide, outline.bullets or [f"{s.get('name')}" for s in series],
            left=Inches(0.7), top=Inches(1.6),
            width=Inches(8.6), height=Inches(3.8),
            font_size=18, color=theme.text_rgb,
            font_name=theme.font_body, accent_color=theme.accent_rgb,
        )


_RENDERERS = {
    "title": _render_title,
    "section": _render_section,
    "bullet": _render_bullet,
    "two_column": _render_two_column,
    "image": _render_image,
    "quote": _render_quote,
    "toc": _render_toc,
    "chart": _render_chart,
}


def _add_footer(slide, theme: Theme, page_number: int, total: int) -> None:
    """Page number + slim divider line at the bottom."""
    # Divider line
    line = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE,
        Inches(0.6), Inches(5.15),
        Inches(8.8), Emu(7000),
    )
    line.line.fill.background()
    line.fill.solid()
    line.fill.fore_color.rgb = _rgb(theme.muted_text_rgb)
    # Page number
    _add_text(
        slide, f"{page_number} / {total}",
        left=Inches(8.4), top=Inches(5.25),
        width=Inches(1.0), height=Inches(0.3),
        font_size=10,
        color=theme.muted_text_rgb,
        font_name=theme.font_body,
        align="right",
    )


# ---------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------


def ppt_create(
    outline: Any,
    *,
    theme: str = "minimal",
    title: str = "",
    author: str = "DeskPet",
    output_path: Optional[str] = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Render an outline into a ``.pptx`` file on disk.

    Parameters
    ----------
    outline:
        Either a JSON string of a list of slide dicts, or a Python list
        of dicts, or a single dict. See :class:`SlideOutline` for shape.
    theme:
        One of ``minimal`` / ``dark`` / ``playful``. Falls back to
        ``minimal`` on typo.
    title:
        Optional deck title. If the first slide is ``layout=title`` the
        outline's title wins; otherwise this is used as the document
        property only.
    author:
        Set on the document core properties.
    output_path:
        Absolute path. Defaults to ``<tempdir>/deskpet-ppt-<ts>.pptx``.

    Returns
    -------
    dict
        On success: ``{"ok": True, "path": str, "slide_count": int, "theme": str}``.
        On failure: ``{"ok": False, "error": str, "markdown_fallback": str}``.
    """
    slides = parse_outline(outline)
    if not slides:
        return {
            "ok": False,
            "error": "outline parse failed or empty",
            "markdown_fallback": render_markdown_fallback(
                slides, title=title, author=author,
            ),
        }

    # WI-T1.6: dry_run 预览模式（PRD §3 D9）。返回 outline markdown 作为
    # text artifact，不写 .pptx — 用户/LLM 可先看大纲再决定是否真生成。
    # 显式 emit artifacts[] 走 D1 一等公民路径。
    if dry_run:
        md = render_markdown_fallback(slides, title=title, author=author)
        return {
            "ok": True,
            "dry_run": True,
            "slide_count": len(slides),
            "artifacts": [{
                "kind": "text",
                "title": (title or "outline") + " (preview)",
                "preview": md,
            }],
        }

    if not _HAS_PPTX:
        return {
            "ok": False,
            "error": "python-pptx not installed; install with `pip install python-pptx`",
            "markdown_fallback": render_markdown_fallback(
                slides, title=title, author=author,
            ),
        }

    theme_obj = get_theme(theme)
    out_path = _resolve_output_path(output_path)

    try:
        prs = _Presentation()
        prs.slide_width = _SLIDE_WIDTH
        prs.slide_height = _SLIDE_HEIGHT
        total = len(slides)
        blank_layout = prs.slide_layouts[6]  # 6 = "Blank" in default template
        for i, so in enumerate(slides, start=1):
            slide = prs.slides.add_slide(blank_layout)
            renderer = _RENDERERS.get(so.layout, _render_bullet)
            renderer(slide, so, theme_obj)
            # Footer everywhere except the very first title slide for breathing room.
            if so.layout != "title":
                _add_footer(slide, theme_obj, i, total)
            # Speaker notes
            if so.notes:
                notes_tf = slide.notes_slide.notes_text_frame
                notes_tf.text = so.notes
        # Document core properties
        try:
            cp = prs.core_properties
            if title:
                cp.title = title
            if author:
                cp.author = author
                cp.last_modified_by = author
        except Exception:  # noqa: BLE001
            pass
        prs.save(out_path)
        # WI-T1.2 D1：显式 emit artifacts[]（一等公民路径，前端按 kind=file
        # 渲染 ArtifactCard；保留 path 字段保 BC）。
        return {
            "ok": True,
            "path": str(out_path),
            "slide_count": total,
            "theme": theme_obj.name,
            "artifacts": [{
                "kind": "file",
                "path": str(out_path),
                "mime": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                "title": Path(str(out_path)).name,
            }],
        }
    except Exception as exc:  # noqa: BLE001
        log.warning("ppt_create failed: %s", exc, exc_info=True)
        return {
            "ok": False,
            "error": f"render failed: {exc}",
            "markdown_fallback": render_markdown_fallback(
                slides, title=title, author=author,
            ),
        }


def _resolve_output_path(p: Optional[str]) -> Path:
    if p:
        path = Path(p).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        return path
    ts = int(time.time())
    fname = f"deskpet-ppt-{ts}.pptx"
    return Path(tempfile.gettempdir()) / fname


# ---------------------------------------------------------------------
# Tool registry wiring
# ---------------------------------------------------------------------


_PPT_SCHEMA = {
    "name": "ppt_create",
    "description": (
        "Generate a professional .pptx presentation locally from an outline. "
        "Returns the file path on success; falls back to a Markdown outline "
        "when python-pptx is unavailable. Use this AFTER you've decided on a "
        "structured slide outline. Do not stuff long paragraphs into bullets "
        "— bullets are cues, not scripts."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "outline": {
                "description": (
                    "List of slide dicts, OR a JSON string of such a list. "
                    "Each slide: {layout, title, subtitle?, bullets?, left?, "
                    "right?, left_title?, right_title?, image_path?, caption?, "
                    "quote?, cite?, notes?}. layout ∈ {title, section, bullet, "
                    "two_column, image, quote, toc}."
                ),
                "type": ["array", "string"],
            },
            "theme": {
                "description": "Visual theme. minimal=clean business; dark=tech demos; playful=marketing.",
                "type": "string",
                "enum": list(VALID_THEMES),
                "default": "minimal",
            },
            "title": {"type": "string", "description": "Document title (core properties)."},
            "author": {"type": "string", "description": "Author name. Defaults to DeskPet."},
            "output_path": {
                "type": "string",
                "description": "Absolute output path. Defaults to a temp file.",
            },
            "dry_run": {
                "type": "boolean",
                "description": (
                    "WI-T1.6 outline 预览模式。True → 不写 .pptx，仅返回 "
                    "outline markdown 作为 text artifact 供用户确认。建议 ≥ 5 "
                    "张幻灯片的 deck 先 dry_run=true 让用户审查 outline，再 "
                    "dry_run=false 实际生成。"
                ),
                "default": False,
            },
        },
        "required": ["outline"],
    },
}


def _handle_ppt_create(args: dict, task_id: str) -> str:
    """Sync handler wired into the tool registry.

    ``ppt_create`` is itself synchronous (no network, no LLM), so we
    just JSON-serialize the result.
    """
    result = ppt_create(
        args.get("outline"),
        theme=str(args.get("theme") or "minimal"),
        title=str(args.get("title") or ""),
        author=str(args.get("author") or "DeskPet"),
        output_path=(str(args["output_path"]) if args.get("output_path") else None),
        dry_run=bool(args.get("dry_run", False)),
    )
    return json.dumps(result, ensure_ascii=False)


def _register_ppt_tool() -> None:
    """Module-import side effect: register ppt_create with the registry.

    Wrapped in a try/except so import-cycles or missing registry don't
    break test collection — every test in this repo imports the tool
    module directly without needing the registry.
    """
    try:
        from .registry import registry  # type: ignore
        registry.register(
            "ppt_create",
            "ppt",
            _PPT_SCHEMA,
            _handle_ppt_create,
            permission_category="write_file",
            timeout_seconds=30.0,
        )
    except Exception as exc:  # noqa: BLE001
        log.debug("ppt tool registration skipped: %s", exc)


_register_ppt_tool()
