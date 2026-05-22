# 内测预装技能 — TDD 测试规格

**关联**: `plans/2026-05-22-builtin-skills-beta-plan.md`
**原则**: 测试先行。每个模块下方的用例在写实现前定稿；实现以"让这些用例
全绿"为完成标准。所有 tool 遵守现有契约：handler 同步、返回 JSON 字符串、
异常分类为 retriable/permanent、缺资源返回 `"missing"` 而非崩溃。

---

## T0 · `office_paths.py` — 路径授权（防手滑核心）

`test_deskpet_office_paths.py`

| # | 用例 | 断言 |
|---|---|---|
| T0-1 | `authorize_path(p)` 后 `is_authorized(p)` | True |
| T0-2 | 未授权路径 `is_authorized` | False |
| T0-3 | 授权目录下的子文件 `is_authorized` | True（目录授权递归生效）|
| T0-4 | `resolve_for_read(p)` 未授权 | 返回 `None` |
| T0-5 | `resolve_for_read(p)` 已授权 | 返回规范化绝对 `Path` |
| T0-6 | `resolve_for_write` 目标在系统目录（`C:\Windows`、`Program Files`）| 返回 `None`（黑名单兜底，即便已授权）|
| T0-7 | `resolve_for_write` 目标在系统 temp | 放行 |
| T0-8 | `resolve_for_write` 未传 path | 返回 temp 下自动命名路径 |
| T0-9 | `..` 穿越绕过授权目录 | 规范化后落在授权外 → 拒绝 |
| T0-10 | 授权集合按会话隔离：`clear_authorizations()` 后全部失效 | True |

---

## T1 · `excel_tools.py` — `excel_create`

`test_deskpet_excel_tools.py`

| # | 用例 | 断言 |
|---|---|---|
| T1-1 | 单 sheet + 纯数据 spec | 生成 .xlsx，openpyxl 可重新打开，单元格值正确 |
| T1-2 | 多 sheet spec | workbook.sheetnames 与 spec 一致 |
| T1-3 | 含公式 `=SUM(A1:A3)` | 写入后 cell.value 以 `=` 开头 |
| T1-4 | 含图表 spec（bar/line/pie）| chart 对象存在于 sheet._charts |
| T1-5 | 条件格式 spec | sheet.conditional_formatting 非空 |
| T1-6 | 样式（粗体/背景色/列宽）| cell.font.bold、column_dimensions 生效 |
| T1-7 | spec 为 JSON 字符串（非 list）| 正确解析，不报错 |
| T1-8 | 坏 spec（缺 sheets 键）| 返回 error JSON，retriable=False，不抛异常 |
| T1-9 | output_path 未传 | 落 temp，返回路径存在 |
| T1-10 | output_path 指向系统目录 | 被 office_paths 拒绝 |
| T1-11 | 空 sheets 列表 | 至少生成一个空 sheet，不崩 |

---

## T2 · `doc_tools.py` — `doc_create / doc_read / doc_edit`

`test_deskpet_doc_tools.py`

| # | 用例 | 断言 |
|---|---|---|
| T2-1 | `doc_create` 标题+段落 spec | 生成 .docx，python-docx 可重新打开 |
| T2-2 | `doc_create` 含表格 | document.tables 非空，行列数对 |
| T2-3 | `doc_create` 含标题层级（H1/H2）| 段落 style 名对应 Heading 1/2 |
| T2-4 | `doc_read` 读 T2-1 产物 | 返回结构化大纲：段落索引+文本+style+表格索引 |
| T2-5 | `doc_edit` replace 操作 | 仅匹配段落文本被替换，其余不动 |
| T2-6 | `doc_edit` replace 限定段落索引 | 只改指定 index，同文本其他段不动 |
| T2-7 | `doc_edit` insert_paragraph | 段落数 +1，插入位置正确 |
| T2-8 | `doc_edit` 改表格单元格 | 目标 cell.text 变，其余不变 |
| T2-9 | `doc_edit` 改后样式保留 | 原段落 style/字号未被破坏 |
| T2-10 | `doc_edit` 路径未授权 | 返回 error，提示先选文件，retriable=False |
| T2-11 | `doc_read` 文件不存在 | error JSON，不崩 |
| T2-12 | `doc_edit` ops 为空 | 原文件不变，返回 ok（no-op）|
| T2-13 | `doc_edit` 单个 op 失败（find 无匹配）| 该 op 标记 skipped，其余 op 继续 |

---

## T3 · `pdf_tools.py` — `pdf_export`

`test_deskpet_pdf_tools.py`

| # | 用例 | 断言 |
|---|---|---|
| T3-1 | soffice 不可用（路径未配/不存在）| 返回 error JSON `soffice_missing`，不崩 |
| T3-2 | mock soffice 成功（伪造 .pdf 产物 + 退出码 0）| 返回 ok + pdf 路径 |
| T3-3 | mock soffice 退出码非 0 | 返回 error，含 stderr 摘要 |
| T3-4 | mock soffice 超时 | kill 进程，返回 `pdf_export_timeout` |
| T3-5 | 输入文件不存在 | error JSON，不调 soffice |
| T3-6 | 输入路径未授权 | error，提示先选文件 |
| T3-7 | `find_soffice()` 解析 `DESKPET_SOFFICE_PATH` 环境变量 | 命中时返回该路径 |

---

## T4 · `file_organize_tools.py` — `file_organize`

`test_deskpet_file_organize.py`

| # | 用例 | 断言 |
|---|---|---|
| T4-1 | `dry_run=True`（默认）| 文件系统零改动，返回归类计划 |
| T4-2 | mode=by_type 计划 | .jpg/.png→images、.docx→documents 等分组正确 |
| T4-3 | mode=by_date 计划 | 按文件 mtime 年月分组 |
| T4-4 | `dry_run=False` 执行 by_type | 文件真实移动到子目录 |
| T4-5 | 查重 mode=dedup | 内容相同文件被识别为重复组 |
| T4-6 | 目录未授权 | error，提示先选文件夹 |
| T4-7 | 空目录 | 返回空计划，不崩 |
| T4-8 | 目标子目录已存在同名文件 | 重命名避让（不覆盖）|
| T4-9 | 默认 dry_run：未显式传 dry_run | 视为 True（安全默认）|

---

## T5 · `ocr_tools.py` — `image_ocr`

`test_deskpet_ocr_tools.py`

> RapidOCR 引擎缺失时整组 skip（`pytest.importorskip`）；引擎在则跑真识别。

| # | 用例 | 断言 |
|---|---|---|
| T5-1 | 已知中文文字图片 | 识别文本包含预期关键字 |
| T5-2 | 已知英文文字图片 | 识别文本包含预期英文词 |
| T5-3 | 图片文件不存在 | error JSON，不崩 |
| T5-4 | 非图片文件（传个 .txt）| error JSON，retriable=False |
| T5-5 | 引擎缺失时 import 容错 | tool 仍注册，调用返回 `ocr_engine_missing` |

---

## T6 · `picker_tools.py` — `office_pick_file`（原生文件对话框）

`test_deskpet_picker_tools.py`

> 实现说明：文件选择器最终用**后端 PowerShell 原生对话框**实现（非 Rust
> IPC）—— 后端本就与用户同机（`ppt_tools` 已 shell `explorer`），单层、
> 可单测、无需 Rust/JS 胶水。`translate-doc` 改为**纯 SKILL.md 编排**
> （模型自己翻译 + 复用 doc_read/doc_edit），不再需要 `translate_tools.py`。

| # | 用例 | 断言 |
|---|---|---|
| T6-1 | mock subprocess 返回选定路径 | `ok:true`，路径被 `authorize_path` |
| T6-2 | mock 返回取消 | `ok:false, cancelled:true`（非 error）|
| T6-3 | mock 超时 | `picker_timeout` 错误 |
| T6-4 | 未知 kind | error JSON |
| T6-5 | kind='dir' 选目录 | 目录被授权 |

---

## T7 · `ppt_tools.py` 增强（回归 + 新增）

`test_deskpet_ppt_tools.py`（在现有文件追加）

| # | 用例 | 断言 |
|---|---|---|
| T7-1 | 现有全部用例 | 0 回归 |
| T7-2 | `chart` 布局 | 生成的 slide 含 chart 对象 |
| T7-3 | `image` 布局带合法 image_path | 图片嵌入 slide |
| T7-4 | `image` 布局 image_path 不存在 | 优雅降级为占位框，不崩 |

---

## T8 · 回归门控

| 套件 | 基线 | 通过线 | 实测 |
|---|---|---|---|
| backend pytest | 1662 | 新增后 ≥1730，**0 回归** | ✅ 1734 passed + 1 flaky 重跑过，10 skipped |
| Rust cargo test | 59 | 0 回归 | N/A（本轮无 Rust 改动）|
| frontend vitest | 255 | 0 回归 | N/A（本轮无前端改动）|

> 文件选择器改为后端工具后，本轮**不动 Rust / 前端**，故 cargo / vitest
> 无需重跑。

**完成定义**：backend 全绿 + 上述 T0-T7 全部用例通过（88 个新用例）。
