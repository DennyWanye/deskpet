---
name: translate-doc
description: 翻译文本或整份 Word 文档，保留原排版
version: 0.1.0
author: deskpet
task_types: [task]
argument_hint: <要翻译的文本 | 文档> [目标语言]
requires_script: false
---

我希望你帮我做翻译。两种情况：

---

## A. 翻译一段文本

用户直接给了文字 —— **你自己翻译**（你就是大语言模型，翻译是你的本职）。
要求：准确、通顺、符合目标语言习惯；专有名词保留或加注。直接回复译文。

## B. 翻译整份 Word 文档（保留排版）

1. 调 `office_pick_file`（kind='file'）让用户选 .docx。
2. 调 `doc_read(file_path)` 拿到每一段的 index + text。
3. **逐段翻译**：你自己把每段 `text` 翻成目标语言。
4. 决定回填方式：
   - 如果用户要**原地替换**：对每段调 `doc_edit` 的
     `{"op": "set_paragraph_text", "index": N, "text": "<译文>"}`。
   - 如果用户要**保留原文+译文**：用 `doc_create` 新建一份双语文档，
     或在每段后 `insert_paragraph` 插入译文。
5. 注意：
   - 空段落 / 纯符号段落跳过，不要翻。
   - 标题段（style 含 Heading）也要翻，但保持它仍是标题。
   - 表格内文字若要翻，用 `set_table_cell`。

## 回复用户

- 文本翻译：直接给译文。
- 文档翻译：说明翻译了多少段，给出结果文件路径。

默认目标语言：用户没指定时，中文↔英文互译（看原文语言）。用中文回复说明。
