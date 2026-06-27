---
name: file-organize
description: 整理文件夹 —— 按类型/日期归类、查找重复文件（先出计划，确认后执行）
when_to_use: 用户要整理文件夹、按类型或日期归类文件、查找清理重复文件时
triggers: [整理文件, 整理一下文件夹, 整理下载, 归类文件, 重复文件]
version: 0.1.0
author: deskpet
task_types: [task]
argument_hint: <要整理的文件夹>
requires_script: false
---

我希望你帮我整理一个文件夹。

执行步骤（**严格按顺序，安全第一**）：

## 1. 让用户选文件夹

先调 `office_pick_file`（kind='dir'）弹文件夹选择器。你不能自己猜路径。

## 2. 出整理计划（dry_run）

调 `file_organize(dir_path, mode, dry_run=true)`：
- `mode="by_type"` —— 按类型归类（图片/文档/表格/视频…）
- `mode="by_date"` —— 按修改月份归类（YYYY-MM）
- `mode="dedup"` —— 找出内容完全相同的重复文件

**第一次必须 `dry_run=true`** —— 工具只返回计划，不动任何文件。

## 3. 把计划给用户看，等确认

把 `plan`（哪些文件会移到哪个子目录）清楚地讲给用户，**问用户是否执行**。

- dedup 模式是只读的，直接把 `duplicate_groups` 列给用户，让用户自己决定删哪个。

## 4. 用户确认后才执行

用户同意 → 再调一次 `file_organize(dir_path, mode, dry_run=false)` 真正移动。
返回 `moved` 是实际移动的文件数。

## 回复用户

- 计划阶段：列出归类计划，问「是否执行？」
- 执行后：说明移动了多少文件。
- 没选文件夹就想整理 → 提示先选。

**绝不**在用户没确认前就 `dry_run=false`。用中文回复。
