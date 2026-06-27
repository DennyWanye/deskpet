# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""PPTX -> PNG preview rendering via local WPS COM.

This module is intentionally best-effort. WPS startup can be slow and COM
availability depends on the host machine, so callers decide when to use it.
"""
from __future__ import annotations

import os
from pathlib import Path

try:  # pragma: no cover - host-dependent import probe
    import pythoncom  # type: ignore
    import win32com.client  # type: ignore

    _COM_IMPORT_OK = True
except Exception:  # noqa: BLE001
    pythoncom = None  # type: ignore
    win32com = None  # type: ignore
    _COM_IMPORT_OK = False


_AVAILABLE: bool | None = False if not _COM_IMPORT_OK else None


def _env_disabled() -> bool:
    raw = os.environ.get("DESKPET_PPT_PREVIEW_RENDER")
    return raw is not None and raw.strip().lower() in {"0", "false", "no", "off", ""}


def com_render_available() -> bool:
    """Return whether WPS COM rendering is available on this machine."""
    global _AVAILABLE
    if _env_disabled():
        return False
    if not _COM_IMPORT_OK:
        _AVAILABLE = False
        return False
    if _AVAILABLE is not None:
        return bool(_AVAILABLE)

    app = None
    initialized = False
    try:
        pythoncom.CoInitialize()
        initialized = True
        app = win32com.client.Dispatch("Kwpp.Application")
        _AVAILABLE = True
    except Exception:  # noqa: BLE001
        _AVAILABLE = False
    finally:
        if app is not None:
            try:
                app.Quit()
            except Exception:  # noqa: BLE001
                pass
        if initialized:
            try:
                pythoncom.CoUninitialize()
            except Exception:  # noqa: BLE001
                pass
    return bool(_AVAILABLE)


def _kill_wps() -> None:
    """杀残留 WPS 进程(COM 在某页 hang 后超时杀进程,释放下次渲染)。"""
    import subprocess

    for name in ("wpp.exe", "wps.exe", "wpscloudsvr.exe", "et.exe"):
        try:
            subprocess.run(["taskkill", "/F", "/IM", name],
                           capture_output=True, timeout=10)
        except Exception:  # noqa: BLE001
            pass


def render_pptx_to_pngs_safe(
    pptx_path: str,
    out_dir: str,
    *,
    width: int = 1280,
    height: int = 720,
    max_slides: int = 30,
    timeout: float = 120.0,
) -> list[str]:
    """带超时的预览渲染 —— 在【子进程】里跑 WPS COM,超时即杀子进程+杀 WPS。

    WPS COM 对个别 .pptx/某页 Open/Export 会【挂死】(非异常),in-process
    渲染会让调用线程永久卡住。异步图文 PPT 任务里这会让"做好啦"推回永久
    丢失(用户感知"桌宠不理我了")。改子进程隔离 + 超时,渲染再怎么挂也
    不阻塞调用方。返回 out_dir 里实际产出的 png(超时也返回已完成的部分)。
    从不抛异常。
    """
    import subprocess
    import sys

    try:
        if not com_render_available():
            return []
        dest = Path(out_dir).expanduser().resolve()
        dest.mkdir(parents=True, exist_ok=True)
        backend_dir = Path(__file__).resolve().parents[2]  # backend/
        proc_args = [
            sys.executable, "-m", "deskpet.tools.ppt_render",
            str(Path(pptx_path).expanduser().resolve()), str(dest),
            str(int(width)), str(int(height)), str(int(max_slides)),
        ]
        try:
            subprocess.run(
                proc_args, cwd=str(backend_dir),
                capture_output=True, timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            log_warn = True
            _kill_wps()
        except Exception:  # noqa: BLE001
            pass
        # 读 out_dir 里实际产出的 png(超时也能拿到已渲染的前几页)
        return sorted(
            str(p) for p in dest.glob("slide*.png")
            if p.is_file() and p.stat().st_size > 1024
        )
    except Exception:  # noqa: BLE001
        return []


def render_pptx_to_pngs(
    pptx_path: str,
    out_dir: str,
    *,
    width: int = 1280,
    height: int = 720,
    max_slides: int = 30,
) -> list[str]:
    """Render each PPTX slide to PNG paths, returning only successful files."""
    if not com_render_available():
        return []

    app = None
    pres = None
    initialized = False
    exported: list[str] = []
    try:
        src = Path(pptx_path).expanduser().resolve()
        if not src.is_file():
            return []
        dest = Path(out_dir).expanduser().resolve()
        dest.mkdir(parents=True, exist_ok=True)

        pythoncom.CoInitialize()
        initialized = True
        app = win32com.client.Dispatch("Kwpp.Application")
        pres = app.Presentations.Open(str(src), True, False, False)
        count = min(int(pres.Slides.Count), max(0, int(max_slides)))
        for idx in range(1, count + 1):
            png = dest / f"slide{idx}.png"
            try:
                pres.Slides(idx).Export(str(png), "PNG", int(width), int(height))
            except Exception:  # noqa: BLE001
                continue
            try:
                if png.is_file() and png.stat().st_size > 1024:
                    exported.append(str(png))
            except OSError:
                continue
        return exported
    except Exception:  # noqa: BLE001
        return []
    finally:
        if pres is not None:
            try:
                pres.Close()
            except Exception:  # noqa: BLE001
                pass
        if app is not None:
            try:
                app.Quit()
            except Exception:  # noqa: BLE001
                pass
        if initialized:
            try:
                pythoncom.CoUninitialize()
            except Exception:  # noqa: BLE001
                pass


if __name__ == "__main__":
    # 被 render_pptx_to_pngs_safe 当子进程调用: <pptx> <out_dir> [w h max]
    import sys as _sys

    _a = _sys.argv
    if len(_a) >= 3:
        _w = int(_a[3]) if len(_a) > 3 else 1280
        _h = int(_a[4]) if len(_a) > 4 else 720
        _ms = int(_a[5]) if len(_a) > 5 else 30
        render_pptx_to_pngs(_a[1], _a[2], width=_w, height=_h, max_slides=_ms)
