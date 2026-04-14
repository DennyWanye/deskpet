"""Clipboard read tool (V5 §9 tools/ directory).

Returns the current clipboard text so the LLM can quote or act on whatever
the user just copied. Read-only — no write path, which keeps this in the
low-risk tier (``requires_confirmation=False``).

Implementation notes:
- We never import ``tkinter`` or a GUI toolkit at module load time; those
  pop a window on some platforms. Import lazily inside ``invoke``.
- On Windows we prefer ``ctypes`` calls against the Win32 clipboard so the
  tool works even when no Tk interpreter is present.
- Failures are swallowed into a human-readable string, never an exception,
  so the LLM stream stays well-formed.
"""
from __future__ import annotations

import sys

from tools.base import Tool, ToolSpec


class ReadClipboardTool:
    spec = ToolSpec(
        name="read_clipboard",
        description="Returns the current system clipboard contents as text.",
    )

    async def invoke(self, **kwargs: object) -> str:
        try:
            if sys.platform.startswith("win"):
                return _read_clipboard_windows()
            # Cross-platform fallback via tkinter (stdlib on macOS/Linux).
            return _read_clipboard_tk()
        except Exception as exc:
            return f"<clipboard read failed: {exc}>"


def _read_clipboard_windows() -> str:
    """Pull CF_UNICODETEXT from the Win32 clipboard via ctypes."""
    import ctypes
    from ctypes import wintypes

    CF_UNICODETEXT = 13
    user32 = ctypes.WinDLL("user32")
    kernel32 = ctypes.WinDLL("kernel32")

    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.GetClipboardData.argtypes = [wintypes.UINT]
    user32.GetClipboardData.restype = wintypes.HANDLE
    user32.CloseClipboard.argtypes = []
    user32.CloseClipboard.restype = wintypes.BOOL
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalUnlock.restype = wintypes.BOOL

    if not user32.OpenClipboard(0):
        return "<clipboard unavailable>"
    try:
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return ""
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            return ""
        try:
            text = ctypes.c_wchar_p(ptr).value or ""
        finally:
            kernel32.GlobalUnlock(handle)
        return text
    finally:
        user32.CloseClipboard()


def _read_clipboard_tk() -> str:
    """Fallback via tkinter's hidden-root clipboard — stdlib, no extra deps."""
    import tkinter
    root = tkinter.Tk()
    root.withdraw()
    try:
        data = root.clipboard_get()
    except tkinter.TclError:
        data = ""
    finally:
        root.destroy()
    return data


read_clipboard_tool: Tool = ReadClipboardTool()
