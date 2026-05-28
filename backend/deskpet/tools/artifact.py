# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""ToolArtifact — 工具产物统一信封（PRD §3 D1）。

WI-T1.1 — Stage 1 第一基石。所有产生"用户可操作产物"的工具，由 registry
统一在 envelope 中追加 ``artifacts: list[dict]`` 字段（flag 控制）。

**字节级一致硬保证**：
  - flag OFF 时（``artifact_envelope=False``）envelope dict **不得 emit**
    ``artifacts`` 键 —— 不是空数组，是缺键（TG-2 T2-5b / TG-12 T12-1）。
  - flag ON 但无可提取 path/url 时同样不加键。

**与 ToolReceipt 的关系**：
  - ``ToolArtifact.sha256`` 由 sha256_file_async 异步计算（PRD D5 ＋ N4）。
  - 大文件（> 10MB）走线程池避免阻塞主循环（TG-2 T2-8）。
  - 30s 超时 → sha256 字段留空，receipt 由调用方标 ``sha256_pending`` 走
    VerifyGate 放行路径（PRD D6 末段 + TG-9 T9-16）。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional

logger = logging.getLogger(__name__)


# ─── 常量 ─────────────────────────────────────────────────────

#: 单 artifact preview 字段硬上限（PRD §3 D1 + TG-2 T2-4）
PREVIEW_MAX_CHARS: int = 2048

#: sha256 走线程池的文件大小阈值（PRD §3 D5）
SHA256_EXECUTOR_THRESHOLD_BYTES: int = 10 * 1024 * 1024  # 10MB

#: sha256 默认超时（秒）（PRD §3 D5）
SHA256_DEFAULT_TIMEOUT_S: float = 30.0

#: 可能落在工具 result 里的"产物路径"字段名（按优先级）
_PATH_KEYS = ("path", "file_path", "output_path", "output", "saved_to")
_URL_KEYS = ("url", "download_url", "image_url")

#: 文件扩展名 → MIME 推断（用于前端 icon；不全也无所谓）
_EXT_TO_MIME: dict[str, str] = {
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".svg": "image/svg+xml",
    ".md": "text/markdown",
    ".txt": "text/plain",
    ".json": "application/json",
    ".csv": "text/csv",
}


# ─── ArtifactAction ──────────────────────────────────────────

ActionId = Literal["open", "show_in_folder", "copy_path", "save_as", "preview"]


@dataclass
class ArtifactAction:
    """前端按 kind 默认 + 工具可显式覆盖。"""
    id: ActionId
    label: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "label": self.label or ""}


def _default_actions_for(kind: str) -> list[ArtifactAction]:
    """按 kind 给默认 actions（D2 capability negotiation 的 fallback）。"""
    if kind == "file":
        return [
            ArtifactAction(id="open", label="打开"),
            ArtifactAction(id="show_in_folder", label="在文件夹中显示"),
            ArtifactAction(id="copy_path", label="复制路径"),
            ArtifactAction(id="save_as", label="另存为"),
        ]
    if kind == "url":
        return [ArtifactAction(id="open", label="在浏览器中打开"),
                ArtifactAction(id="copy_path", label="复制链接")]
    if kind == "text":
        return [ArtifactAction(id="preview", label="预览"),
                ArtifactAction(id="copy_path", label="复制内容")]
    if kind == "image":
        return [ArtifactAction(id="open", label="打开"),
                ArtifactAction(id="show_in_folder", label="在文件夹中显示"),
                ArtifactAction(id="save_as", label="另存为")]
    if kind == "table":
        return [ArtifactAction(id="preview", label="预览")]
    return []


# ─── ToolArtifact ────────────────────────────────────────────

Kind = Literal["file", "url", "text", "image", "table"]


@dataclass
class ToolArtifact:
    """工具产物统一信封 — 详见 PRD §3 D1。

    字段顺序必须稳定（用于字节级 golden file 对账，TG-12 T12-1）。
    """
    kind: Kind
    path: Optional[str] = None
    url: Optional[str] = None
    mime: Optional[str] = None
    title: str = ""
    preview: Optional[str] = None
    size_bytes: Optional[int] = None
    sha256: Optional[str] = None
    created_at: str = ""
    actions: list[ArtifactAction] = field(default_factory=list)

    def __post_init__(self) -> None:
        # kind 约束（PRD §3.1 IDL allOf）
        if self.kind == "file" and not self.path:
            raise ValueError(f"kind='file' requires path; got path={self.path!r}")
        if self.kind == "url" and not self.url:
            raise ValueError(f"kind='url' requires url; got url={self.url!r}")
        # preview 自动截断（T2-4）
        if self.preview is not None and len(self.preview) > PREVIEW_MAX_CHARS:
            cut_at = PREVIEW_MAX_CHARS - len("…(truncated)")
            self.preview = self.preview[:cut_at] + "…(truncated)"
        # 默认 title — basename(path) / url / "untitled"
        if not self.title:
            if self.path:
                self.title = os.path.basename(self.path) or "untitled"
            elif self.url:
                self.title = self.url
            else:
                self.title = "untitled"
        # 默认 created_at = now（UTC, ISO-8601 with Z）
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
        # 默认 mime（仅 file）
        if self.kind in ("file", "image") and self.path and not self.mime:
            ext = Path(self.path).suffix.lower()
            self.mime = _EXT_TO_MIME.get(ext)
        # 默认 actions（按 kind）
        if not self.actions:
            self.actions = _default_actions_for(self.kind)

    def to_dict(self) -> dict[str, Any]:
        """字段顺序稳定的 dict 表示。

        None 字段**保留为 null**（不省略），前端 type signature 稳定；
        actions 序列化为 [{id, label}, ...]。
        """
        d = asdict(self)
        d["actions"] = [a if isinstance(a, dict) else a.to_dict()
                        if hasattr(a, "to_dict") else dict(a)
                        for a in self.actions]
        # asdict 已展开 ArtifactAction 为 dict，但 .to_dict() 路径更稳；
        # 这里兜底确保 list[dict]。
        return d


# ─── sha256 异步 ──────────────────────────────────────────────

def _sha256_file_sync(path: Path) -> str:
    """同步算 sha256（线程池里调）。读 1MB chunk 防 OOM。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


async def sha256_file_async(
    path: Path,
    timeout_s: float = SHA256_DEFAULT_TIMEOUT_S,
) -> Optional[str]:
    """异步算 sha256，大文件走线程池避免阻塞主循环（PRD D5）。

    Returns:
        hex digest 字符串；超时 → ``None``（调用方据此标 ``sha256_pending``）。
    """
    if not path.exists() or not path.is_file():
        return None
    try:
        size = path.stat().st_size
    except OSError:
        return None

    loop = asyncio.get_running_loop()
    fut: asyncio.Future[str]
    if size <= SHA256_EXECUTOR_THRESHOLD_BYTES:
        # 小文件直接同步算（< 10MB，~30ms 量级）
        # 仍包一层 executor 让 await 语义统一 + 不阻塞 event loop tick
        fut = loop.run_in_executor(None, _sha256_file_sync, path)
    else:
        # 大文件强制走 executor
        fut = loop.run_in_executor(None, _sha256_file_sync, path)

    try:
        return await asyncio.wait_for(fut, timeout=timeout_s)
    except asyncio.TimeoutError:
        logger.warning(
            "sha256_file_async timed out after %.2fs: %s (size=%d)",
            timeout_s, path, size,
        )
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("sha256_file_async error: %s — %s", path, exc)
        return None


# ─── result JSON → ToolArtifact 推断 ─────────────────────────

def extract_artifacts_from_result(
    *,
    tool_name: str,
    result_json: str,
) -> list[ToolArtifact]:
    """从工具 result JSON 字符串推断 ToolArtifact 列表。

    向后兼容设计（PRD D1 末段）：工具尚未显式 emit ``artifacts`` 字段时，
    registry 自动从 ``path`` / ``url`` 字段推断单个 file/url artifact。

    Returns:
        artifact 列表（可能为空——无可识别字段时；调用方据此决定是否加键）。
    """
    try:
        result_obj: Any = json.loads(result_json) if result_json else None
    except (TypeError, ValueError):
        return []
    if not isinstance(result_obj, dict):
        return []
    if result_obj.get("ok") is False:
        return []  # 失败的工具不产 artifact

    # 优先用工具显式 emit 的 artifacts[]（D1 一等公民路径）
    explicit = result_obj.get("artifacts")
    if isinstance(explicit, list) and explicit:
        out: list[ToolArtifact] = []
        for item in explicit:
            if not isinstance(item, dict):
                continue
            try:
                out.append(_artifact_from_dict(item))
            except (ValueError, TypeError) as exc:
                logger.warning(
                    "extract_artifacts: tool %r emit_invalid_artifact: %s",
                    tool_name, exc,
                )
        return out

    # 兜底：从 path / url 字段推断
    for key in _PATH_KEYS:
        val = result_obj.get(key)
        if isinstance(val, str) and val:
            return [ToolArtifact(kind="file", path=val)]
    for key in _URL_KEYS:
        val = result_obj.get(key)
        if isinstance(val, str) and val:
            return [ToolArtifact(kind="url", url=val)]
    return []


def _artifact_from_dict(d: dict[str, Any]) -> ToolArtifact:
    """从工具显式 emit 的 dict 构造 ToolArtifact（验完整字段）。"""
    kind = d.get("kind", "file")
    return ToolArtifact(
        kind=kind,
        path=d.get("path"),
        url=d.get("url"),
        mime=d.get("mime"),
        title=d.get("title", ""),
        preview=d.get("preview"),
        size_bytes=d.get("size_bytes"),
        sha256=d.get("sha256"),
        created_at=d.get("created_at", ""),
    )


# ─── registry 包装 helper ────────────────────────────────────

def maybe_add_artifacts(
    *,
    envelope: dict[str, Any],
    tool_name: str,
    enable: bool,
) -> dict[str, Any]:
    """在 envelope 上**条件追加** ``artifacts`` 键。

    **字节级一致硬保证**（TG-2 T2-5b）：
      - ``enable=False`` 或推断 0 artifact → 返回原 envelope（不加键）
      - ``enable=True`` 且推断 ≥ 1 → 追加 ``artifacts: list[dict]``
    """
    if not enable:
        return envelope
    result = envelope.get("result")
    if not isinstance(result, str):
        return envelope
    arts = extract_artifacts_from_result(tool_name=tool_name, result_json=result)
    if not arts:
        return envelope  # 0 artifact → 不加键
    envelope["artifacts"] = [a.to_dict() for a in arts]
    return envelope
