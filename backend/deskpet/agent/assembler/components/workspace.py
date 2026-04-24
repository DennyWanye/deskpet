"""Workspace component (P4-S7 task 12.5).

Emits a short summary of the user's workspace directory — file count,
total size, a handful of recent files. Scope: ``%APPDATA%\\deskpet\\workspace\\``
on Windows, XDG equivalents elsewhere (resolved by ``platformdirs``).

Lightweight by design: we enumerate the top level only (no recursion)
and cap at 20 entries. Anything deeper is the filesystem MCP's job (P4-S9).
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Optional

from deskpet.agent.assembler.bundle import Slice
from deskpet.agent.assembler.components.base import Component, ComponentContext


_MAX_ENTRIES = 20


class WorkspaceComponent:
    """Summarises the user's workspace directory.

    Parameters
    ----------
    workspace_dir:
        Override — normally ``None`` and the component resolves from
        ``config.workspace.dir`` or platformdirs default at provide() time.
    """

    name: str = "workspace"

    def __init__(self, workspace_dir: Optional[Path] = None) -> None:
        self._override = workspace_dir

    async def provide(self, ctx: ComponentContext) -> Slice:
        start = time.monotonic()
        path = self._resolve_dir(ctx)
        if path is None or not path.exists():
            return Slice(
                component_name=self.name,
                priority=40,
                bucket="dynamic",
                meta={"status": "missing", "path": str(path) if path else None},
            )

        try:
            entries = sorted(
                path.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True
            )[:_MAX_ENTRIES]
        except OSError as exc:
            return Slice(
                component_name=self.name,
                priority=40,
                bucket="dynamic",
                meta={"error": str(exc), "error_type": type(exc).__name__},
            )

        lines = [f"## 工作目录 ({path})"]
        if not entries:
            lines.append("(空)")
        for p in entries:
            try:
                size = p.stat().st_size if p.is_file() else None
                size_str = f" [{_fmt_size(size)}]" if size is not None else "/"
                lines.append(f"- {p.name}{size_str}")
            except OSError:
                continue

        text = "\n".join(lines)
        elapsed_ms = (time.monotonic() - start) * 1000.0
        return Slice(
            component_name=self.name,
            text_content=text,
            tokens=max(1, len(text) // 4),
            priority=40,
            bucket="dynamic",
            meta={
                "path": str(path),
                "entries": len(entries),
                "latency_ms": round(elapsed_ms, 2),
            },
        )

    def _resolve_dir(self, ctx: ComponentContext) -> Optional[Path]:
        if self._override is not None:
            return self._override
        ws = ctx.config.get("workspace") if isinstance(ctx.config, dict) else None
        if isinstance(ws, dict):
            dir_val = ws.get("dir")
            if isinstance(dir_val, str) and dir_val:
                return Path(os.path.expandvars(dir_val)).expanduser()
        # Default: platformdirs — lazy import to keep the hot path lean
        try:
            from platformdirs import user_data_dir  # type: ignore
        except Exception:
            return None
        return Path(user_data_dir("deskpet", appauthor=False)) / "workspace"


def _fmt_size(size: Optional[int]) -> str:
    if size is None:
        return ""
    if size < 1024:
        return f"{size}B"
    if size < 1024 * 1024:
        return f"{size // 1024}KB"
    return f"{size // (1024 * 1024)}MB"


_ASSERT_PROTOCOL: Component = WorkspaceComponent()
