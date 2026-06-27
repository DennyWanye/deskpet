---
name: doc-edit
description: 新建 Word 文档，或修改用户已有的 .docx（查找替换/插段落/改表格，保留排版）
when_to_use: 用户要新建 Word 文档、修改 .docx、查找替换文字、插入段落、编辑文档表格时
triggers: [word文档, Word文档, docx, 改文档, 编辑文档, 新建文档]
version: 0.1.0
author: deskpet
task_types: [task, plan]
argument_hint: <新建需求 | 修改需求>
requires_script: false
---

我希望你帮我处理 Word 文档。两种情况：**新建**，或**修改我已有的文档**。

---

## A. 新建文档

把内容整理成 `doc_create` 的 `spec`：

```json
{
  "title": "请假条",
  "elements": [
    {"type": "heading", "text": "请假条", "level": 1},
    {"type": "paragraph", "text": "尊敬的领导：", "bold": false},
    {"type": "paragraph", "text": "本人因事假，申请休假 3 天……"},
    {"type": "table", "rows": [["项目","内容"],["天数","3 天"]], "header": true},
    {"type": "paragraph", "text": "申请人：张三", "align": "right"}
  ]
}
```

元素类型：`heading{text,level,color}`、`paragraph{text,bold,italic,align,font_size,
color}`、`table{rows,header}`、`page_break`。调 `doc_create(spec)`，返回文件路径。
默认存到 **`桌宠/OutPut/Doc`**。

### 复杂文档进阶（正式报告/合同/图文混排，按需用）

- **项目符号 / 编号列表** `{"type":"list","items":["要点一","要点二"],"ordered":false}`
  （`ordered:true` 为 1.2.3 编号；`level` 可缩进嵌套）。
- **段内混排**（一段里多种格式）`{"type":"paragraph","runs":[
  {"text":"重点","bold":true,"color":"#C00000"},{"text":"正常文字"}]}`。
- **字体颜色 / 下划线**：段落或 run 上加 `color:"#RRGGBB"` / `underline:true` /
  `font_size:12`。
- **表头底色**：`table` 设 `header:true` 自动深蓝底白字；可 `header_fill:"#RRGGBB"`
  / `header_color:"#RRGGBB"` 覆盖。
- **插图** `{"type":"image","path":"<本地图片路径>","width_in":4.0,"align":"center"}`
  （先用出图工具生成插图/封面图再嵌）。
- **页眉/页脚/页码**（spec 顶层，不是 element）：`"header":"公司机密"`、
  `"footer":"© 2026 DeskPet"`、`"page_number":true`（页脚居中加真页码字段）。

正式报告骨架示例：

```json
{
  "title": "2026 上半年市场分析报告",
  "header": "内部资料 · 注意保密",
  "footer": "DeskPet 研究院",
  "page_number": true,
  "elements": [
    {"type": "heading", "text": "2026 上半年市场分析报告", "level": 0, "color": "#1F4E78"},
    {"type": "heading", "text": "一、核心结论", "level": 1},
    {"type": "list", "ordered": true, "items": ["市场规模同比增长 23%", "头部集中度提升"]},
    {"type": "paragraph", "runs": [
      {"text": "关键风险：", "bold": true, "color": "#C00000"},
      {"text": "供应链成本上行。"}
    ]},
    {"type": "heading", "text": "二、数据明细", "level": 1},
    {"type": "table", "header": true, "rows": [["季度","营收(万)","同比"],["Q1","1200","+18%"],["Q2","1480","+23%"]]},
    {"type": "page_break"}
  ]
}
```

## B. 修改已有文档（**关键流程，严格按顺序**）

1. **先调 `office_pick_file`**（kind='file'）—— 弹文件选择器让用户选 .docx。
   你**不能**自己猜文件路径；没经过选择器的路径工具会拒绝。
2. 用返回的路径调 `doc_read(file_path)` —— 拿到结构化大纲（每段的
   index / text / style，表格的 index / 行列数）。
3. 根据大纲决定怎么改，调 `doc_edit(file_path, ops)`。`ops` 是操作列表：
   - `{"op": "replace", "find": "旧文本", "replace": "新文本"}` —— 全文替换
   - `{"op": "replace", "find": "...", "replace": "...", "paragraph_index": N}` —— 只改第 N 段
   - `{"op": "set_paragraph_text", "index": N, "text": "整段新文本"}`
   - `{"op": "insert_paragraph", "text": "...", "after_index": N}`
   - `{"op": "set_table_cell", "table_index": 0, "row": 1, "col": 1, "text": "..."}`

**修改原则**：
- 做**定点修改**，不要整篇重写 —— 用户的排版/样式要保住。
- 改之前用 `doc_read` 确认要改的位置，别盲改。
- `doc_edit` 返回里每个 op 有 `status`（ok/skipped）；`skipped` 说明没匹配上，
  要回报用户而不是假装改了。

## 回复用户

- ✅ 成功：说明新建/修改了什么，给出文件路径。
- ❌ 失败：如实说原因。未选文件就想改 → 提示用户「请先选择文件」。

用中文回复。
