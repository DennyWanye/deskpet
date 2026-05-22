"""TDD T5 — ocr_tools.py (image_ocr)."""
from __future__ import annotations

from pathlib import Path

import pytest

from deskpet.tools import office_paths as op
from deskpet.tools import ocr_tools as ocr


@pytest.fixture(autouse=True)
def _clean():
    op.clear_authorizations()
    yield
    op.clear_authorizations()


def _make_text_image(path: Path, text: str) -> None:
    """Render text onto a white PNG so OCR has something to read."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (480, 140), "white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 48)
    except OSError:
        font = ImageFont.load_default()
    draw.text((20, 40), text, fill="black", font=font)
    img.save(path)


_HAS_OCR = ocr.ocr_engine_available()
_skip_ocr = pytest.mark.skipif(not _HAS_OCR, reason="RapidOCR not installed")


@_skip_ocr
def test_t5_2_english_image(tmp_path: Path):
    img = tmp_path / "en.png"
    _make_text_image(img, "HELLO WORLD")
    op.authorize_path(img)
    r = ocr.image_ocr(str(img))
    assert r["ok"], r
    assert "HELLO" in r["text"].upper() or "WORLD" in r["text"].upper()


def test_t5_3_image_not_found(tmp_path: Path):
    r = ocr.image_ocr(str(tmp_path / "ghost.png"))
    assert not r["ok"]


def test_t5_4_non_image_rejected(tmp_path: Path):
    f = tmp_path / "note.txt"
    f.write_text("hi")
    op.authorize_path(f)
    r = ocr.image_ocr(str(f))
    assert not r["ok"] and r["retriable"] is False


def test_t5_5_unauthorized_rejected(tmp_path: Path):
    img = tmp_path / "x.png"
    _make_text_image(img, "TEST")
    # not authorized
    r = ocr.image_ocr(str(img))
    assert not r["ok"] and r["retriable"] is False
