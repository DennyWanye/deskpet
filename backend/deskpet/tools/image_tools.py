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
_TIMEOUT_S = 120.0

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


def _handle_generate_image(args: dict[str, Any], task_id: str = "") -> str:
    prompt = args.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        return _err(
            "prompt required",
            "generate_image 需要 prompt 字段（图片的文字描述）。"
            "示例：{\"prompt\": \"一只戴墨镜的橘猫，扁平插画风\"}",
        )
    size = str(args.get("size") or _DEFAULT_SIZE)
    model = _image_model()

    base_url, api_key = _resolve_endpoint()
    if not base_url:
        return _err(
            "no image endpoint",
            "没解析到图像生成 endpoint（llm_runtime.json / config 都没有 "
            "base_url）。请先在设置里配好 LLM endpoint。",
        )

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {"model": model, "prompt": prompt, "size": size, "n": 1}

    try:
        with httpx.Client(timeout=_TIMEOUT_S) as cli:
            resp = cli.post(
                f"{base_url}/images/generations",
                json=payload,
                headers=headers,
            )
        if resp.status_code != 200:
            detail = ""
            try:
                detail = json.dumps(resp.json(), ensure_ascii=False)[:300]
            except Exception:  # noqa: BLE001
                detail = ""
            return _err(
                f"image API HTTP {resp.status_code}",
                "图像生成接口返回非 200。可能是 chinzy 不支持该 model、"
                f"额度或参数问题。响应：{detail}",
                status=resp.status_code,
            )
        data = (resp.json() or {}).get("data") or []
        if not data:
            return _err(
                "empty image response",
                "接口 200 但 data 为空，无法取回图片。",
            )
        item = data[0]
        png: bytes
        if item.get("b64_json"):
            png = base64.b64decode(item["b64_json"])
        elif item.get("url"):
            with httpx.Client(timeout=_TIMEOUT_S) as cli:
                dl = cli.get(item["url"])
            if dl.status_code != 200:
                return _err(
                    "image download failed",
                    f"取回图片 URL 失败 HTTP {dl.status_code}。",
                )
            png = dl.content
        else:
            return _err(
                "no image payload",
                "响应里既没有 b64_json 也没有 url，无法保存图片。",
            )
    except httpx.TimeoutException:
        return _err(
            "image API timeout",
            f"图像生成超时（>{int(_TIMEOUT_S)}s）。稍后再试或换更简单的描述。",
        )
    except httpx.HTTPError as exc:
        return _err("image API error", f"网络/接口错误：{exc}")
    except Exception as exc:  # noqa: BLE001 — tool must never raise
        return _err("image gen failed", f"未预期错误：{type(exc).__name__}: {exc}")

    ws = _workspace_dir()
    fname = f"genimg_{time.strftime('%Y%m%dT%H%M%S', time.gmtime())}.png"
    out = ws / fname
    try:
        out.write_bytes(png)
    except Exception as exc:  # noqa: BLE001
        return _err("save failed", f"写入 workspace 失败：{exc}")

    opened = _open_file(out)
    return json.dumps(
        {
            "ok": True,
            "path": str(out),
            "opened": opened,
            "prompt": prompt,
            "model": model,
        },
        ensure_ascii=False,
    )


registry.register(
    "generate_image",
    "image",
    _SCHEMA,
    _handle_generate_image,
    permission_category="network",
    timeout_seconds=_TIMEOUT_S,
)
