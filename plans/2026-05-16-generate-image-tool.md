# Plan — generate_image tool (2026-05-16)

## Goal
User says 生成图片 → agent calls `gpt-image-2` via the existing chinzy
endpoint → saves PNG to workspace → opens it with the OS default viewer.

## Decisions (user-confirmed)
- **Endpoint**: existing `https://chinzy.com/v1` + same api_key, `POST
  /v1/images/generations`, `model="gpt-image-2"`. Reuse the exact creds
  the working chat path uses (`<user_data>/llm_runtime.json`
  base_url+api_key, fallback config + resolve_cloud_api_key()).
- **Vehicle**: a **registry tool** (not a skill) — agent invokes tools
  in the loop; flat module `deskpet/tools/image_tools.py` is
  auto-discovered (same as web_tools.py/file_tools.py), zero main.py
  change.
- **Open**: `os.startfile` on Windows (best-effort, guarded; tool still
  succeeds if open fails). Pet replies with the saved path.
- **Name = `generate_image`** — exactly matches capability_gate.py
  image `tool_markers[0]`, so registering it auto-flips image requests
  from REFUSE → PASS (consistent with the companion-context-isolation
  design: "新增图像工具自动放行").

## Files
1. NEW `backend/deskpet/tools/image_tools.py`
   - `_resolve_endpoint() -> (base_url, api_key)`: read
     `paths.user_data_dir()/llm_runtime.json`; fallback config.toml
     `[llm].base_url` + `resolve_cloud_api_key()`.
   - `_handle_generate_image(args, task_id="") -> str(JSON)`:
     - `prompt` required (→ `{ok:false,error,hint}` if missing, mirrors
       write_file `_err` pattern)
     - `size` optional, default `"1024x1024"`
     - model: `args.get("model")` → config `[image].model` → `"gpt-image-2"`
     - POST `{base_url}/images/generations`
       `{model,prompt,size,n:1}` + `Authorization: Bearer {key}`,
       httpx timeout 120s
     - response: `data[0].b64_json` (decode) OR `data[0].url`
       (download via httpx) → save
       `<user_data>/workspace/genimg_<UTCstamp>.png`
     - `os.startfile(path)` guarded by `sys.platform=="win32"`,
       try/except → `opened: bool`
     - return `{ok:true, path, opened, prompt, model}`
     - any failure → `{ok:false, error, hint}` (never raise)
   - `registry.register("generate_image","image",_SCHEMA,
     _handle_generate_image, permission_category="network",
     timeout_seconds=120.0)`
2. NEW `backend/tests/test_generate_image_tool.py` (TDD, httpx mocked)
3. `config.toml` — optional `[image] model = "gpt-image-2"` (default
   works without it; documents the knob)

## Test cases (red→green)
- missing prompt → ok:false + hint
- b64_json response → png written to workspace, ok:true, path under workspace
- url response → downloaded + saved
- API non-200 → ok:false, no raise
- registered: `registry` has `generate_image`, toolset `image`
- write stays in workspace (D3-safe: workspace IS the allowed root)
- os.startfile failure → still ok:true, opened:false (best-effort)

## Out of scope
- No inline chat thumbnail (frontend) — pet replies with path; user
  opens via viewer / it auto-opens. Thumbnail = separate later.
- No ret/queue/streaming — single synchronous gen, 120s timeout.

## Verification
- pytest test_generate_image_tool.py + full suite (0 regress)
- live E2E (computer-use): companion session 说"帮我生成一张猫咪海报"
  → capability_gate PASS (no longer REFUSE) → image saved to workspace
  → viewer opens → screenshot evidence.
