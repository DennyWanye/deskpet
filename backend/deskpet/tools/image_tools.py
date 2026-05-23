"""generate_image tool — text→image via the existing chinzy endpoint.

User says 生成图片 → agent calls this tool → POST
{base_url}/images/generations with model gpt-image-2 (reusing the exact
base_url+api_key the working chat path uses) → PNG saved into the
workspace folder → opened with the OS default viewer.

Flat auto-discovered module (same as web_tools.py / file_tools.py):
``registry.register`` at module scope, zero main.py change.

Name MUST stay ``generate_image`` — it equals capability_gate.py image
``tool_markers[0]``, so registering it flips image requests
REFUSE→PASS (consistent with companion-context-isolation §D2:
"新增图像工具自动放行"). Plan: plans/2026-05-16-generate-image-tool.md
"""
from __future__ import annotations

import base64
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx

from .registry import registry

_DEFAULT_MODEL = "gpt-image-2"
_DEFAULT_SIZE = "1024x1024"
# 2026-05-16 timeout 协调：之前 per-request=registry=120s → 单次慢出图
# 或一次重试就被 registry 的 asyncio.wait_for(120s) 砍掉，重试形同虚设。
# 现在 per-HTTP-attempt=100s，最多 2 次（1 次重试足够接住 chinzy 瞬时
# 断连——实测断连发生在 ~62s），registry 总超时另设 _TOOL_TIMEOUT_S
# 覆盖 2×100 + 退避，保证重试能真正跑完。
_TIMEOUT_S = 100.0          # 单次 HTTP 请求超时
_MAX_ATTEMPTS = 2           # 总尝试次数（1 次重试）
_RETRY_BACKOFF = (5.0,)     # attempt 2 前退避
# 注册到 ToolRegistry 的总超时：必须 > 最坏重试预算
# (2×100 + 5 = 205) 否则 registry 会在重试跑完前杀掉 handler。
_TOOL_TIMEOUT_S = 240.0

_SCHEMA: dict[str, Any] = {
    "name": "generate_image",
    "description": (
        "根据文字描述生成一张图片（文生图）。用户说“生成图片/画一张/做个海报”"
        "等时调用。图片会存到 workspace 目录并自动用系统默认看图器打开。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "图片内容的文字描述（中英文均可，越具体越好）。",
            },
            "size": {
                "type": "string",
                "description": '图片尺寸，默认 "1024x1024"。',
            },
        },
        "required": ["prompt"],
    },
}


def _err(error: str, hint: str, **extra: Any) -> str:
    body: dict[str, Any] = {"ok": False, "error": error, "hint": hint}
    body.update(extra)
    return json.dumps(body, ensure_ascii=False)


def _resolve_endpoint() -> tuple[str, str | None]:
    """(base_url, api_key) — the SAME creds the working chat path uses.

    Priority: ``<user_data>/llm_runtime.json`` (base_url + api_key the
    user actually configured for chinzy) → config.toml ``[llm]`` +
    ``resolve_cloud_api_key()``. Lazy imports keep tool auto-discovery
    free of import-order coupling.
    """
    base_url = ""
    api_key: str | None = None
    try:
        from paths import user_data_dir  # type: ignore[import-not-found]

        rt = user_data_dir() / "llm_runtime.json"
        if rt.exists():
            data = json.loads(rt.read_text(encoding="utf-8"))
            base_url = str(data.get("base_url") or "")
            api_key = data.get("api_key") or None
    except Exception:  # noqa: BLE001 — fall through to config
        pass

    if not base_url:
        try:
            import config as _cfg  # type: ignore[import-not-found]

            base_url = str(
                getattr(getattr(_cfg.config, "llm", None), "local", None)
                and _cfg.config.llm.local.base_url
                or (_cfg.config.raw.get("llm") or {}).get("base_url", "")
            )
        except Exception:  # noqa: BLE001
            base_url = ""
    if not api_key:
        try:
            from config import resolve_cloud_api_key  # type: ignore

            api_key = resolve_cloud_api_key()
        except Exception:  # noqa: BLE001
            api_key = None

    return base_url.rstrip("/"), api_key


def _image_model() -> str:
    try:
        import config as _cfg  # type: ignore[import-not-found]

        return str(
            (_cfg.config.raw.get("image") or {}).get("model")
            or _DEFAULT_MODEL
        )
    except Exception:  # noqa: BLE001
        return _DEFAULT_MODEL


def _workspace_dir() -> Path:
    from paths import user_data_dir  # type: ignore[import-not-found]

    ws = user_data_dir() / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    return ws


def _open_file(path: Path) -> bool:
    """Best-effort open with the OS default app. Never raises."""
    try:
        if sys.platform == "win32":
            os.startfile(str(path))  # noqa: S606 — desktop pet, intended
            return True
        import subprocess

        opener = "open" if sys.platform == "darwin" else "xdg-open"
        subprocess.Popen([opener, str(path)])  # noqa: S603
        return True
    except Exception:  # noqa: BLE001
        return False


def _generate_png(
    prompt: str, size: str, model: str
) -> tuple[bytes | None, str | None]:
    """Blocking: chinzy POST + transient-retry → (png_bytes, None) on
    success, or (None, error_hint) on failure. No file IO. Reused by
    BOTH the legacy sync handler and the async ImageGenerationWorker —
    single copy of the slow logic. Never raises.
    """
    base_url, api_key = _resolve_endpoint()
    if not base_url:
        return None, (
            "没解析到图像生成 endpoint（llm_runtime.json / config 都没有 "
            "base_url）。请先在设置里配好 LLM endpoint。"
        )

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    # 2026-05-16 中专站确认契约：OpenAI 标准 Images API，显式请求
    # b64_json（规格明确要求；服务端把 n 钳到 [1,10]）。
    payload = {
        "model": model,
        "prompt": prompt,
        "size": size,
        "n": 1,
        "response_format": "b64_json",
    }

    # chinzy 上游对慢/复杂出图会瞬时断连（RemoteProtocolError
    # "Server disconnected"）。断连/超时/5xx 重试带退避；4xx 确定性
    # 错误立即返回不重试。
    last_transient = ""
    for _attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            with httpx.Client(timeout=_TIMEOUT_S) as cli:
                resp = cli.post(
                    f"{base_url}/images/generations",
                    json=payload,
                    headers=headers,
                )
            if resp.status_code in (502, 503, 504):
                last_transient = f"HTTP {resp.status_code}（上游网关瞬时）"
            elif resp.status_code != 200:
                detail = ""
                try:
                    detail = json.dumps(resp.json(), ensure_ascii=False)[:300]
                except Exception:  # noqa: BLE001
                    detail = ""
                return None, (  # 4xx 确定性错误：不重试
                    f"图像接口 HTTP {resp.status_code}：可能是 model 不支持、"
                    f"额度或参数问题。响应：{detail}"
                )
            else:
                data = (resp.json() or {}).get("data") or []
                if not data:
                    return None, "接口 200 但 data 为空，无法取回图片。"
                item = data[0]
                if item.get("b64_json"):
                    return base64.b64decode(item["b64_json"]), None
                if item.get("url"):
                    with httpx.Client(timeout=_TIMEOUT_S) as cli:
                        dl = cli.get(item["url"])
                    if dl.status_code != 200:
                        return None, f"取回图片 URL 失败 HTTP {dl.status_code}。"
                    return dl.content, None
                return None, "响应里既没有 b64_json 也没有 url，无法保存图片。"
        except (
            httpx.TimeoutException,
            httpx.RemoteProtocolError,
            httpx.ConnectError,
            httpx.ReadError,
            httpx.WriteError,
            httpx.PoolTimeout,
        ) as exc:
            last_transient = f"{type(exc).__name__}: {exc}"
        except httpx.HTTPError as exc:
            last_transient = f"{type(exc).__name__}: {exc}"
        except Exception as exc:  # noqa: BLE001 — never raise
            return None, f"未预期错误：{type(exc).__name__}: {exc}"
        if _attempt < _MAX_ATTEMPTS:
            time.sleep(_RETRY_BACKOFF[_attempt - 1])

    return None, (
        f"图像接口连试 {_MAX_ATTEMPTS} 次仍失败（chinzy 上游不稳定，常见"
        f"瞬时断连）：{last_transient}。稍后再试，或把描述写简单点 —— "
        "复杂出图更慢、上游更容易把连接掐掉。"
    )


def _save_image(png: bytes) -> Path:
    """Save PNG into workspace, return the path. Raises on IO failure."""
    ws = _workspace_dir()
    fname = f"genimg_{time.strftime('%Y%m%dT%H%M%S', time.gmtime())}.png"
    out = ws / fname
    out.write_bytes(png)
    return out


def _validate_prompt(args: dict[str, Any]) -> str | None:
    prompt = args.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        return None
    return prompt


def _handle_generate_image_sync(args: dict[str, Any], task_id: str = "") -> str:
    """Legacy synchronous blocking path (config [image].async_enabled=
    false). Kept verbatim-behavior for Strangler-Fig rollback."""
    prompt = _validate_prompt(args)
    if prompt is None:
        return _err(
            "prompt required",
            "generate_image 需要 prompt 字段（图片的文字描述）。"
            '示例：{"prompt": "一只戴墨镜的橘猫，扁平插画风"}',
        )
    size = str(args.get("size") or _DEFAULT_SIZE)
    model = _image_model()
    png, err = _generate_png(prompt, size, model)
    if png is None:
        return _err("image gen failed", err or "未知错误")
    try:
        out = _save_image(png)
    except Exception as exc:  # noqa: BLE001
        return _err("save failed", f"写入 workspace 失败：{exc}")
    opened = _open_file(out)
    # WI-T1.2 D1：显式 emit artifacts[]（kind=image 用于前端区分文件 vs 图像）
    # 注：audit follow-up — _open_file 已自动打开图片；当
    # tools.last_mile.frontend_artifact_card=true 时建议禁用 _open_file 避免双重打开
    # （留作 follow-up），本期保 BC 不动 opened 行为。
    out_path = str(out)
    ext = Path(out_path).suffix.lower()
    mime = "image/png" if ext == ".png" else "image/jpeg" if ext in (".jpg", ".jpeg") else "image/*"
    return json.dumps(
        {
            "ok": True,
            "path": out_path,
            "opened": opened,
            "prompt": prompt,
            "model": model,
            "artifacts": [{
                "kind": "image",
                "path": out_path,
                "mime": mime,
                "title": Path(out_path).name,
            }],
        },
        ensure_ascii=False,
    )


def _async_enabled() -> bool:
    try:
        import config as _cfg  # type: ignore[import-not-found]

        return bool(
            (_cfg.config.raw.get("image") or {}).get("async_enabled", True)
        )
    except Exception:  # noqa: BLE001
        return True


def _handle_generate_image(args: dict[str, Any], task_id: str = "") -> str:
    """Dispatch: async (default) → submit to ImageGenerationWorker and
    return immediately so the agent turn doesn't block 60–240s; the
    worker pushes the result back to the pet when done. sync (rollback)
    → legacy blocking path.
    """
    if not _async_enabled():
        return _handle_generate_image_sync(args, task_id)

    prompt = _validate_prompt(args)
    if prompt is None:
        return _err(
            "prompt required",
            "generate_image 需要 prompt 字段（图片的文字描述）。"
            '示例：{"prompt": "一只戴墨镜的橘猫，扁平插画风"}',
        )
    size = str(args.get("size") or _DEFAULT_SIZE)
    model = _image_model()
    # session_id + worker 由 chat handler 经 set_session_context 注入
    # （同 _write_scope_root 机制，registry 把它们 merge 进 args）。
    # 这样工具不用 import main 的 ServiceContext 单例（会循环/重量）。
    session_id = str(args.get("_session_id") or "default")
    worker = args.get("_image_worker")
    if worker is None:
        # worker 不可用（未注册/构造失败）→ 不静默失败，回退同步，
        # 用户至少能拿到图（慢但可用）。
        return _handle_generate_image_sync(args, task_id)

    status, job_id = worker.submit(
        session_id=session_id, prompt=prompt, size=size, model=model
    )
    if status == "already_generating":
        return json.dumps(
            {
                "ok": True,
                "status": "already_generating",
                "message": "🎨 同样的图我已经在画了，稍等一下下就好~",
            },
            ensure_ascii=False,
        )
    if status != "queued":
        # 关键修复：submit 返回 "unavailable"（worker 未存活/入队失败）
        # 时**不能**再谎报 "generating" success —— 那会让 agent 说
        # “在画了”，但 job 从未入队、永不出图（静默失败，用户实测到的
        # bug）。最佳实践 = 优雅降级：回退同步路径，慢但用户必拿到图；
        # 同时落日志以便定位 worker 不可用的根因。
        import logging as _logging

        _logging.getLogger(__name__).warning(
            "generate_image: worker.submit status=%r (not queued) "
            "→ falling back to sync generation",
            status,
        )
        return _handle_generate_image_sync(args, task_id)
    return json.dumps(
        {
            "ok": True,
            "status": "generating",
            "job_id": job_id,
            "message": (
                "🎨 在画了，稍等~ 画好我会自动打开给你看，"
                "这会儿你想聊点别的也行。"
            ),
        },
        ensure_ascii=False,
    )


registry.register(
    "generate_image",
    "image",
    _SCHEMA,
    _handle_generate_image,
    permission_category="network",
    timeout_seconds=_TOOL_TIMEOUT_S,
)
