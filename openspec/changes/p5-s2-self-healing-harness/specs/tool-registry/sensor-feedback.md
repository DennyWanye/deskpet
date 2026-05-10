# Spec: tool-registry / sensor-feedback (MODIFIED)

## MODIFIED Requirements

### Requirement: Tool error responses include remediation hint

Every tool in the registry SHALL extend its error response shape from the legacy `{"error": <str>}` to the structured `{"ok": false, "error": <code>, "hint": <human guidance>, "examples": [<sample valid args>], ...}` form, so the LLM consumer has self-describing recovery information without external supervision.

This applies to ALL tools shipped under `backend/deskpet/tools/`: `run_shell`, `write_file`, `edit_file`, `read_file`, `list_directory`, `desktop_create_file`, `web_fetch`, `glob`, `grep` (and any future addition).

#### Scenario: Missing required parameter returns hint + example

- **GIVEN** caller invokes `write_file({})` (missing both `path` and `content`)
- **WHEN** the tool's schema check fires
- **THEN** the tool SHALL return:
  ```json
  {
    "ok": false,
    "error": "missing_required_parameters",
    "missing": ["path", "content"],
    "hint": "write_file 需要 path 和 content 两个参数。例如 {\"path\": \"./hello.txt\", \"content\": \"hi\"}.",
    "examples": [
      {"path": "./notes/today.md", "content": "# Today\n- ..."},
      {"path": "main.go", "content": "package main\n"}
    ]
  }
  ```

#### Scenario: Type-mismatch returns expected vs actual

- **GIVEN** caller invokes `read_file({"path": 42})` (path should be string)
- **WHEN** schema check fires
- **THEN** tool SHALL return error containing `"expected": "string"`, `"got": "integer"`, plus a hint pointing at the type

#### Scenario: File-not-found suggests adjacent paths

- **GIVEN** caller invokes `read_file({"path": "./mian.go"})` and the file doesn't exist
- **WHEN** the OS reports ENOENT
- **THEN** tool SHALL return `{"ok": false, "error": "file_not_found", "hint": "./mian.go 不存在。也许你想读 ./main.go ?", "did_you_mean": ["./main.go"]}`
- **AND** the `did_you_mean` list SHOULD contain at most 5 fuzzy-match candidates from the same directory

#### Scenario: Operation blocked (overwrite without flag) explains the safety guard

- **GIVEN** caller invokes `write_file({"path": "existing.txt", "content": "x"})` and the file exists
- **WHEN** the safety check trips
- **THEN** tool SHALL return:
  ```json
  {
    "ok": false,
    "error": "would_overwrite",
    "hint": "existing.txt 已存在。如要覆盖请加 overwrite: true，或改用 edit_file 增量修改。",
    "alternatives": ["pass overwrite=true", "use edit_file"]
  }
  ```

#### Scenario: Timeout error tells caller actual elapsed time

- **GIVEN** `run_shell({"command": "sleep 100", "timeout": 5})`
- **WHEN** the timeout fires
- **THEN** tool SHALL return:
  ```json
  {
    "ok": false,
    "error": "timeout",
    "hint": "命令在 5 秒内未结束被中止。如果是耗时操作（pip install / cargo build），请把 timeout 调大；如果是死循环，先 Ctrl+C 排查。",
    "elapsed_seconds": 5,
    "stdout_partial": "<前 2KB 已捕获的输出>"
  }
  ```

#### Scenario: Hint field is non-empty string for ALL error responses

- **GIVEN** any tool in `os_tools/` returns `ok: false` for any reason
- **THEN** the response SHALL include `hint` field
- **AND** `hint` SHALL be a non-empty `str`
- **AND** `hint` SHALL be in 中文 (consistent with the rest of deskpet)

This is a hard contract — the integration test `test_all_os_tools_error_have_hint_field` SHALL iterate every registered tool, force an error, and assert the contract.
