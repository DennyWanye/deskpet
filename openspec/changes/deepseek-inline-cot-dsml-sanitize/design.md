## Context

`backend/providers/openai_compatible.py` is the single chokepoint for every LLM response (streaming `chat_stream_with_tools` + non-streaming `chat`). It already handles the *structured* thinking-mode contract: `reasoning_content`/`reasoning` JSON fields and OpenAI `tool_calls` arrays, plus a `reasoning_content`-400 multi-turn round-trip workaround.

The chinzy-served **deepseek-v4-pro** sometimes does NOT use those structured fields. Instead it emits, inline in the `content` token stream:
- chain-of-thought wrapped in `<｜begin▁of▁thinking｜>…<｜end▁of▁thinking｜>` (full-width `｜` U+FF5C, `▁` U+2581), and
- tool calls in its native textual protocol: `<｜｜DSML｜｜tool_calls>` / `<｜｜DSML｜｜invoke name="…">` / `<｜｜DSML｜｜parameter name="…" …>` followed by the argument payload.

Current code (`:562` non-stream `content = msg.get("content") or ""`; `:980` stream `full_content += txt`; `:965` end-of-stream `full_content = msg["content"]`) returns this raw. The agent loop then uses the returned `content` as the final assistant message and, when the model expresses a `write_file`/`edit_file` via the inline protocol, that polluted text becomes the file body. Real incident: `test-research-helper/.../llm_service.py` overwritten with `if "<｜end▁of▁thinking｜>API 测试通过…` + a raw `<｜｜DSML｜｜tool_calls>` `todo_write` block → `SyntaxError`, backend dead. `reasoning_chars=0` in logs confirms the provider never saw structured reasoning — it was all in `content`.

## Goals / Non-Goals

**Goals:**
- Guarantee `content` returned to all consumers is free of inline CoT/DSML markup.
- Recover inline DSML tool calls into structured `tool_calls` so the agent's intended action is not lost.
- One pure, exhaustively-tested helper; wired at the provider chokepoint for both response paths.
- Default-on, instantly reversible via a Strangler-Fig flag.
- Zero regression to structured `reasoning_content`/`tool_calls` and the `reasoning_content`-400 round-trip.

**Non-Goals:**
- Changing pet/UI display filtering (`petText.ts`/`forPet`) — orthogonal; this fixes the data path, not just display.
- Generalizing to every vendor's bespoke inline protocol — scope is the deepseek delimiters + DSML block actually observed.
- Streaming-delta rewriting for the UI mid-stream — only the *accumulated* `full_content` and the *final returned* `content`/`tool_calls` must be clean (UI already filters; correctness lives in the final value the agent loop consumes).

## Decisions

**D1 — New pure module `backend/providers/_response_sanitizer.py`.** Two pure functions: `strip_inline_reasoning(text) -> str` and `extract_dsml_tool_calls(text) -> (clean_text, list[tool_call])`, plus a `sanitize_response(content, tool_calls, *, enabled) -> (content, tool_calls, extracted_any)` orchestrator. Pure functions = trivially unit-testable red→green with fixtures captured verbatim from the incident; no provider/network needed. Alternative (inline regex in `openai_compatible.py`) rejected: untestable in isolation, pollutes a 1400-line hot file.

**D2 — Regex strategy for reasoning strip.** Remove well-formed `<｜begin▁of▁thinking｜>.*?<｜end▁of▁thinking｜>` (DOTALL, non-greedy). Then handle the incident's *orphan* shape: a leading/!matched `<｜end▁of▁thinking｜>` or `<｜begin▁of▁thinking｜>` with no partner — drop from the orphan delimiter up to the first DSML marker or, if none, the delimiter token itself, never silently eating a real answer. Also strip `<think>.*?</think>` defensively. Final guard: assert no residual `<｜` thinking-delimiter token remains; if one does, drop the token (never the surrounding answer).

**D3 — DSML extraction.** Match `<｜｜DSML｜｜tool_calls>` … then iterate `<｜｜DSML｜｜invoke name="NAME">` blocks, each with zero+ `<｜｜DSML｜｜parameter name="P" …>PAYLOAD` until the next marker / end. Build `{"name": NAME, "arguments": {P: json.loads(PAYLOAD) or raw}}`. Wrap each payload parse in try/except → on failure, keep extraction going, log `dsml_param_parse_failed` WARNING, still strip the block (never leak markup). Remove the entire matched DSML span from `content`.

**D4 — Merge semantics.** If structured `tool_calls` already present → trust them, do NOT also parse DSML (avoid double-execute); only strip any stray reasoning. If none structured AND DSML extracted → set `tool_calls` = extracted, `stop_reason = "tool_use"`. This mirrors existing `finish=="tool_calls"` semantics at `:531`/`:891`.

**D5 — Wiring points.** Non-stream: post-process just before the `return {...}` at `:561`. Stream: sanitize the *accumulated* `full_content` + buffered tool calls at the single final-dict assembly (~`:1300`), and also the non-stream-body-via-stream branch (`:877`/`:905`). Do NOT mutate per-delta `yield`s (UI cosmetic only; correctness is the final value). One helper call per path.

**D6 — Strangler-Fig flag.** `[llm] sanitize_inline_cot_dsml` (bool, default `true`). Read via existing config plumbing; pass `enabled` into `sanitize_response`. `false` → identity passthrough = byte-for-byte legacy behavior (rollback for demo).

## Risks / Trade-offs

- **Over-stripping a legitimate answer that mentions the delimiter literally** → Mitigation: non-greedy matched-pair removal first; orphan handling only drops up to the next structured marker, never past a real answer boundary; "clean content unchanged → byte-identical" is a spec scenario + test.
- **Hot path on every response** → Mitigation: pure functions, precompiled regexes, early-exit when no `<｜`/`<think>` substring present (cheap `in` check before regex); flag-off = identity.
- **DSML grammar drift (model changes its protocol)** → Mitigation: malformed payload is logged + markup still stripped (never leaks); fixtures from the real incident pin current grammar; flag lets us disable fast.
- **Double tool execution if both structured + DSML present** → Mitigation: D4 precedence rule (structured wins, DSML ignored when structured exists) + explicit spec scenario/test.
- **Regression to `reasoning_content`-400 round-trip** → Mitigation: sanitizer touches only `content`; structured fields pass through untouched; dedicated no-regression spec scenario + test asserting structured-path responses are byte-identical.

## Migration Plan

1. Land pure module + tests (red→green) — no wiring yet, zero runtime impact.
2. Wire both provider paths behind the default-on flag.
3. Full pytest (`.venv` interpreter) — assert 0 regressions, new tests green.
4. Live E2E: code-mode write_file on deepseek-v4-pro produces a clean file (no `<｜` markup); screenshot evidence.
5. Rollback: set `[llm] sanitize_inline_cot_dsml = false` → instant legacy passthrough (no redeploy).
6. `--no-archive`: change stays active until demo-day sign-off.

## Open Questions

- Exact config accessor for `[llm]` block (reuse the same plumbing the existing `[llm]` runtime override uses) — resolve during apply by reading the config module.
- Whether `edit_file` (old_string/new_string) needs the same DSML guard as `write_file` — sanitizer is content-agnostic so it covers both; confirm no separate arg path bypasses the provider chokepoint during apply.
