# Stage 0 — 工具产物 last-mile 现状审计（WI-T0.1）

**日期**：2026-05-23
**方法**：逐文件读 backend/deskpet/tools/*.py + tauri-app/src + src-tauri/src

---

## §1 工具产物字段盘点（按工具）

| 工具 | handler 文件 | 成功返回字段 | 产物路径模式 | 静默 fallback | 备注 |
|---|---|---|---|---|---|
| ppt_create | ppt_tools.py:_handle_ppt_create | `ok/path/slide_count/theme` | `tempfile.gettempdir()/deskpet-ppt-<ts>.pptx` | ✅ `_HAS_PPTX`：返回 `markdown_fallback` 字符串 | 失败时 `markdown_fallback` 字段含文本内容但无磁盘文件 |
| excel_create | excel_tools.py:_handle | `ok/path/sheet_count` | 走 `office_paths.resolve_for_write` → 默认 `tempfile.gettempdir()/deskpet-excel-<ts>.xlsx` | ✅ `_HAS_OPENPYXL`：返回错误无 fallback | 依赖 openpyxl |
| doc_create | doc_tools.py:_handle_create | `ok/path/element_count` | 同 `office_paths.resolve_for_write` → tempdir | ✅ `_HAS_DOCX`：返回错误无 fallback | 依赖 python-docx |
| doc_read | doc_tools.py:_handle_read | `ok/path/paragraphs/tables` | 输入路径（需已经过 office_pick_file 授权） | 无 | 读工具，不生成产物 |
| doc_edit | doc_tools.py:_handle_edit | `ok/path/applied/ops` | 原地修改授权路径 | 无 | 写工具，path 是修改后的原文件路径 |
| pdf_export | pdf_tools.py:_handle | `ok/path/size_bytes` | 同 `office_paths.resolve_for_write` → tempdir | 无（soffice 缺失返回 `error=soffice_missing`，不静默） | 依赖 LibreOffice；产物 .pdf |
| image_ocr | ocr_tools.py:_handle | `ok/path/text/lines/line_count` | 输入图片路径（授权） | ✅ `ocr_engine_missing`：返回错误不静默 | 读工具；path 是输入路径非输出 |
| generate_image | image_tools.py:_handle_generate_image | `ok/path/opened/prompt/model` 或 `ok/status/job_id/message` | `<user_data>/workspace/genimg_<ts>.png` | 无 | 异步路径返回 job_id；同步路径调 `os.startfile` 自动打开 |
| file_organize | file_organize_tools.py:_handle | `ok/mode/dry_run/moved/file_count` | 目标目录（用户授权文件夹内的子文件夹） | 无 | dry_run=true 只返回 plan，不生成文件 |
| office_pick_file | picker_tools.py | `ok/path` | 用户选择的路径（native dialog） | 无 | 授权工具，返回用户选择的路径 |
| research_run | research_tools.py:_handle_research_run | `ok/topic/summary/sections/citations/coverage` | 无磁盘产物（纯文本报告 in result） | 无 | 返回 Markdown 文本，不写文件 |
| file_write | file_tools.py:_handle_file_write | `bytes_written/path/mode` | `<APPDATA>/deskpet/workspace/<相对路径>` | 无 | 仅写 workspace 内 |
| file_read | file_tools.py:_handle_file_read | `content/lines_read/path` | workspace 内相对路径 | 无 | 读工具 |
| os_tools/write_file | os_tools/write_file.py | `path/bytes_written` | 用户提供绝对路径（有 root 限制） | 无 | code-panel 工具，路径由 LLM 决定 |

**产物类型汇总**：

- 生成文件型（有 `path` 字段，是"新建产物"）：`ppt_create` / `excel_create` / `doc_create` / `pdf_export` / `generate_image`
- 修改文件型（`path` 指原文件）：`doc_edit` / `file_write`
- 输入文件型（`path` 是授权输入）：`doc_read` / `image_ocr`
- 无磁盘产物型：`research_run` / `todo_write` / `file_organize(dry_run=true)` 等

---

## §2 前端展示现状

**`ToolResultCard` 渲染逻辑**（MessageBubble.tsx L307-L371）：

1. 接收 `name: string`、`ok: boolean`、`result: string`（后端 handler 返回的 JSON 字符串）。
2. 调 `splitToolError(result)` 拆出 `{ body, hint, examples }`：
   - 若 JSON 含 `hint` 字段 → 在卡片顶部显示黄色 `💡` 提示框（仅用于错误时的修复建议）。
   - `body = JSON.stringify(parsed, null, 2)`：直接 pretty-print 整个 JSON，**`path` 字段显示为普通字符串**。
3. 折叠/展开逻辑：≤ 30 行自动展开，> 30 行折叠并显示"点击展开"。
4. `<pre data-bp-selectable="">` 内渲染文本，文本可选中复制（data-bp-selectable）。

**用户可点击的 action**：**无**。`path` / `url` 字段作为纯文本展示，没有任何"在文件夹中显示"/"用 App 打开"/"复制路径"按钮。

**相关 grep 确认**：
- `shell.open`：前端代码中未出现（除了 `noopener noreferrer` HTML 属性）。
- `show_in_folder` / `revealItemInDir` / `clipboard`：前端代码中均未出现。

**Tauri 已注册 command 中与文件/路径相关的**：

| Command | 文件 | 功能 |
|---|---|---|
| `open_log_dir` | user_data.rs | 用 opener 打开日志目录（仅限 logs/） |
| `open_app_data_dir` | user_data.rs | 用 opener 打开 AppData/deskpet/ |
| `purge_user_data` | user_data.rs | 递归删除 user_data 目录 |
| `open_directory_dialog` | commands.rs | 弹 native 文件夹选择器，返回路径 |

以上均用 `tauri_plugin_opener::OpenerExt::open_path`，但**都是管理类目的（日志/AppData 目录），不是工具产物操作**。没有 `artifact_open` / `artifact_show_in_folder` / `artifact_save_as` 等命令。

---

## §3 已有跨平台能力盘点

| Plugin | 状态 | 版本/范围 | 备注 |
|---|---|---|---|
| `tauri-plugin-opener` | **已引入** | Cargo.toml `"2"` | 注册在 lib.rs：`tauri_plugin_opener::init()`；前端暂未通过 `invoke` 使用，仅 Rust 侧 user_data.rs 用于打开目录 |
| `tauri-plugin-dialog` | **已引入** | Cargo.toml `"2"` | 注册在 lib.rs；用于 fatal-error 对话框 + `open_directory_dialog` folder picker；**无 file-save 对话框** |
| `tauri-plugin-fs` | **未引入** | — | Cargo.toml / package.json 均不含 |
| `@tauri-apps/plugin-shell` | **未引入** | — | package.json 不含 |
| `@tauri-apps/plugin-clipboard-manager` | **未引入** | — | package.json 不含 |
| `@tauri-apps/plugin-dialog` | **未引入** | — | package.json 不含（仅 Rust 侧有 tauri-plugin-dialog）|

**结论**：`tauri-plugin-opener`（打开文件/目录）和 `tauri-plugin-dialog`（弹原生 picker）两个最关键插件**在 Rust 侧已经有了**，但前端 JS 没有对应的 invoke 封装，也没有对工具产物路径的任何操作入口。D3 新增的 4 个 artifact_* Tauri command 可直接复用已装的插件，无需新增 Cargo 依赖。

---

## §4 "找不到文件"真实样本（来自历史测试报告）

历史测试报告（`2026-05-22-beta-100-manual-test-results.md` 和 builtin-skills 结果）中没有用户正式抱怨"生成了 PPT 找不到"的记录（因为 builtin-skills 的实机测试是通过 `registry.dispatch()` 直接调用，而非从聊天 UI 触发）。但 PRD §1.2 已将断点明确锁定：

> **B1（来自 PRD §1.2）**：「帮我生成 PPT」→ 用户拿到 `/tmp/deskpet-ppt-1716...pptx` 一串路径，没有「在文件夹中显示」「用 PowerPoint 打开」按钮。

> **B2（来自 PRD §1.2）**：重启/系统清理 → 文件丢失；用户不知道在哪。`tempfile.gettempdir()` 是系统临时目录，Windows 重启后会清除。

> **PRD §5 度量列**（`plans/2026-05-23-tool-last-mile-upgrade/00-PRD.md`）：指标「用户报告'PPT 生成但找不到文件' beta 期工单数」的现状值为"历史 N"，目标为"≤ 1（30 天）"——说明此问题在历史上确实发生过但量未统计。

> **内测技能测试结果**（`2026-05-22-builtin-skills-test-results.md` §4）：「C2 内测默认 LLM 必须支持 function-calling：gemma4:e4b 等小模型不发 tool call，所有技能无法从聊天触发」——这意味着真实用户从聊天触发工具的 E2E 链路尚未在 dev 环境完整验证，B1/B2 断点存在但没有被端到端测试覆盖。

---

## §5 给 PRD 实施的提示（baseline 锁定）

**哪些工具应优先升级（用户用得最多 + 现状最差）**：

1. `ppt_create`：最高频场景，落盘到 tempdir，fallback 最混乱（markdown 字符串伪装成"已生成"）。
2. `excel_create` / `doc_create`：同为 tempdir + 无前端 action，优先级紧随。
3. `generate_image`：已经有 `os.startfile` 自动打开，但 path 依然仅作文本显示在气泡里；升级后应换成 ArtifactCard 提供一致体验。
4. `pdf_export`：依赖 LibreOffice，soffice_missing fallback 已不静默，升级路径清晰。

**哪些 Tauri plugin 必须新引入**（Cargo.toml / package.json 均缺）：

- `@tauri-apps/plugin-clipboard-manager`（前端 JS 侧）：用于 `artifact_copy_path` 写剪贴板。
- Rust 侧 `tauri-plugin-clipboard-manager = "2"`（对应 crate）：如果要在 Rust command 里写剪贴板。

注：`artifact_open` 和 `artifact_show_in_folder` 可复用已装的 `tauri-plugin-opener`（Rust 侧）；`artifact_save_as` 可复用已装的 `tauri-plugin-dialog`（Rust 侧的 `file().save_file()`）。

**哪些 plugin 已经有了可以直接复用**：

- `tauri-plugin-opener`（Rust）：直接用于 `artifact_open(path)` 和 `artifact_show_in_folder(path)` 内的 `open_path`。
- `tauri-plugin-dialog`（Rust）：复用 `DialogExt` 添加 `file().save_file()` 实现 `artifact_save_as`。
- `open_directory_dialog` 的模式（mpsc channel 同步化异步 dialog）可直接作为 `artifact_save_as` 的实现参考。

**字段命名建议（避免和现有冲突）**：

建议采用 PRD D1 规定的 `artifacts: list[ToolArtifact]` 平行于现有 `path`，**不替换 `path`**（D1 向后兼容 3 个版本）。具体而言：

- 现有工具 handler 保留 `{"ok": True, "path": str(out_path), ...}` 不变。
- `registry.execute_tool` 在 `artifact_envelope` flag 开启时，在返回结构外层包装 `artifacts` 数组，`path` 字段读取自工具返回的 `result["path"]`。
- flag 关闭时，`artifacts` 键**不出现**在 tool_result JSON 里（G5 字节级一致保证）。
- `image_ocr`、`doc_read` 的 `path` 字段含义是"输入路径"不是"产物路径"，注册 artifacts 时 kind 应设 `"text"` 而非 `"file"`（避免前端展示"在文件夹中显示"按钮指向输入文件）。
