"""Multi-provider LLM adapters (P4-S6, tasks 10.x).

Unified surface via ``base.py::LLMClient`` — every adapter returns a
``ChatResponse(content, tool_calls, stop_reason, usage)`` and a
streaming-capable ``ChatChunk(delta_content, delta_tool_calls, is_final)``.

Adapters:

- ``anthropic_adapter.py``   — ``anthropic>=0.40``. Prompt caching via
  ``cache_control={"type": "ephemeral"}`` placed at the tail of
  ``frozen_system`` for cache-friendly prefix reuse.
- ``openai_adapter.py``      — ``openai>=1.40``. Tool calls normalised
  into the unified ``ToolCall`` dataclass.
- ``gemini_adapter.py``      — Google's new ``google-genai>=1.0`` SDK
  (NOT the legacy ``google-generativeai``). Tool calls normalised likewise.

Infrastructure:

- ``registry.py``            — ``list_providers()`` auto-hides adapters
  whose required API key env var is missing.
- Fallback chain             — ``[llm.fallback_chain]`` in config.toml,
  max 2 retries across providers.
- Budget cap                 — daily USD cap tracked against
  ``[llm.providers].daily_usd_cap``; 80% warning IPC, 100% block.
- 429 handling               — honour ``Retry-After`` header, exponential
  backoff up to 3 tries.
"""
