# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Image OCR tool — RapidOCR wrapper (screenshot-ocr builtin skill).

``image_ocr(image_path)`` extracts text from an image (screenshot,
photo, scan). Backed by ``rapidocr-onnxruntime`` — a pure-pip OCR engine
that bundles its own ONNX models, so there is no system Tesseract
dependency and Chinese + English both work out of the box.

The engine is heavy to construct (loads ONNX models), so it is created
lazily on first use and cached for the process lifetime.

Degradation: if the engine package is not installed the tool still
registers but returns ``{"ok": false, "error": "ocr_engine_missing"}`` —
the skill tells the user the OCR component is unavailable.

Path policy: ``image_path`` must be authorized via ``office_pick_file``
(filter_key='image').
"""
from __future__ import annotations

import json
import logging
import threading
from typing import Any, Optional

from . import office_paths

log = logging.getLogger(__name__)

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tiff", ".tif"}

# Lazy, cached engine. Construction loads ONNX models (~1s+), so we only
# pay it once and only when OCR is actually used.
_engine: Any = None
_engine_lock = threading.Lock()
_engine_failed = False


def _get_engine() -> Optional[Any]:
    global _engine, _engine_failed
    if _engine is not None:
        return _engine
    if _engine_failed:
        return None
    with _engine_lock:
        if _engine is not None:
            return _engine
        if _engine_failed:
            return None
        try:
            from rapidocr_onnxruntime import RapidOCR

            _engine = RapidOCR()
        except Exception as exc:  # noqa: BLE001
            log.warning("RapidOCR init failed: %s", exc)
            _engine_failed = True
            return None
    return _engine


def ocr_engine_available() -> bool:
    """True when RapidOCR can be imported (does not construct it)."""
    try:
        import rapidocr_onnxruntime  # noqa: F401

        return True
    except Exception:  # noqa: BLE001
        return False


def image_ocr(image_path: str) -> dict[str, Any]:
    """Extract text from an image. Returns joined text + per-line list."""
    resolved = office_paths.resolve_for_read(image_path)
    if resolved is None:
        return {
            "ok": False,
            "error": (
                "image not authorized or not found — call office_pick_file "
                "with filter_key='image' so the user can choose the image"
            ),
            "retriable": False,
        }

    if resolved.suffix.lower() not in _IMAGE_EXTS:
        return {
            "ok": False,
            "error": f"not a recognized image file: {resolved.suffix}",
            "retriable": False,
        }

    engine = _get_engine()
    if engine is None:
        return {
            "ok": False,
            "error": "ocr_engine_missing",
            "message": "OCR 组件 (RapidOCR) 不可用，无法识别图片文字。",
            "retriable": False,
        }

    try:
        result, _elapse = engine(str(resolved))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"ocr failed: {exc}", "retriable": True}

    lines: list[str] = []
    if result:
        for item in result:
            # RapidOCR row: [box, text, score]
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                lines.append(str(item[1]))

    return {
        "ok": True,
        "path": str(resolved),
        "text": "\n".join(lines),
        "lines": lines,
        "line_count": len(lines),
    }


# ---------------------------------------------------------------------------
# Tool registry wiring
# ---------------------------------------------------------------------------
_SCHEMA = {
    "name": "image_ocr",
    "description": (
        "Extract text from an image (screenshot / photo / scan). Supports "
        "Chinese and English. Returns the recognized text. If the OCR engine "
        "is unavailable the result has error='ocr_engine_missing' — tell the "
        "user honestly. The image must first be chosen via office_pick_file "
        "(filter_key='image')."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "image_path": {"type": "string", "description": "Absolute path to the image."},
        },
        "required": ["image_path"],
    },
}


def _handle(args: dict[str, Any], task_id: str = "") -> str:
    return json.dumps(image_ocr(str(args.get("image_path", ""))), ensure_ascii=False)


def _register() -> None:
    try:
        from .registry import registry

        registry.register(
            "image_ocr",
            "office",
            _SCHEMA,
            _handle,
            permission_category="read_file",
            timeout_seconds=60.0,
        )
    except Exception:  # noqa: BLE001
        pass


_register()

__all__ = ["image_ocr", "ocr_engine_available"]
