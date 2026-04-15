"""P2-1-S3 Task 5: isolated handler for the control-WS
``provider_test_connection`` message.

Lives in a dedicated module (not ``main.py``) so:
  1. The SettingsPanel 测试连接 button has a tight unit test (see
     tests/test_provider_test_connection.py) that doesn't need the full
     app bootstrap (faster_whisper, CUDA, etc).
  2. The handler itself is a pure ``async`` function — the WS receive
     loop in ``main.control_channel`` just calls it. That keeps main.py's
     message-type dispatch skinny.

The apiKey passed through this handler is the *candidate* the user just
typed into the SettingsPanel form — it's not persisted on success. The
UI is expected to invoke ``set_cloud_api_key`` (Tauri command) only
after the test came back green.
"""
from __future__ import annotations

from typing import Any

import structlog

from providers.openai_compatible import OpenAICompatibleProvider

logger = structlog.get_logger()


async def handle_provider_test_connection(ws: Any, payload: dict) -> None:
    """Ping a candidate cloud endpoint; reply with ``provider_test_connection_result``.

    ``ws`` needs ``send_json`` — either ``fastapi.WebSocket`` or the test's
    fake. We don't return anything; the caller doesn't need a value, it
    just awaits so the reply is on the wire before the next ``receive``.
    """
    payload = payload or {}
    base_url = (payload.get("base_url") or "").strip()
    api_key = (payload.get("api_key") or "").strip()
    model = (payload.get("model") or "").strip()

    if not (base_url and api_key and model):
        await ws.send_json({
            "type": "provider_test_connection_result",
            "payload": {
                "ok": False,
                "error": "base_url / api_key / model all required",
            },
        })
        return

    tested_url = f"{base_url.rstrip('/')}/models"
    try:
        candidate = OpenAICompatibleProvider(
            base_url=base_url,
            api_key=api_key,
            model=model,
        )
        ok = await candidate.health_check()
        await ws.send_json({
            "type": "provider_test_connection_result",
            "payload": {
                "ok": bool(ok),
                "tested_url": tested_url,
            },
        })
    except Exception as exc:
        # provider init or the GET /models both land here. Log + surface
        # the exception string so the UI can show something actionable
        # without exposing a stacktrace.
        logger.warning(
            "provider_test_failed", base_url=base_url, model=model, error=str(exc)
        )
        await ws.send_json({
            "type": "provider_test_connection_result",
            "payload": {"ok": False, "error": str(exc), "tested_url": tested_url},
        })
