# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""generate_image tool — text→image via the existing the relay endpoint.

User says 生成图片 → agent calls this tool → POST
{base_url}/images/generations with the configured image model (default
``doubao-seedream-4.0`` — relay 下线 gpt-image-2 后于 2026-06-24 切换，
真链路实测可用；reusing the exact base_url+api_key the working chat path
uses) → PNG saved into the workspace folder → opened with the OS default
viewer.

Flat auto-discovered module (same as web_tools.py / file_tools.py):
``registry.register`` at module scope, zero main.py change.

Name MUST stay ``generate_image`` — it equals capability_gate.py image
``tool_markers[0]``, so registering it flips image requests
REFUSE→PASS (consistent with companion-context-isolation §D2:
"新增图像工具自动放行"). Plan: plans/2026-05-16-generate-image-tool.md
"""
from __future__ import annotations

import base64
from contextvars import ContextVar
import json
import logging
import os
import ssl
import sys
import time
from pathlib import Path
from typing import Any

import httpx

from .registry import registry

log = logging.getLogger(__name__)

# 2026-06-24：relay 下线 gpt-image-2。真链路实测 doubao-seedream-4.0 可用
# （返回 b64，接受 1024x1024 / 1536x1024 / 1792x1024 / 1344x768，落在 300s
# 读超时预算内）。同库的 seedream-4.5 / 5.0-lite 也活着但拒绝 1024x1024、
# 强制 ≥2K，会打断现有 1024 侧栏出图路径，故不选作默认。
_DEFAULT_MODEL = "doubao-seedream-4.0"
_DEFAULT_SIZE = "1024x1024"
_DEFAULT_QUALITY = "medium"
# 2026-06-11 超时根因修正（中转站侧 Caddy 访问日志 + RequestLog 交叉证据
# + 本机真链路实测）。「60 秒必挂」其实是两个独立的 60s 杀手叠加：
# 1) httpx 超时太短：服务端最近 30 次调用 28 次成功 200，耗时 57~221s
#    （典型 70~120s），而客户端 60~70s 就掐断 → 报超时、服务端 1~2 分钟
#    后照样把图生成完并按次计费（$0.15/次，钱花了图没人收）。修法：read
#    超时拉到 300s，与服务端路由超时 timeoutMs=300000 对齐。
# 2) 本机代理掐空闲连接：httpx 默认 trust_env=True 会跟随 HTTP(S)_PROXY
#    env（如 Clash 127.0.0.1:7897），而出图期间连接上 60~220s 没有任何
#    字节流动，代理把"空闲"连接 ~60s 就掐断（实测 RemoteProtocolError
#    "Server disconnected" 精确卡在 ~64s；Caddy 侧对应记到 status 0 的
#    "客户端 59.8s 断开"）。中转站是国内站、直连可达，修法：默认
#    trust_env=False 直连（config [image].trust_env_proxy=true 可改回）。
# 之前 2026-06-09 误判为「relay 挂起不返回」改成 70s 快速失败 —— 实际是
# 上面两个 60s 杀手。修复后真链路实测：200 OK / 79.9s / 1.3MB 出图
# （plans/2026-06-11-image-timeout-fix-live.png）。同步阻塞的体验问题由
# ImageGenerationWorker 异步路径兜底（默认开启，agent turn 秒回不阻塞）。
_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=30.0)
# 重试只接「快速瞬时失败」：连接超时/断连/SSL/502/503（都没打到生成、
# 无扣费风险）。读超时（等满 300s）与 504（上游耗尽 300s 预算）不重试 ——
# 客户端断开后服务端仍会继续生成并扣费，盲目重试 = 双倍烧钱。
_MAX_ATTEMPTS = 2           # 总尝试次数（仅瞬时错误重试 1 次）
_RETRY_BACKOFF = (5.0,)     # attempt 2 前退避
# 注册到 ToolRegistry 的总超时：必须 > 最坏重试预算
# （attempt1 瞬时失败 ≤~40s + 5s 退避 + attempt2 读满 300s + 收尾），
# 否则 registry 会在请求跑完前杀掉 handler。
_TOOL_TIMEOUT_S = 360.0
_ImageErrorContext = tuple[int | None, dict[str, Any] | str | None, Exception | None]
_LAST_IMAGE_ERROR_CONTEXT: ContextVar[_ImageErrorContext] = ContextVar(
    "_LAST_IMAGE_ERROR_CONTEXT", default=(None, None, None)
)

_SCHEMA: dict[str, Any] = {
    "name": "generate_image",
    "description": (
        "AI 文生图/原创生成图片工具。用户要生成、画、做海报、插画、图标、头像或保存一张新图片时必须用本工具；"
        "本工具会真实调用图像生成接口并把图片保存到本地 workspace 后返回路径。"
        "不要用 web_fetch、网页搜索或抓取网页去找图来替代图片生成。"
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
    user actually configured for the relay) → config.toml ``[llm]`` +
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
            from config import standalone_config_section  # type: ignore[import-not-found]

            base_url = str(
                (standalone_config_section("llm") or {}).get("base_url", "")
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


def _resolve_relay_base_and_key() -> tuple[str | None, str | None]:
    """复用现有 image endpoint/key 解析来源，避免 probe 与生图路径漂移。"""
    base_url, api_key = _resolve_endpoint()
    return base_url or None, api_key


def _models_url_for_base(base_url: str) -> str:
    base = base_url.rstrip("/")
    return f"{base}/models" if base.endswith("/v1") else f"{base}/v1/models"


def probe_image_reachable(*, timeout_s: float = 8.0) -> bool:
    """便宜探测 relay images 服务可达性；只判服务层，不证明模型可用。"""
    base_url, api_key = _resolve_relay_base_and_key()
    if not base_url:
        log.info("image probe unreachable: missing image relay base_url")
        return False

    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        timeout = httpx.Timeout(
            connect=5.0,
            read=timeout_s,
            write=5.0,
            pool=5.0,
        )
        with httpx.Client(timeout=timeout, trust_env=_trust_env_proxy()) as cli:
            resp = cli.get(_models_url_for_base(base_url), headers=headers)
        return resp.status_code < 500
    except Exception as exc:  # noqa: BLE001 - probe 从不向上抛
        log.info("image probe unreachable: %s", exc)
        return False


def _image_model() -> str:
    try:
        from config import standalone_config_section  # type: ignore[import-not-found]

        return str(
            (standalone_config_section("image") or {}).get("model")
            or _DEFAULT_MODEL
        )
    except Exception:  # noqa: BLE001
        return _DEFAULT_MODEL


def _trust_env_proxy() -> bool:
    """config ``[image].trust_env_proxy`` → httpx trust_env（默认 False）。

    默认绕过系统代理直连中转站：本机代理（Clash 等）会把出图这种
    60~220s 零字节流动的「空闲」连接 ~60s 掐断（见模块顶部根因注释）。
    若用户的网络环境必须走代理才能到达 endpoint，可在 config 设
    ``[image] trust_env_proxy = true`` 改回跟随 HTTP(S)_PROXY env。
    """
    try:
        from config import standalone_config_section  # type: ignore[import-not-found]

        return bool(
            (standalone_config_section("image") or {}).get("trust_env_proxy", False)
        )
    except Exception:  # noqa: BLE001
        return False


def _image_quality() -> str:
    """config ``[image].quality`` → payload quality（默认 medium）。

    中转层全字段透传（images.service.ts 仅替换 model 字段），quality 直达
    上游；计费按张数与质量无关。桌宠小窗展示场景用不到 HD —— 降 quality
    能把现网 57~221s 的生成耗时显著压低，是最便宜的提速手段。
    """
    try:
        from config import standalone_config_section  # type: ignore[import-not-found]

        return str(
            (standalone_config_section("image") or {}).get("quality")
            or _DEFAULT_QUALITY
        )
    except Exception:  # noqa: BLE001
        return _DEFAULT_QUALITY


def _set_image_error_context(
    status_code: int | None,
    body: dict[str, Any] | str | None,
    exc: Exception | None,
) -> None:
    _LAST_IMAGE_ERROR_CONTEXT.set((status_code, body, exc))


def _get_image_error_context() -> _ImageErrorContext:
    return _LAST_IMAGE_ERROR_CONTEXT.get()


def _collect_error_texts(body: dict[str, Any] | str | None) -> list[str]:
    if body is None:
        return []
    if isinstance(body, str):
        try:
            parsed = json.loads(body)
        except Exception:  # noqa: BLE001
            return [body]
        if isinstance(parsed, dict):
            return _collect_error_texts(parsed)
        return [body]
    texts: list[str] = []

    def _walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"code", "type", "message", "error"} and isinstance(
                    item, (str, int, float)
                ):
                    texts.append(str(item))
                else:
                    _walk(item)
        elif isinstance(value, list):
            for item in value:
                _walk(item)
        elif isinstance(value, (str, int, float)):
            texts.append(str(value))

    _walk(body)
    return texts


def _classify_image_error(
    status_code: int | None,
    body: dict[str, Any] | str | None,
    exc: Exception | None,
) -> str:
    """给 PPT Pro 回退 gate 使用的粗粒度失败分类。"""
    if exc is not None and isinstance(
        exc,
        (
            httpx.TimeoutException,
            httpx.RemoteProtocolError,
            httpx.ConnectError,
            httpx.ReadError,
            httpx.WriteError,
            httpx.ProtocolError,
            ssl.SSLError,
        ),
    ):
        return "connectivity"
    if status_code in (502, 503, 504):
        return "connectivity"
    if status_code in (401, 403):
        return "auth"
    if status_code == 429:
        return "quota"

    texts = [text.lower() for text in _collect_error_texts(body)]
    joined = " ".join(texts)
    if (
        "model_not_found" in joined
        or "invalid_model" in joined
        or ("model" in joined and "unsupport" in joined)
    ):
        return "model_unavailable"
    if "content_policy" in joined or "safety" in joined:
        return "content"

    # relay 有时只返回自然语言 message；这层依赖文案，可能漏判。
    # 上层还会用「全图失败也回退」兜底，避免整套 PPT 变成占位图。
    if "model" in joined and any(
        marker in joined
        for marker in ("not found", "unsupport", "不支持", "不存在", "无可用")
    ):
        return "model_unavailable"

    if status_code is not None and 400 <= status_code < 500:
        return "content"
    return "unknown"


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
    """Blocking: the relay POST + transient-retry → (png_bytes, None) on
    success, or (None, error_hint) on failure. No file IO. Reused by
    BOTH the legacy sync handler and the async ImageGenerationWorker —
    single copy of the slow logic. Never raises.
    """
    _set_image_error_context(None, None, None)
    base_url, api_key = _resolve_relay_base_and_key()
    if not base_url:
        _set_image_error_context(None, None, None)
        return None, (
            "没解析到图像生成 endpoint（llm_runtime.json / config 都没有 "
            "base_url）。请先在设置里配好 LLM endpoint。"
        )

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    else:
        # 2026-06-11 中转站日志发现过一次不带 Authorization 头的裸 401
        # （疑似某次配置丢了 key）。本地无鉴权 endpoint 是合法场景，所以
        # 不硬失败，但落警告日志便于下次定位 key 解析链路哪环断了。
        import logging as _logging

        _logging.getLogger(__name__).warning(
            "generate_image: api_key 未解析到，发送不带 Authorization 的"
            "请求（base_url=%s）— 若对端是中转站会 401",
            base_url,
        )
    # 2026-05-16 中专站确认契约：OpenAI 标准 Images API，显式请求
    # b64_json（规格明确要求；服务端把 n 钳到 [1,10]）。
    payload = {
        "model": model,
        "prompt": prompt,
        "size": size,
        "n": 1,
        "quality": _image_quality(),
        "response_format": "b64_json",
    }

    # 中转站上游对慢/复杂出图偶发瞬时断连（RemoteProtocolError
    # "Server disconnected"）。瞬时错误（连接断/SSL/502/503）重试带退避；
    # 4xx 确定性错误、读超时、504 立即返回不重试（后两者重试 = 重复扣费）。
    last_transient = ""
    for _attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            with httpx.Client(
                timeout=_TIMEOUT, trust_env=_trust_env_proxy()
            ) as cli:
                resp = cli.post(
                    f"{base_url}/images/generations",
                    json=payload,
                    headers=headers,
                )
            if resp.status_code in (502, 503):
                _set_image_error_context(resp.status_code, None, None)
                last_transient = f"HTTP {resp.status_code}（上游网关瞬时）"
            elif resp.status_code == 504:
                _set_image_error_context(resp.status_code, None, None)
                return None, (  # 上游已耗尽服务端 300s 预算：重试只会再烧一轮
                    "上游等满 300 秒仍没出图（网关 504，上游偶发抽风）。"
                    "为避免重复扣费已停止重试。稍后再试，或把描述写简单点。"
                )
            elif resp.status_code != 200:
                detail = ""
                body: dict[str, Any] | str | None = None
                try:
                    body = resp.json()
                    detail = json.dumps(body, ensure_ascii=False)[:300]
                except Exception:  # noqa: BLE001
                    detail = ""
                    body = None
                _set_image_error_context(resp.status_code, body, None)
                return None, (  # 4xx 确定性错误：不重试
                    f"图像接口 HTTP {resp.status_code}：可能是 model 不支持、"
                    f"额度或参数问题。响应：{detail}"
                )
            else:
                data = (resp.json() or {}).get("data") or []
                if not data:
                    _set_image_error_context(resp.status_code, {"data": data}, None)
                    return None, "接口 200 但 data 为空，无法取回图片。"
                item = data[0]
                if item.get("b64_json"):
                    return base64.b64decode(item["b64_json"]), None
                if item.get("url"):
                    with httpx.Client(
                        timeout=_TIMEOUT, trust_env=_trust_env_proxy()
                    ) as cli:
                        dl = cli.get(item["url"])
                    if dl.status_code != 200:
                        _set_image_error_context(dl.status_code, None, None)
                        return None, f"取回图片 URL 失败 HTTP {dl.status_code}。"
                    return dl.content, None
                _set_image_error_context(resp.status_code, item, None)
                return None, "响应里既没有 b64_json 也没有 url，无法保存图片。"
        except httpx.ConnectTimeout as exc:
            # 连接没建立就超时（10s）：请求没打到生成、无扣费风险 → 可重试。
            # 必须排在 TimeoutException 之前（它是其子类）。
            _set_image_error_context(None, None, exc)
            last_transient = f"{type(exc).__name__}: {exc}"
        except httpx.TimeoutException as exc:
            _set_image_error_context(None, None, exc)
            return None, (  # 读超时不重试：服务端仍会生成完并按次计费
                f"图像接口等了 {int(_TIMEOUT.read)} 秒仍未返回"
                f"（{type(exc).__name__}）。服务端可能仍在出图并照常计费，"
                "为避免重复扣费已停止等待且不重试。稍后再试，"
                "或把描述写简单点 —— 复杂出图更慢。"
            )
        except (
            httpx.RemoteProtocolError,
            httpx.ConnectError,
            httpx.ReadError,
            httpx.WriteError,
            httpx.ProtocolError,
            ssl.SSLError,
        ) as exc:
            _set_image_error_context(None, None, exc)
            last_transient = f"{type(exc).__name__}: {exc}"
        except httpx.HTTPError as exc:
            _set_image_error_context(None, None, exc)
            last_transient = f"{type(exc).__name__}: {exc}"
        except Exception as exc:  # noqa: BLE001 — never raise
            _set_image_error_context(None, None, exc)
            return None, f"未预期错误：{type(exc).__name__}: {exc}"
        if _attempt < _MAX_ATTEMPTS:
            time.sleep(_RETRY_BACKOFF[_attempt - 1])

    return None, (
        f"图像接口连试 {_MAX_ATTEMPTS} 次仍失败（中转站上游不稳定，常见"
        f"瞬时断连）：{last_transient}。稍后再试，或把描述写简单点 —— "
        "复杂出图更慢、上游更容易把连接掐掉。"
    )


def _save_image(png: bytes) -> Path:
    """Save PNG into workspace, return the path. Raises on IO failure."""
    ws = _workspace_dir()
    stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
    fname = f"genimg_{stamp}_{time.time_ns()}.png"
    out = ws / fname
    out.write_bytes(png)
    return out


def generate_images(prompts, *, size=_DEFAULT_SIZE, model=None):
    """同步批量文生图，给 PPT 整页生图用（async worker 不返回路径，这里要确定性同步路径）。
    逐 prompt 调 _generate_png + _save_image。返回 list，每项:
    {"prompt": str, "path": str|None, "error": str|None}。
    任一 prompt 失败不影响其它（该项 path=None,error=原因）。从不抛异常。
    """
    def _safe_prompt_text(value) -> str:
        if isinstance(value, str):
            return value
        try:
            return str(value)
        except Exception:  # noqa: BLE001
            return ""

    try:
        resolved_model = model or _image_model()
    except Exception:  # noqa: BLE001
        resolved_model = _DEFAULT_MODEL
    results = []
    if not isinstance(prompts, list):
        return [
            {
                "prompt": _safe_prompt_text(prompts),
                "path": None,
                "error": "empty prompt",
                "error_kind": "content",
            }
        ]
    for prompt in prompts:
        if not isinstance(prompt, str) or not prompt.strip():
            results.append(
                {
                    "prompt": _safe_prompt_text(prompt),
                    "path": None,
                    "error": "empty prompt",
                    "error_kind": "content",
                }
            )
            continue
        try:
            _set_image_error_context(None, None, None)
            png, err = _generate_png(prompt, size, resolved_model)
            if png is None:
                status_code, body, exc = _get_image_error_context()
                results.append(
                    {
                        "prompt": prompt,
                        "path": None,
                        "error": err or "未知错误",
                        "error_kind": _classify_image_error(status_code, body, exc),
                    }
                )
                continue
            try:
                out = _save_image(png)
            except Exception as exc:  # noqa: BLE001
                results.append(
                    {
                        "prompt": prompt,
                        "path": None,
                        "error": f"写入 workspace 失败：{exc}",
                        "error_kind": "unknown",
                    }
                )
                continue
            results.append({"prompt": prompt, "path": str(out), "error": None})
        except Exception as exc:  # noqa: BLE001
            results.append(
                {
                    "prompt": prompt,
                    "path": None,
                    "error": f"未预期错误：{type(exc).__name__}: {exc}",
                    "error_kind": _classify_image_error(None, None, exc),
                }
            )
    return results


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
        status_code, body, exc = _get_image_error_context()
        return _err(
            "image gen failed",
            err or "未知错误",
            error_kind=_classify_image_error(status_code, body, exc),
        )
    try:
        out = _save_image(png)
    except Exception as exc:  # noqa: BLE001
        return _err("save failed", f"写入 workspace 失败：{exc}", error_kind="unknown")
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
        from config import standalone_config_section  # type: ignore[import-not-found]

        return bool(
            (standalone_config_section("image") or {}).get("async_enabled", True)
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
