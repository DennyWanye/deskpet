---
name: pdf-export
description: 把 Word/PPT/Excel 文档导出为 PDF（本地 LibreOffice 引擎，排版还原）
when_to_use: 用户要把 Word、PPT、Excel 文档导出或转换成 PDF 时
triggers: [pdf, PDF]
version: 0.1.0
author: deskpet
task_types: [task]
argument_hint: <要导出的文档>
requires_script: false
---

我希望你帮我把一个文档导出成 PDF。

执行步骤：

## 1. 拿到源文件路径

- 如果是**刚才由其他技能生成的文件**（doc_create / excel_create / ppt_create
  返回的 path）：直接用那个路径，不用再选。
- 如果是**用户已有的文件**：先调 `office_pick_file`（kind='file'）弹选择器，
  让用户选。你不能自己猜路径。

## 2. 调 `pdf_export` 工具

参数：
- `input_path`：源文档路径（.docx / .pptx / .xlsx / .odt 等）
- `output_path`：用户指定就用；否则不传，生成到 temp

返回 `{"ok": true, "path": "...pdf", "size_bytes": N}`。

## 3. 回复用户

- ✅ 成功：「PDF 已导出：`<path>`」。
- ❌ `error == "soffice_missing"`：明确告诉用户「PDF 组件（LibreOffice）
  不可用，无法导出」—— **绝不假装成功**。
- ❌ 其他失败：如实说明原因。

用中文回复。
