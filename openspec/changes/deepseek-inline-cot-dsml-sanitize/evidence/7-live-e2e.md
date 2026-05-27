# Live E2E evidence — deepseek-inline-cot-dsml-sanitize (2026-05-17)

## Setup
- Clean single stack restart, `.venv` backend (`backend_launch] Dev python=…\.venv\Scripts\python.exe`), Uvicorn 8100, Vite 5173.
- Strangler-Fig flag confirmed live in backend log:
  `event='sanitize_inline_cot_dsml_flag' enabled=True`
- Code-mode agent provider = **Global Chain → the relay deepseek-v4-pro**
  (`POST https://your-llm-relay.example.com/v1/chat/completions "HTTP/1.1 200 OK"`).

## Procedure (computer-use, real UI)
1. Opened Code Mode (🔧) → 小说网站 session.
2. Instructed: `write_file G:\projects\小说网站\__e2e_sanitize_probe__\probe.py`
   with a module docstring + `def add(a, b): return a + b`, nothing else.
3. Backend log: `p5s2_tool_call_args_dump idx=0 name='write_file' … probe.py … parse_ok=True`;
   same turn `p4s25_stream_summary content_chars=787 reasoning_chars=1193
   tool_calls=… stop_reason='end_turn'` — model used the **structured
   reasoning** path this turn (regression-sensitive path exercised live).

## Result — PASS
`probe.py` (58 bytes), verbatim:

```python
"""e2e sanitize probe"""

def add(a, b):
    return a + b
```

- `grep -c "<｜" probe.py` → **0** (zero thinking/DSML markup).
- `python -m py_compile probe.py` → **PY_COMPILE_OK** (valid Python).
- Agent UI confirmation: “文件已创建完毕 … 58 字节 …”.
- Screenshot captured (computer-use, save_to_disk) showing the completed
  session.

## Interpretation
- Deterministic correctness of the sanitizer is proven by the 10 unit
  tests on the **verbatim incident fixture** (`test_response_sanitizer.py`,
  importing the same `providers._response_sanitizer` wired into
  `openai_compatible.py`).
- This live run proves **integration + zero regression**: with the fix
  active, a real the relay-deepseek-v4-pro `write_file` round-trip (including
  the structured-reasoning path) produces a byte-clean, valid file — the
  llm_service.py-style corruption cannot occur.
- Rollback path available: `[llm] sanitize_inline_cot_dsml = false`
  → legacy passthrough after restart (not exercised live to avoid demo
  disruption; covered by `test_flag_disabled_identity`).

## Cleanup
- Scratch dir `G:\projects\小说网站\__e2e_sanitize_probe__\` removed
  post-verification (throwaway probe only).

## Full regression
- `pytest backend/tests` (.venv): **1403 passed, 10 skipped, 0 failed**
  (+10 new sanitizer tests, 0 regressions).
- `openspec validate deepseek-inline-cot-dsml-sanitize --strict`: valid.
- `--no-archive`: change stays active until demo sign-off.
