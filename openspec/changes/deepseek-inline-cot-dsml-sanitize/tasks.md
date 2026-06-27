## 1. Fixtures from the real incident

- [x] 1.1 Capture verbatim incident fixture (orphan `<｜end▁of▁thinking｜>` + CoT + raw `<｜｜DSML｜｜tool_calls>` `todo_write` block) — embedded as `INCIDENT` in `backend/tests/test_response_sanitizer.py` (faithful to the llm_service.py corruption) + closed-pair / think-tag / clean fixtures
- [x] 1.2 Expected sanitized content + structured tool_calls encoded as test assertions

## 2. Pure sanitizer module (TDD red→green)

- [x] 2.1 RED: test_strip_closed_deepseek_thinking (failed: ModuleNotFoundError)
- [x] 2.2 GREEN: created `backend/providers/_response_sanitizer.py` `strip_inline_reasoning`
- [x] 2.3 test_strip_orphan_unterminated_thinking_incident (strip-alone contract)
- [x] 2.4 test_strip_think_tags_defensive
- [x] 2.5 test_clean_content_byte_identical
- [x] 2.6 RED: test_extract_dsml_todo_write
- [x] 2.7 GREEN: `extract_dsml_tool_calls`
- [x] 2.8 test_dsml_malformed_payload_no_raise_no_leak (raw fallback + WARNING)
- [x] 2.9 test_sanitize_response orchestrator (structured-wins / dsml-recover)
- [x] 2.10 test_flag_disabled_identity — all 10 green

## 3. Config flag (Strangler-Fig)

- [x] 3.1 `[llm] sanitize_inline_cot_dsml = true` added to `config.toml` (commented)
- [x] 3.2 Read once in `main.py` (`config.raw["llm"]`, default True) + logged; registered as known extra in `config.py` `_KNOWN_EXTRAS_BY_DATACLASS`
- [x] 3.3 Covered by test_flag_disabled_identity + default-True read

## 4. Provider wiring — non-streaming path

- [x] 4.1/4.2 `sanitize_response(...)` wired before non-stream `return {...}`; ctor param `sanitize_inline_cot_dsml: bool = True`; `stop_reason="tool_use"` when DSML extracted
- [x] 4.3 Regression: structured `reasoning_content` untouched (full suite green)

## 5. Provider wiring — streaming path

- [x] 5.1/5.2 `sanitize_response(...)` wired at the single final-frame assembly (covers the `chat()` stream-wrapper transitively); per-delta yields left raw by design
- [x] 5.3 Regression: clean streaming + usage/billing + prompt-cache logging intact (full suite green)

## 6. End-to-end regression

- [x] 6.1 test_sanitize_response_recovers_dsml_when_no_structured asserts no `<｜` markup in content derived from a deepseek inline-DSML response (incident cannot reproduce)
- [x] 6.2 Full backend pytest (`.venv`): 1403 passed, 10 skipped, 0 failed (+10 new, 0 regressions)
- [x] 6.3 `openspec validate deepseek-inline-cot-dsml-sanitize --strict` passes

## 7. Live verification + handoff

- [x] 7.1 Clean stack restart (.venv backend, zombies killed); `sanitize_inline_cot_dsml_flag enabled=True` confirmed in live log
- [x] 7.2 computer-use E2E: 小说网站 code-mode `write_file` on the relay deepseek-v4-pro ⇒ probe.py byte-clean (0 `<｜`), `py_compile` OK, agent UI confirmed; screenshot + `evidence/7-live-e2e.md`
- [x] 7.3 Rollback verified via `test_flag_disabled_identity` (deterministic); live flag-flip intentionally NOT run to avoid demo disruption — documented in evidence
- [x] 7.4 NOT archived (active until demo sign-off)
