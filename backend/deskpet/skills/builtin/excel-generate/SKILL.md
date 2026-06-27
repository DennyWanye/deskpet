---
name: excel-generate
description: 把数据或需求描述变成一份专业的 .xlsx 表格（本地生成，含公式/图表/样式）
when_to_use: 用户要生成 Excel 表格、把数据整理成 xlsx、要带公式图表的统计报表时
triggers: [excel, Excel, xlsx, 做表格, 整理成表格, 统计报表]
version: 0.1.0
author: deskpet
task_types: [task, plan]
argument_hint: <数据/需求描述> [--out=PATH]
requires_script: false
---

我希望你帮我做一份 Excel 表格。

输入可能是：
1. 一段需求（例如「做一个本月开支统计表」）
2. 一份已有数据（粘贴的文本 / CSV）
3. 一个明确的表格结构要求

执行步骤（**严格按顺序**）：

## 1. 设计表格结构

先想清楚：几个 sheet、每个 sheet 的列、哪些单元格要公式、要不要图表。

把它整理成 `excel_create` 的 `spec`（一个 dict）：

```json
{
  "sheets": [
    {
      "name": "开支",
      "rows": [
        ["类别", "金额"],
        ["餐饮", 1200],
        ["交通", 300],
        ["合计", "=SUM(B2:B3)"]
      ],
      "header_row": true,
      "freeze_panes": "A2",
      "conditional_format": {"range": "B2:B3", "rule": "color_scale"},
      "chart": {"type": "bar", "title": "开支分布",
                "data": "B1:B3", "categories": "A2:A3", "anchor": "D2"}
    }
  ]
}
```

**内容质量要求**：

- 任何需要计算的单元格**用公式**（值以 `=` 开头），不要自己算好填死。
- 第一行是表头时设 `header_row: true`（自动加粗 + 深蓝底白字）。
- 数据多时设 `freeze_panes` 冻结表头行。
- 有对比/趋势数据时**加 chart**（bar=对比，line=趋势，pie=占比）。一个 sheet
  要多张图用 `charts: [ {...}, {...} ]`（列表）。
- 金额/数量列适合配 `conditional_format`（color_scale 色阶，data_bar 数据条）。

### 复杂表格进阶字段（让表格更专业，按需用）

- **数字格式** `number_formats`：列表，每项 `{col:"B", format:"#,##0.00"}`（千分位）
  或 `{col:"C", format:"0.0%"}`（百分比）或 `{range:"D2:D9", format:"yyyy-mm-dd"}`。
  金额一律配千分位、占比配百分比 —— 不要把 `0.25` 当 `25%` 死填。
- **合并单元格** `merge_cells`：`["A1:E1"]`（跨列大标题）、`["A2:A5"]`（跨行分类）。
- **逐格样式** `cell_styles`：列表，每项 `{range:"A1:E1", bold:true, fill:"#1F4E78",
  font_color:"#FFFFFF", align:"center", border:true, wrap:true}`。给大标题行 / 合计行
  / 重点单元格上色描边。
- **配图** `images`：`[{path:"<本地图片路径>", anchor:"H2", width:320}]`（先用出图工具
  生成 logo/插图再嵌）。

完整示例（带合并标题 + 千分位 + 合计行描边 + 双图表）：

```json
{
  "sheets": [{
    "name": "2026Q2开支",
    "rows": [
      ["2026 第二季度开支汇总", null, null, null],
      ["月份", "餐饮", "交通", "合计"],
      ["4月", 1200, 300, "=SUM(B3:C3)"],
      ["5月", 1500, 280, "=SUM(B4:C4)"],
      ["6月", 1100, 350, "=SUM(B5:C5)"],
      ["季度合计", "=SUM(B3:B5)", "=SUM(C3:C5)", "=SUM(D3:D5)"]
    ],
    "merge_cells": ["A1:D1"],
    "freeze_panes": "A3",
    "number_formats": [{"range": "B3:D6", "format": "#,##0.00"}],
    "cell_styles": [
      {"range": "A1:D1", "bold": true, "fill": "#1F4E78", "font_color": "#FFFFFF", "align": "center", "font_size": 14},
      {"range": "A2:D2", "bold": true, "fill": "#D9E1F2", "border": true},
      {"range": "A6:D6", "bold": true, "fill": "#FCE4D6", "border": true}
    ],
    "charts": [
      {"type": "bar", "title": "月度对比", "data": "B2:C5", "categories": "A3:A5", "anchor": "F2"},
      {"type": "pie", "title": "季度占比", "data": "D3:D5", "categories": "A3:A5", "anchor": "F20"}
    ]
  }]
}
```

## 2. 调 `excel_create` 工具

参数：
- `spec`：上一步的结构（dict 或 JSON 字符串都接受）
- `output_path`：用户指定就用；否则不传，工具自动存到 **`桌宠/OutPut/Excel`**

返回 `{"ok": true, "path": "...", "sheet_count": N}`。

## 3. 回复用户

- ✅ 成功：「表格已生成：`<path>`」—— **必须给出完整路径**（用户要知道存哪了）+
  一句话说明做了几个 sheet、含什么。
- ❌ 失败（`ok:false`）：如实说明原因（一般是 openpyxl 没装），**不要假装成功**。

用中文回复。
