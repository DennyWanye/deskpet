## Why

(2026-05-17) A real incident corrupted a user project: the code-mode agent, running on the relay-served **deepseek-v4-pro**, emitted its chain-of-thought and a tool call as *inline text inside the `content` stream* — `<｜begin▁of▁thinking｜>…<｜end▁of▁thinking｜>` and the native `<｜｜DSML｜｜tool_calls>` protocol — instead of the structured `reasoning_content` / OpenAI `tool_calls` fields. `backend/providers/openai_compatible.py` only sanitizes the *structured* `reasoning_content` field, so this inline markup passed straight through `content` into the agent loop and was written verbatim as the `write_file` argument, overwriting `test-research-helper/backend/app/services/llm_service.py` with a half CoT sentence + a raw DSML `todo_write` block → `SyntaxError`, backend dead. Any file the agent writes on this model can be corrupted this way; it must be fixed at the single provider chokepoint.

## What Changes

- Add a **pure content sanitizer** that removes inline thinking-delimiter blocks from provider `content`: deepseek `<｜begin▁of▁thinking｜>…<｜end▁of▁thinking｜>` (including an unterminated leading-`<｜…thinking｜>` with no close) plus defensive `<think>…</think>`.
- Add a **DSML textual tool-call extractor**: when `content` contains an inline `<｜｜DSML｜｜tool_calls>` block (`invoke name="…"`, `parameter name="…"` … payload), parse it into structured `tool_calls` (name + JSON arguments) so the tool call is **not lost**, and strip the DSML markup out of the returned `content`.
- Wire both into `openai_compatible.py` at **both** response paths — non-streaming return (~`:562`) and streaming accumulated `full_content` + final dict (~`:877/:965/:980/:1300`) — so every consumer (agent_loop, history persistence, write_file/edit_file args, pet display) receives clean `content` and structured tool calls.
- Strangler-Fig config flag (`[llm] sanitize_inline_cot_dsml`, default **on**) so the behavior is instantly reversible for the demo.
- Existing structured `reasoning_content` / `tool_calls` handling and the `reasoning_content`-400 multi-turn round-trip logic are **unchanged** (sanitizer only touches `content`; structured fields untouched).

## Capabilities

### New Capabilities
- `llm-response-sanitization`: Provider-layer guarantee that `content` returned to downstream consumers is free of inline reasoning/tool-call markup, and that inline-protocol tool calls are normalized into structured `tool_calls`.

### Modified Capabilities
<!-- None: structured reasoning_content/tool_calls behavior is implementation detail and is intentionally left unchanged at the spec level. -->

## Impact

- **Code**: `backend/providers/openai_compatible.py` (non-stream return + stream accumulation/final dict); new helper module (e.g. `backend/providers/_response_sanitizer.py`); `config.toml` `[llm]` flag.
- **Behavior**: `content` for inline-CoT/DSML responses changes from polluted → clean; previously-lost inline DSML tool calls now execute. Structured-field responses unaffected.
- **Tests**: new `backend/tests/test_response_sanitizer.py` (sanitizer + DSML extractor, fixtures from the real incident) + a provider-level regression that a deepseek inline-DSML response yields `content` with no `<｜` markup and a structured tool call.
- **Risk**: core hot path (every LLM response flows through it) — mitigated by pure-function design, exhaustive unit tests, and the default-on/reversible flag.
- **Constraints**: backend interpreter `/path/to/deskpet\backend\.venv\Scripts\python.exe`; no sandbox/permission walls; rollback must keep working; `--no-archive` (stays active until demo sign-off).
