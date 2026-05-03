"""P4-S19 BGE-M3 子进程 worker — 隔离 PyTorch+ctranslate2 segfault。

**绝对约束**：本文件**不** import 任何 deskpet 内部模块、faster_whisper、
ctranslate2、cosyvoice 或其他可能 transitively 拉 ctranslate2 的库。
否则 P4-S18 的 PyTorch+ctranslate2 共存 segfault 会复现。

## 架构

主进程（backend）通过 ``subprocess.Popen`` 启动这个 worker，stdin/stdout
JSON-RPC 通信。worker 独占 CUDA context，主进程的 ctranslate2 完全访问
不到 worker 的 PyTorch state。

## JSON-RPC 协议

- 每行一个 JSON object（line-delimited）
- Request:
    ```json
    {"id": 42, "method": "encode",
     "texts": ["hello", "你好"], "max_length": 512, "batch_size": 8}
    ```
- Response (success):
    ```json
    {"id": 42, "ok": true,
     "vectors": [[1024 floats], [1024 floats]],
     "elapsed_ms": 23.5}
    ```
- Response (error):
    ```json
    {"id": 42, "ok": false,
     "error": "OOM", "error_type": "RuntimeError"}
    ```
- Health probe: ``{"method": "ping"}`` →
  ``{"ok": true, "is_ready": true, "device": "cuda"}``
- Shutdown: ``{"method": "shutdown"}`` → exit 0

## 启动

::

    python -m deskpet.memory.embedder_worker \\
        --model-path "C:\\Users\\...\\bge-m3-int8" \\
        --device cuda

或用 stdin 一行 JSON 配置（兼容 spawn 时不传命令行参数的场景）。

## 错误隔离

- ``encode`` 抛错 → 写 error response 继续循环（不 crash worker）
- stdin EOF / SIGTERM → 优雅 exit
- 任何 segfault → OS 杀 worker，主进程通过 returncode 检测重启
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from pathlib import Path
from typing import Any

# Windows 默认 stdin/stdout 是 cp936/cp1252，中文 JSON 一进来就乱码 →
# tokenizer 把 mojibake 当不认识的字符 → "TextEncodeInput must be Union..."。
# 强制 UTF-8。
try:
    sys.stdin.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except AttributeError:  # pragma: no cover — pre-3.7
    pass

# !!! 不要 import deskpet 任何东西 !!!
# !!! 不要 import faster_whisper / ctranslate2 / cosyvoice !!!


_PROTOCOL_VERSION = "1.0"


def _emit(obj: dict[str, Any]) -> None:
    """写一条 JSON 到 stdout，立刻 flush。"""
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _emit_error(req_id: Any, exc: BaseException, kind: str = "encode") -> None:
    _emit(
        {
            "id": req_id,
            "ok": False,
            "error": str(exc),
            "error_type": type(exc).__name__,
            "kind": kind,
        }
    )


class _WorkerState:
    """承载已加载模型 + 设备 + 加载耗时。"""

    def __init__(self) -> None:
        self.model: Any = None
        self.device: str = "cpu"
        self.model_path: Path | None = None
        self.load_elapsed_ms: float = 0.0


def _load_model(state: _WorkerState, model_path: Path, device: str) -> None:
    """同步加载 BGE-M3。失败抛异常给 caller。"""
    # Lazy import — 让进程在没收到 model_path 之前不要拉 transformers/torch。
    # 这意味着 ping 等轻命令零开销。
    from FlagEmbedding import BGEM3FlagModel  # type: ignore

    t0 = time.perf_counter()
    use_fp16 = device == "cuda"
    state.model = BGEM3FlagModel(
        str(model_path),
        use_fp16=use_fp16,
        device=device,
    )
    state.device = device
    state.model_path = model_path
    state.load_elapsed_ms = (time.perf_counter() - t0) * 1000.0


def _handle_encode(state: _WorkerState, req: dict) -> None:
    if state.model is None:
        _emit_error(req.get("id"), RuntimeError("model not loaded"))
        return

    texts = req.get("texts") or []
    if not isinstance(texts, list) or not all(isinstance(t, str) for t in texts):
        _emit_error(req.get("id"), ValueError("texts must be list[str]"))
        return

    if not texts:
        _emit({"id": req.get("id"), "ok": True, "vectors": [], "elapsed_ms": 0.0})
        return

    batch_size = int(req.get("batch_size", 8))
    max_length = int(req.get("max_length", 512))

    try:
        import base64
        import numpy as np

        t0 = time.perf_counter()
        out = state.model.encode(
            texts,
            batch_size=batch_size,
            max_length=max_length,
            return_dense=True,
            return_sparse=False,
            return_colbert_vecs=False,
        )
        dense = out["dense_vecs"]
        # P4-S19 BANDWIDTH FIX: list[list[float]] JSON ≈ 10KB/句，asyncio
        # readline() 默认 buffer 64KB → 7+ 句 batch overflow
        # (LimitOverrunError). 改用 base64-encoded float32 raw bytes：
        # 1024 dim × 4 bytes = 4KB/句 raw → ~5.4KB/句 base64，远低于 limit。
        # 协议加 ``encoding`` / ``shape`` / ``dtype`` 字段标记。
        vec_arr = np.asarray(dense, dtype=np.float32)
        b64 = base64.b64encode(vec_arr.tobytes()).decode("ascii")
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        _emit(
            {
                "id": req.get("id"),
                "ok": True,
                "encoding": "base64-f32",
                "shape": list(vec_arr.shape),
                "vectors_b64": b64,
                "elapsed_ms": round(elapsed_ms, 2),
            }
        )
    except Exception as exc:  # noqa: BLE001
        _emit_error(req.get("id"), exc)


def _handle_ping(state: _WorkerState, req: dict) -> None:
    _emit(
        {
            "id": req.get("id"),
            "ok": True,
            "is_ready": state.model is not None,
            "device": state.device,
            "model_path": str(state.model_path) if state.model_path else "",
            "load_elapsed_ms": state.load_elapsed_ms,
            "protocol_version": _PROTOCOL_VERSION,
        }
    )


def _run_loop(state: _WorkerState) -> int:
    """主循环：一行 JSON 一条命令，直到 stdin EOF 或 shutdown。"""
    for raw_line in sys.stdin:
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            req = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            _emit_error(None, exc, kind="parse")
            continue

        method = req.get("method", "")
        if method == "encode":
            _handle_encode(state, req)
        elif method == "ping":
            _handle_ping(state, req)
        elif method == "shutdown":
            _emit({"id": req.get("id"), "ok": True, "shutting_down": True})
            return 0
        else:
            _emit_error(
                req.get("id"),
                ValueError(f"unknown method: {method!r}"),
                kind="dispatch",
            )

    # stdin EOF — caller closed pipe
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="P4-S19 BGE-M3 isolated worker (subprocess)"
    )
    parser.add_argument(
        "--model-path",
        type=Path,
        required=True,
        help="Absolute path to BGE-M3 model directory",
    )
    parser.add_argument(
        "--device",
        choices=["cuda", "cpu"],
        default="cuda",
        help="Where to load BGE-M3 (default: cuda)",
    )
    args = parser.parse_args(argv)

    # Heartbeat 1 — parent reads this to know spawn was successful BEFORE
    # the long FlagEmbedding/torch import chain. Helps diagnose silent
    # crashes where the child dies during import.
    _emit({"ok": True, "alive": True, "stage": "spawned"})

    state = _WorkerState()
    try:
        _load_model(state, args.model_path, args.device)
    except Exception as exc:  # noqa: BLE001
        # Load 失败：写一条 fatal error 然后退出。主进程会从 returncode≠0
        # 知道子进程没起来，回退 mock。
        _emit(
            {
                "ok": False,
                "fatal": True,
                "error": str(exc),
                "error_type": type(exc).__name__,
                "traceback": traceback.format_exc(),
                "kind": "load",
            }
        )
        return 1

    # 通知主进程 ready
    _emit(
        {
            "ok": True,
            "ready": True,
            "device": state.device,
            "model_path": str(state.model_path) if state.model_path else "",
            "load_elapsed_ms": round(state.load_elapsed_ms, 2),
            "protocol_version": _PROTOCOL_VERSION,
        }
    )

    return _run_loop(state)


if __name__ == "__main__":
    sys.exit(main())
