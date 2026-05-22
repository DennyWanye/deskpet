# 内测预装技能 — 测试执行结果

**执行日期**: 2026-05-22
**被测**: 8 个预装办公技能（office_pick_file / excel_create / doc_create /
doc_read / doc_edit / file_organize / pdf_export / image_ocr）+ 7 个内置
SKILL.md + ppt-generate chart 增强
**关联**: `2026-05-22-builtin-skills-beta-plan.md` / `-tdd.md` / `-manual-test.md`

---

## 1. 自动化测试（TDD）

| 套件 | 结果 |
|---|---|
| 新增单元测试 T0-T7 | **88 passed**（office_paths 11 / excel 11 / doc 13 / file_organize 9 / pdf 7 / ocr 4 / picker 5 / ppt 28）|
| backend 全量回归 | **1734 passed**，1 flaky（`test_enqueue_small_batch_flushes_on_interval`，vector worker 0.3s 计时，重跑通过）, 10 skipped — **0 真实回归** |
| Rust / 前端 | 本轮无改动，未重跑 |

端到端冒烟：直接调用工具生成真实 .xlsx / .docx / .pptx 文件并解析校验，
doc create→read→edit 链路、ppt chart 布局均通过。

## 2. 实机测试（windows-mcp 子代理，opus 4.7）

DeskPet dev 实例正常启动（backend 8100 已连接）。聊天 UI 触发受限于
dev 环境配置的小模型 `gemma4:e4b`（~4B）不发 tool call —— 子代理改为
通过 `registry.dispatch()`（DeskPet 工具分发器**真实代码路径**）逐一
验证 8 个工具，并实机生成 + 严格解析产物。

| 用例 | 结果 | 证据 |
|---|---|---|
| B1 excel-generate | ✅ | 三类+合计齐全，合计为活公式 `=SUM(...)`，表头加粗+底色，列宽自适应 |
| B2 doc-edit 新建 | ✅ | 标题/称呼/正文/落款齐全，排版正常 |
| B3 doc-edit 修改 | ✅ | "事假 2 天"→"3 天"，其余文字/排版未动 |
| **B4 防手滑（一票否决）** | ✅ | 未授权路径被拒；写 `C:\Windows` 被拒；即便加入授权集，黑名单仍拦截；hosts mtime 未变，无文件被改 |
| B5 ppt-generate | ✅ | 6 页多布局，bullet ≤5 条 |
| B6 pdf-export 降级 | ✅ | 无 LibreOffice 时返回 `soffice_missing` + 明确中文提示，不静默失败 |
| B7 file-organize | ✅ | dry_run 只出计划不动文件；确认后 6 文件按类型归类无丢失 |
| B8 translate-doc | ✅ | 纯 SKILL.md 编排（模型翻译 + doc_read/doc_edit），无独立工具 |
| B9 web-read | ✅ | 纯 SKILL.md 编排，复用 web_extract_article |
| B10 screenshot-ocr | ✅ | 正确识别图中中文；引擎缺失时 `ocr_engine_missing` + 明确提示 |

**功能 bug：0。B4 防手滑一票否决项通过。子代理结论：Go。**

## 3. 发版前两个条件（均非代码 bug）

| # | 条件 | 状态 |
|---|---|---|
| C1 | 打包必须包含 `openpyxl / python-docx / python-pptx / rapidocr-onnxruntime` | ✅ 已在 `backend/pyproject.toml` dependencies 声明；`backend/.venv` 已装齐。打包/`pip install -e .` 会带上 |
| C2 | 内测默认 LLM 必须支持 function-calling | ⚠️ 待内测前确认。`gemma4:e4b` 等小模型不发 tool call，所有技能无法从聊天触发。需配置支持 function-calling 的模型（更大的 Qwen/GLM/云端模型）|

## 4. 实施偏差（详见 plan §10.5）

- 文件选择器：Rust IPC → 后端 `picker_tools.py`（PowerShell WinForms 原生对话框）
- translate-doc：独立 tool → 纯 SKILL.md 编排
- 本轮零 Rust / 前端改动

## 5. 结论

✅ **Go** — 8 个预装技能自动化测试（88 新用例 + 1734 回归 0 倒退）
+ 实机工具层测试全部通过，**0 功能 bug**，防手滑一票否决项通过。

C1 已闭环；C2 是内测环境的 LLM 配置项，需在分发内测包时确认默认模型
具备 function-calling 能力。
