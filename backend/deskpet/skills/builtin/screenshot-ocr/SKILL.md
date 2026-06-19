---
name: screenshot-ocr
description: 识别图片/截图里的文字（中英文，本地 OCR 引擎）
when_to_use: 用户要识别图片或截图里的文字、OCR 提取文本时
triggers: [ocr, OCR, 识别文字, 图里的字, 截图文字, 图片文字]
version: 0.1.0
author: deskpet
task_types: [task]
argument_hint: <图片文件>
requires_script: false
---

用户想识别一张图片 / 截图里的文字。

执行步骤：

## 1. 拿到图片路径

- 用户直接给了图片路径，且是刚生成/明确的本地路径：可直接用。
- 否则调 `office_pick_file`（kind='file', filter_key='image'）弹选择器，
  让用户选图片。你不能自己猜路径。

## 2. 调 `image_ocr` 工具

参数 `image_path`。返回 `{"ok": true, "text": "...", "lines": [...], "line_count": N}`。

## 3. 回复用户

- ✅ 成功：把识别出的 `text` 完整给用户。如果用户还要求「翻译/总结/整理」
  这段文字，再接着做。
- ❌ `error == "ocr_engine_missing"`：明确告诉用户「OCR 组件不可用」，
  **不要假装识别成功**。
- ❌ 其他失败：如实说明（图片损坏 / 不是图片格式等）。

注意：OCR 对清晰、水平、对比度高的文字效果最好；对手写、艺术字、
强透视的图可能识别不全 —— 结果存疑时如实提醒用户。

用中文回复。
