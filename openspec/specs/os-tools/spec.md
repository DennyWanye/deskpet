# os-tools Specification

## Purpose
TBD - created by archiving change deskpet-skill-platform. Update Purpose after archive.
## Requirements
### Requirement: read_file tool

The system SHALL provide `read_file(path: str, offset: int = 0, limit: int = 2000)` returning file content (or chunk for large files). The tool SHALL be registered with `permission_category="read_file"`.

#### Scenario: Read text file
- **WHEN** `read_file(path="C:\\Users\\me\\Desktop\\todo.txt")` is called and the file exists with content "milk\neggs"
- **THEN** the tool returns `{"content": "milk\neggs", "lines": 2, "truncated": false}`

#### Scenario: Read non-existent file
- **WHEN** path does not exist
- **THEN** tool returns `{"error": "FileNotFoundError", "path": "..."}`

#### Scenario: Read large file with offset/limit
- **WHEN** file has 5000 lines and `read_file(path, offset=100, limit=50)` is called
- **THEN** result has 50 lines starting from line 101 and `truncated=true`

#### Scenario: Read sensitive path triggers permission popup
- **WHEN** path matches sensitive patterns (`.env`, `id_rsa`, browser cookies)
- **THEN** permission_category is upgraded to `read_file_sensitive` and user is prompted

### Requirement: write_file tool

The system SHALL provide `write_file(path: str, content: str, overwrite: bool = false)`. Permission category `write_file`. SHALL create parent directories if missing.

#### Scenario: Create new file
- **WHEN** `write_file(path="C:/tmp/note.txt", content="hello")` is called and parent `C:/tmp` exists
- **THEN** file is created and tool returns `{"path": "...", "bytes_written": 5}`

#### Scenario: Overwrite refused without flag
- **WHEN** target exists and `overwrite=false`
- **THEN** tool returns `{"error": "FileExistsError"}` without writing

#### Scenario: Permission denied
- **WHEN** user denies the write_file popup
- **THEN** tool returns `{"error": "permission denied"}`

### Requirement: edit_file tool

The system SHALL provide `edit_file(path: str, old_string: str, new_string: str, replace_all: bool = false)` using exact string match (no regex). SHALL fail if `old_string` is not unique unless `replace_all=true`.

#### Scenario: Single replacement
- **WHEN** file contains "foo bar baz" and `edit_file(path, "bar", "BAR")` is called
- **THEN** file becomes "foo BAR baz" and tool returns `{"replacements": 1}`

#### Scenario: Old string not unique fails
- **WHEN** file contains "x x x" and `edit_file(path, "x", "y")` (replace_all=false)
- **THEN** tool returns `{"error": "old_string is not unique (3 matches); use replace_all=true"}`

#### Scenario: replace_all
- **WHEN** `edit_file(path, "x", "y", replace_all=true)` against "x x x"
- **THEN** file becomes "y y y" and tool returns `{"replacements": 3}`

### Requirement: list_directory tool

The system SHALL provide `list_directory(path: str, max_entries: int = 100)` returning structured list of files and subdirectories.

#### Scenario: List a directory
- **WHEN** `list_directory(path="C:/Users/me/Desktop")` is called
- **THEN** result is `{"entries": [{"name": "todo.txt", "type": "file", "size": 23}, {"name": "Projects", "type": "dir"}]}`

#### Scenario: Truncated listing
- **WHEN** dir has 500 files and max_entries=100
- **THEN** result has first 100 + `truncated=true`

### Requirement: run_shell tool

The system SHALL provide `run_shell(command: str, cwd: str | None = None, timeout: int = 30)`. Permission category `shell`. MUST reject commands matching deny patterns before any user prompt. MUST capture stdout + stderr + exit_code.

#### Scenario: Successful command
- **WHEN** `run_shell(command="echo hello")` is called and user approves
- **THEN** result is `{"stdout": "hello\n", "stderr": "", "exit_code": 0}`

#### Scenario: Deny pattern rejected
- **WHEN** `run_shell(command="rm -rf /")` is called
- **THEN** tool returns `{"error": "command rejected by deny pattern: rm -rf"}` WITHOUT prompting user

#### Scenario: Timeout
- **WHEN** command runs > timeout
- **THEN** process is killed and result is `{"error": "timeout", "stdout_partial": "..."}`

### Requirement: web_fetch tool

The system SHALL provide `web_fetch(url: str, max_bytes: int = 1_000_000)`. Permission category `network`. Returns text content (HTML stripped to readable text).

#### Scenario: Fetch URL
- **WHEN** `web_fetch(url="https://example.com")` is called and user approves
- **THEN** tool returns `{"url": "...", "status": 200, "text": "Example Domain ...", "content_type": "text/html"}`

#### Scenario: Refuse non-http(s)
- **WHEN** `web_fetch(url="file:///etc/passwd")` is called
- **THEN** tool returns `{"error": "scheme must be http(s)"}`

### Requirement: desktop_create_file tool

The system SHALL provide `desktop_create_file(name: str, content: str)` as ergonomic wrapper that resolves to `<user_desktop>/<name>` and calls write_file. Permission category `desktop_write`.

#### Scenario: Create todo.txt on desktop
- **WHEN** `desktop_create_file(name="todo.txt", content="吃饭买菜")` is called and user approves
- **THEN** file is created at `%USERPROFILE%\Desktop\todo.txt` (Windows) / `~/Desktop/todo.txt` (Mac/Linux)
- **AND** the tool returns `{"path": "<absolute>", "platform": "windows"}`

#### Scenario: Cross-platform desktop resolution
- **WHEN** `desktop_create_file` runs on each OS
- **THEN** Windows uses `%USERPROFILE%\Desktop`, macOS uses `~/Desktop`, Linux falls back to `~/Desktop` then `xdg-user-dir DESKTOP`

