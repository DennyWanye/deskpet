# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""系统 Edge + CDP 的 JS 渲染适配器（deep-research）。

用系统已安装的 Microsoft Edge 常驻无头进程渲染 JS/SPA 页面，再经 CDP
读取 ``document.documentElement.outerHTML``。全程 best-effort：任何失败都返回
``None``，调用方可自然降级到静态抓取链路。
"""
from __future__ import annotations

import asyncio
import json
import platform
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any, Optional

import structlog
import websockets

log = structlog.get_logger(__name__)

_UNSET = object()
_EDGE_EXE_CACHE: object = _UNSET

_proc: Optional[subprocess.Popen[bytes]] = None
_ws: Any = None
_recv_task: Optional[asyncio.Task[None]] = None
_port: Optional[int] = None
_user_data_dir: Optional[str] = None

_next_msg_id = 0
_pending: dict[int, asyncio.Future[dict[str, Any]]] = {}

_startup_lock = asyncio.Lock()
_send_lock = asyncio.Lock()
_render_semaphore = asyncio.Semaphore(2)


def _find_edge_executable() -> Optional[str]:
    """按平台查找 Edge 可执行文件。"""
    system = platform.system()
    if system == "Windows":
        candidates = (
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        )
        for path in candidates:
            if Path(path).is_file():
                return path
        return shutil.which("msedge")
    if system == "Darwin":
        path = "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"
        if Path(path).is_file():
            return path
        return shutil.which("microsoft-edge") or shutil.which("msedge")
    return shutil.which("microsoft-edge") or shutil.which("msedge")


def _edge_executable() -> Optional[str]:
    global _EDGE_EXE_CACHE
    if _EDGE_EXE_CACHE is _UNSET:
        _EDGE_EXE_CACHE = _find_edge_executable()
    return _EDGE_EXE_CACHE if isinstance(_EDGE_EXE_CACHE, str) else None


def cdp_edge_available() -> bool:
    """系统是否能找到 Edge 可执行文件（结果进程缓存）。"""
    return _edge_executable() is not None


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _read_json_url(url: str, timeout: float) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - 只读本机 CDP
        data = resp.read()
    obj = json.loads(data.decode("utf-8", "replace"))
    return obj if isinstance(obj, dict) else {}


def _start_edge_process(edge: str, port: int, user_data_dir: str) -> subprocess.Popen[bytes]:
    args = [
        edge,
        "--headless=new",
        "--disable-gpu",
        "--no-first-run",
        "--no-default-browser-check",
        "--remote-allow-origins=*",
        f"--remote-debugging-port={port}",
        f"--user-data-dir={user_data_dir}",
        "about:blank",
    ]
    creationflags = 0
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        creationflags = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
    return subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
    )


async def _wait_for_ws_url(port: int, timeout: float) -> str:
    deadline = time.perf_counter() + timeout
    last_error = ""
    while time.perf_counter() < deadline:
        try:
            info = await asyncio.to_thread(
                _read_json_url,
                f"http://127.0.0.1:{port}/json/version",
                2.0,
            )
            ws_url = info.get("webSocketDebuggerUrl")
            if isinstance(ws_url, str) and ws_url:
                return ws_url
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
        await asyncio.sleep(0.25)
    raise RuntimeError(f"edge cdp websocket unavailable: {last_error}")


def _browser_running() -> bool:
    return _proc is not None and _proc.poll() is None and _ws is not None


async def _recv_loop() -> None:
    """集中接收 CDP 消息，按 id 分发给等待中的 call。"""
    global _ws
    try:
        while _ws is not None:
            raw = await _ws.recv()
            msg = json.loads(raw)
            msg_id = msg.get("id")
            if isinstance(msg_id, int):
                fut = _pending.pop(msg_id, None)
                if fut is not None and not fut.done():
                    fut.set_result(msg)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        for fut in list(_pending.values()):
            if not fut.done():
                fut.set_exception(exc)
        _pending.clear()


async def _cdp_call(
    method: str,
    params: Optional[dict[str, Any]] = None,
    *,
    session_id: Optional[str] = None,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """发送一条 CDP 命令并等待响应。"""
    global _next_msg_id
    if _ws is None:
        raise RuntimeError("cdp websocket is not connected")
    loop = asyncio.get_running_loop()
    msg_id = 0
    try:
        async with _send_lock:
            _next_msg_id += 1
            msg_id = _next_msg_id
            fut: asyncio.Future[dict[str, Any]] = loop.create_future()
            _pending[msg_id] = fut
            payload: dict[str, Any] = {
                "id": msg_id,
                "method": method,
                "params": params or {},
            }
            if session_id:
                payload["sessionId"] = session_id
            await _ws.send(json.dumps(payload))
    except Exception:
        if msg_id:
            _pending.pop(msg_id, None)
        raise
    try:
        msg = await asyncio.wait_for(fut, timeout=timeout)
    except Exception:
        _pending.pop(msg_id, None)
        raise
    if "error" in msg:
        raise RuntimeError(str(msg["error"]))
    result = msg.get("result")
    return result if isinstance(result, dict) else {}


async def _reset_browser_state(*, remove_profile: bool = True) -> None:
    """内部清理；异常全部吞掉。"""
    global _proc, _ws, _recv_task, _port, _user_data_dir, _pending
    proc, ws, recv_task, user_data_dir = _proc, _ws, _recv_task, _user_data_dir
    _proc = None
    _ws = None
    _recv_task = None
    _port = None
    _user_data_dir = None
    for fut in list(_pending.values()):
        if not fut.done():
            fut.cancel()
    _pending.clear()

    if recv_task is not None:
        try:
            recv_task.cancel()
            await recv_task
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001
            pass
    if ws is not None:
        try:
            await ws.close()
        except Exception:  # noqa: BLE001
            pass
    if proc is not None:
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    await asyncio.to_thread(proc.wait, timeout=3.0)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    await asyncio.to_thread(proc.wait, timeout=3.0)
        except Exception:  # noqa: BLE001
            pass
    if remove_profile and user_data_dir:
        try:
            shutil.rmtree(user_data_dir, ignore_errors=True)
        except Exception:  # noqa: BLE001
            pass


async def _ensure_browser(timeout: float) -> None:
    """懒启动常驻 Edge，并连接浏览器级 CDP WebSocket。"""
    global _proc, _ws, _recv_task, _port, _user_data_dir
    if _browser_running():
        return
    async with _startup_lock:
        if _browser_running():
            return
        await _reset_browser_state()
        edge = _edge_executable()
        if not edge:
            raise RuntimeError("edge executable not found")
        _port = _free_port()
        _user_data_dir = tempfile.mkdtemp(prefix="deskpet_cdp_edge_")
        try:
            _proc = _start_edge_process(edge, _port, _user_data_dir)
            ws_url = await _wait_for_ws_url(_port, max(3.0, min(timeout, 12.0)))
            _ws = await websockets.connect(ws_url, max_size=50_000_000)
            _recv_task = asyncio.create_task(_recv_loop())
        except Exception:
            await _reset_browser_state()
            raise


def _remaining(deadline: float) -> float:
    return max(0.1, deadline - time.perf_counter())


async def _eval(
    expression: str,
    *,
    session_id: str,
    deadline: float,
) -> Any:
    # 经典 CDP 坑: Page.navigate 后页面正在导航,旧执行上下文被销毁、新上下文还没建好,
    # 此刻发的 Runtime.evaluate 收不到响应。用【短超时 + 重试】化解: 一次没响应就快速
    # 重发(等上下文就绪),而不是死等 5s 然后整个 render 失败(quotes/js 稳定 5.8s 超时即此)。
    last_exc: Optional[Exception] = None
    for _ in range(5):
        if time.perf_counter() >= deadline:
            break
        try:
            result = await _cdp_call(
                "Runtime.evaluate",
                {"expression": expression, "returnByValue": True},
                session_id=session_id,
                timeout=min(2.0, _remaining(deadline)),
            )
            return (result.get("result") or {}).get("value")
        except Exception as exc:  # noqa: BLE001 — 含 wait_for 超时,导航中重试
            last_exc = exc
            await asyncio.sleep(min(0.4, _remaining(deadline)))
    if last_exc is not None:
        raise last_exc
    return None


async def _render_once(url: str, timeout: float) -> Optional[str]:
    await _ensure_browser(timeout)
    deadline = time.perf_counter() + timeout
    target_id = ""
    session_id = ""
    try:
        target = await _cdp_call(
            "Target.createTarget",
            {"url": "about:blank"},
            timeout=min(5.0, _remaining(deadline)),
        )
        target_id = str(target.get("targetId") or "")
        if not target_id:
            raise RuntimeError("Target.createTarget returned no targetId")
        attached = await _cdp_call(
            "Target.attachToTarget",
            {"targetId": target_id, "flatten": True},
            timeout=min(5.0, _remaining(deadline)),
        )
        session_id = str(attached.get("sessionId") or "")
        if not session_id:
            raise RuntimeError("Target.attachToTarget returned no sessionId")

        await _cdp_call("Page.enable", session_id=session_id, timeout=min(5.0, _remaining(deadline)))
        await _cdp_call("Runtime.enable", session_id=session_id, timeout=min(5.0, _remaining(deadline)))
        # Page.navigate 在"提交导航"时才返回响应;慢站/走系统代理的外国站可能迟迟不提交
        # (实测 quotes.toscrape.com 经 Clash 代理 navigate 卡 5s+)。**非致命**: 超时也继续,
        # 靠下面的 readyState/innerText 轮询(deadline 界内)兜底——页面可能仍在后台加载,
        # 拿到啥算啥;真加载不出 → 轮询到 deadline → 返回空壳/None → 上层优雅降级到 jina/原结果。
        try:
            await _cdp_call(
                "Page.navigate",
                {"url": url},
                session_id=session_id,
                timeout=min(10.0, _remaining(deadline)),
            )
        except Exception as exc:  # noqa: BLE001
            log.debug("cdp_edge Page.navigate slow/timeout, continue polling", url=url, error=str(exc)[:80])

        # 先等主文档 readyState complete。
        while time.perf_counter() < deadline:
            state = await _eval("document.readyState", session_id=session_id, deadline=deadline)
            if state == "complete":
                break
            await asyncio.sleep(min(0.25, _remaining(deadline)))

        # 再给 SPA/fetch/异步 DOM 构建一点固定时间。
        await asyncio.sleep(min(2.5, _remaining(deadline)))

        # 文本长度稳定后再取 DOM，降低抓到半成品页面的概率。
        last_len: Optional[int] = None
        stable_hits = 0
        for _ in range(8):
            if time.perf_counter() >= deadline:
                break
            value = await _eval(
                "document.body ? document.body.innerText.length : 0",
                session_id=session_id,
                deadline=deadline,
            )
            text_len = int(value or 0)
            if last_len == text_len:
                stable_hits += 1
                if stable_hits >= 2:
                    break
            else:
                stable_hits = 0
                last_len = text_len
            await asyncio.sleep(min(0.5, _remaining(deadline)))

        html = await _eval(
            "document.documentElement ? document.documentElement.outerHTML : ''",
            session_id=session_id,
            deadline=deadline,
        )
        return html if isinstance(html, str) and html else None
    finally:
        if target_id:
            try:
                await _cdp_call(
                    "Target.closeTarget",
                    {"targetId": target_id},
                    timeout=min(3.0, _remaining(deadline)),
                )
            except Exception:  # noqa: BLE001
                pass


async def cdp_edge_render(url: str, *, timeout: float = 20.0) -> Optional[str]:
    """用常驻无头 Edge 经 CDP 渲染 url，返回渲染后的 outerHTML。

    best-effort：Edge 找不到、启动失败、CDP 失败、超时或任何异常都返回 ``None``，
    绝不向调用方抛异常。
    """
    started = time.perf_counter()
    try:
        if not url:
            raise RuntimeError("empty url")
        async with _render_semaphore:
            html = await asyncio.wait_for(_render_once(url, timeout), timeout=timeout)
        if not html:
            raise RuntimeError("empty html")
        elapsed = time.perf_counter() - started
        log.info("cdp_edge_render", url=url, chars=len(html), ms=int(elapsed * 1000), ok=True)
        return html
    except Exception as exc:  # noqa: BLE001
        # 只在**连接级致命错误**(进程死 / ws 断)才重置常驻浏览器;单次渲染超时/eval 失败
        # **不应**杀掉常驻 Edge —— 否则下一个 URL 冷启 10-17s,违背"单例常驻复用"(plan §2 决策)
        # 且 deep 档多 URL 叠加冷启会顶穿 300s。本次 target 已由 _render_once 的 finally 关掉,
        # 浏览器仍存活可复用。
        if not _browser_running():
            try:
                await _reset_browser_state()
            except Exception:  # noqa: BLE001
                pass
        log.info("cdp_edge_render", url=url, ok=False, error=(str(exc) or exc.__class__.__name__)[:120])
        return None


async def shutdown_cdp_edge() -> None:
    """清理常驻 Edge 子进程、CDP WebSocket 和临时 profile；幂等。"""
    try:
        await _reset_browser_state()
    except Exception:  # noqa: BLE001
        pass


__all__ = ["cdp_edge_render", "cdp_edge_available", "shutdown_cdp_edge"]
