# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""首启模型自动下载（thin NSIS bundle 配套 — Option A / 2026-06-05）。

装机包不再内嵌 ~2.6GB 模型（PyInstaller spec `DESKPET_BUNDLE_MODELS=0`），
首次启动时 `user_models_dir()` 是空的。本模块在 backend 启动后用一个后台线程
检查缺哪个模型，缺则**直接 HTTP 从自建 COS 下载**，进度经 control-WS
`model_provision_status` 暴露给前端首启进度卡片。

为什么走 COS 直下而不是 huggingface_hub：实测 hf-mirror 与 huggingface_hub
0.36.x 的下载 API 不兼容（元数据 HEAD 调用失败，关 Xet 也无效），而 hf.co
直连在国内被墙。自建 COS（公有读）= 国内快 + 全控 + 无第三方元数据层 + 与
安装包/latest.json 同一套基建。下载用 stdlib urllib，不引新依赖。

每个模型在 COS 上有一份 ``manifest.json`` 列出相对文件路径 + 字节数（由发布
脚本在上传时生成），provisioner 先取 manifest 拿到真实总量再逐文件下载。

目标目录走 `paths.user_models_dir()`（尊重 `DESKPET_MODEL_ROOT` 等 override），
与运行时 `paths.resolve_model_dir` 实际读取路径严格一致——避免跨层目录漂移。
未就绪期间 ASR / 记忆走各自的优雅降级，不阻塞 backend 启动。
"""
from __future__ import annotations

import json
import os
import threading
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import structlog

from paths import user_models_dir

logger = structlog.get_logger(__name__)

#: COS 上模型根 URL（公有读）。可经 ``DESKPET_MODEL_CDN_BASE`` env 覆盖。
#: 布局：``<base>/<subdir>/manifest.json`` + ``<base>/<subdir>/<相对路径>``。
DEFAULT_CDN_BASE = (
    "https://your-cdn.example.com/deskpet/models"
)

#: (子目录, 就绪哨兵文件)。哨兵存在即视为就绪；None 表示"目录非空即就绪"。
#: 子目录名与 `paths.resolve_model_dir` 期望的一致。
_MODELS: tuple[tuple[str, Optional[str]], ...] = (
    ("bge-m3-int8", None),
    ("faster-whisper-large-v3-turbo", "model.bin"),
)

#: 抓取一个 URL → bytes。可注入以便单测不联网。
FetchUrl = Callable[[str], bytes]


def _http_get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "deskpet-provisioner"})
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 — 固定 https COS
        return resp.read()


@dataclass
class ProvisionStatus:
    """供前端渲染的快照。state 机：idle→checking→downloading→ready/error。"""

    state: str = "idle"
    current: Optional[str] = None  # 正在下载的模型子目录名
    index: int = 0                 # 1-based：当前是第几个
    total: int = 0                 # 本次需下载的模型总数
    downloaded_bytes: int = 0      # 当前模型已下载（按磁盘实时大小估）
    total_bytes: int = 0           # 当前模型总字节（来自 manifest，0=未知）
    error: Optional[str] = None

    def as_dict(self) -> dict:
        return {
            "state": self.state,
            "current": self.current,
            "index": self.index,
            "total": self.total,
            "downloaded_bytes": self.downloaded_bytes,
            "total_bytes": self.total_bytes,
            "error": self.error,
        }


def _dir_size(path: Path) -> int:
    """目录下所有文件字节数之和（best-effort，下载中目录在变所以忽略错误）。"""
    total = 0
    try:
        for p in path.rglob("*"):
            try:
                if p.is_file():
                    total += p.stat().st_size
            except OSError:
                continue
    except OSError:
        pass
    return total


class ModelProvisioner:
    """检查并（必要时）从 COS 下载首启模型。线程安全的状态快照供 IPC 读取。"""

    def __init__(
        self,
        models_dir: Optional[Path] = None,
        cdn_base: Optional[str] = None,
        fetch_url: Optional[FetchUrl] = None,
    ) -> None:
        # models_dir=None → 运行时解析（尊重 env override）。测试可注入临时目录。
        self._models_dir_override = Path(models_dir) if models_dir else None
        self._cdn_base = (cdn_base or os.environ.get("DESKPET_MODEL_CDN_BASE") or DEFAULT_CDN_BASE).rstrip("/")
        # fetch_url 可注入以便单测不真的联网；默认走 stdlib urllib。
        self._fetch: FetchUrl = fetch_url or _http_get
        self._status = ProvisionStatus()
        self._lock = threading.Lock()
        self._started = False

    def __deepcopy__(self, memo):
        # 进程单例：ServiceContext.create_session() 会 deepcopy 整个 context，
        # 而本对象持有不可 deepcopy 的 threading.Lock + 后台线程。返回自身，
        # 让所有 per-session context 共享同一个 provisioner（语义也正确）。
        return self

    # ---- 路径 ----------------------------------------------------------
    def _models_dir(self) -> Path:
        return self._models_dir_override if self._models_dir_override is not None else user_models_dir()

    def _target(self, subdir: str) -> Path:
        return self._models_dir() / subdir

    def _is_ready(self, subdir: str, sentinel: Optional[str]) -> bool:
        d = self._target(subdir)
        if not d.is_dir():
            return False
        if sentinel:
            return (d / sentinel).is_file()
        try:
            return any(d.iterdir())
        except OSError:
            return False

    def missing(self) -> list[tuple[str, Optional[str]]]:
        return [m for m in _MODELS if not self._is_ready(m[0], m[1])]

    # ---- 状态 ----------------------------------------------------------
    def status(self) -> dict:
        with self._lock:
            snap = self._status
            # 下载中时按磁盘实时大小估算 downloaded_bytes（按需计算，无需轮询线程）。
            if snap.state == "downloading" and snap.current:
                snap_dict = snap.as_dict()
                snap_dict["downloaded_bytes"] = _dir_size(self._target(snap.current))
                return snap_dict
            return snap.as_dict()

    def _update(self, **kw) -> None:
        with self._lock:
            for k, v in kw.items():
                setattr(self._status, k, v)

    # ---- 下载 ----------------------------------------------------------
    def _provision_one(self, subdir: str, target: Path) -> None:
        """取 COS manifest → 逐文件下载到 target。"""
        base = f"{self._cdn_base}/{subdir}"
        manifest = json.loads(self._fetch(f"{base}/manifest.json").decode("utf-8"))
        files = manifest.get("files", [])
        total_bytes = int(manifest.get("total_bytes") or sum(int(f.get("size", 0)) for f in files))
        self._update(total_bytes=total_bytes)
        target.mkdir(parents=True, exist_ok=True)
        for f in files:
            rel = f["path"].replace("\\", "/")
            dest = target / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            data = self._fetch(f"{base}/{rel}")
            dest.write_bytes(data)

    def _run(self) -> None:
        try:
            self._update(state="checking", error=None)
            missing = self.missing()
            if not missing:
                self._update(state="ready", total=0, current=None)
                logger.info("model_provision_already_ready")
                return

            self._update(state="downloading", total=len(missing))
            logger.info("model_provision_start", count=len(missing), base=self._cdn_base)

            for i, (subdir, _sentinel) in enumerate(missing, start=1):
                self._update(current=subdir, index=i, downloaded_bytes=0, total_bytes=0)
                logger.info("model_provision_downloading", model=subdir, index=i, total=len(missing))
                self._provision_one(subdir, self._target(subdir))
                logger.info("model_provision_model_done", model=subdir)

            self._update(state="ready", current=None)
            logger.info("model_provision_done")
        except Exception as exc:  # noqa: BLE001 — 任何失败都转成 error 状态，不崩 backend
            logger.warning("model_provision_failed", error=str(exc), error_type=type(exc).__name__)
            self._update(state="error", error=str(exc))

    def start_background(self) -> None:
        """幂等：起一个 daemon 线程跑 `_run`。已起过则忽略。"""
        with self._lock:
            if self._started:
                return
            self._started = True
        threading.Thread(target=self._run, name="model-provisioner", daemon=True).start()
