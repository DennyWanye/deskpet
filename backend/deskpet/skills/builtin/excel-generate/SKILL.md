---
name: excel-generate
description: 把数据或需求描述变成一份专业的 .xlsx 表格（本地生成，含公式/图表/样式）
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
- 第一行是表头时设 `header_row: true`（自动加粗 + 底色）。
- 数据多时设 `freeze_panes` 冻结表头行。
- 有对比/趋势数据时**加一个 chart**（bar=对比，line=趋势，pie=占比）。
- 金额/数量列适合配 `conditional_format`（color_scale 色阶，data_bar 数据条）。

## 2. 调 `excel_create` 工具

参数：
- `spec`：上一步的结构（dict 或 JSON 字符串都接受）
- `output_path`：用户指定就用；否则不传，工具生成到系统 temp

返回 `{"ok": true, "path": "...", "sheet_count": N}`。

## 3. 回复用户

- ✅ 成功：「表格已生成：`<path>`」+ 一句话说明做了几个 sheet、含什么。
- ❌ 失败（`ok:false`）：如实说明原因（一般是 openpyxl 没装），**不要假装成功**。

用中文回复。
