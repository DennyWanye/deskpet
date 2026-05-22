# DeskPet 内测预装技能计划 — 开箱即用的办公技能套件

**创建日期**: 2026-05-22
**目标版本**: `0.6.0-beta`（100 人内测包）
**状态**: 计划待评审
**关联文档**: `plans/2026-05-22-beta-100-readiness.md`、`plans/2026-05-21-memory-system-survey.md`

---

## 0. 背景与目标

100 人内测要给用户一个**到手即用**的版本。当前桌宠已有 `ppt-generate` /
`deep-research` 两个内置技能，但作为"生产力桌宠"还不够 —— 用户拿到手
最想要的是**办公三件套**（Excel / Word / PPT）能直接用，且质量过硬。

### 目标

1. 内测包预装 **8 个高质量内置技能**（P0 三件套 + P1 五个增值技能）。
2. 用户**不用安装任何东西**、不用配置，装好桌宠就能用。
3. 办公文件能**读写用户磁盘上的真实文件**（不锁死在沙盒），但有"防手滑"护栏。
4. 每个技能都有可验证的**质量基线**（评测样例 + 实机点击测试）。

### 非目标

- 不做技能市场/在线下载（marketplace 已有骨架，本轮不动）。
- 不做协同编辑、不做云同步。
- 不在本轮引入付费 API（搜索/OCR 全部本地或免费端点）。

---

## 1. 现状盘点（已确认）

### 技能的两层结构

DeskPet 的"技能"= **Tool + SKILL.md** 两层：

| 层 | 是什么 | 位置 |
|---|---|---|
| **Tool** | 后端可执行能力，注册进 `ToolRegistry` | `backend/deskpet/tools/*.py` |
| **SKILL.md** | 编排提示词，教模型"何时、如何用好这个 tool" | `backend/deskpet/skills/builtin/<name>/SKILL.md` |

`ppt-generate` 就是完整样板：`ppt_tools.py`（tool）+ `builtin/ppt-generate/SKILL.md`。

### 关键机制

- **Tool 自动发现**：`tools/__init__._discover_and_load()` 会 import 每个
  `tools/*.py`，顶层 `registry.register(...)` 自动生效 —— **新增 tool 模块
  无需改 `__init__.py`**。
- **内置技能播种**：首次运行时 installer 把 `skills/builtin/` 整目录播种到
  `<user_data>/deskpet/skills/built-in/`，`SkillLoader` 运行时加载 —— **新增
  `builtin/<name>/SKILL.md` 即自动随包发布**。
- **Tauri dialog 插件**：`tauri-plugin-dialog = "2"` 的 Rust 端已装（当前只
  用于 MessageDialog）→ 文件选择器直接在 Rust 写 `#[command]`，沿用
  `onboarding.rs` / `diagnostics.rs` 模式，**不用加 JS 插件**。

### 现有依赖

已装：`python-pptx`、`trafilatura`、`selectolax`、`Pillow`（随 torch 链）。
缺：`openpyxl`、`python-docx`、OCR 引擎。

---

## 2. 决策记录（用户已拍板 2026-05-22）

| # | 决策点 | 选择 | 含义 |
|---|---|---|---|
| D1 | 预装范围 | **P0 + P1 全装（8 技能）** | 工作量翻倍，但开箱即用感最强 |
| D2 | 文件路径策略 | **任意路径 + 文件选择器** | office 工具脱离 workspace 沙盒；读/改已有文件须经 Tauri 文件选择器由用户显式选定 |
| D3 | PDF 导出 | **捆绑 LibreOffice headless** | 还原度最好；安装包 +150~200MB，需在瘦身评估中单列 |

---

## 3. 技能清单（PRD）

### P0 — 办公三件套（必装）

#### S1. `excel-generate` — 表格生成

- **能力**：从结构化数据 / CSV / 自然语言描述生成 `.xlsx`。
- **特性**：多 sheet、公式、内置图表（柱/折线/饼）、条件格式、单元格样式、
  冻结窗格、自动列宽。
- **Tool**：`excel_create(spec, output_path?)`，`spec` 为 sheet 列表的 JSON。
- **不做**：不读 Excel 做数据分析（那是 `data-chart` 的活）。

#### S2. `doc-edit` — Word 文档新建 + 修改

- **能力**：① 新建 `.docx`；② **修改用户已有的 `.docx`**。
- **修改操作**：查找替换（全文/限定段落）、插入段落/标题、改样式、
  增删表格行、应用模板样式。
- **Tool**：
  - `doc_create(spec, output_path?)` — 新建
  - `doc_edit(file_path, ops)` — 对已有文档应用一组操作（`ops` 为操作列表）
  - `doc_read(file_path)` — 读出结构化大纲供模型决策（段落/标题/表格索引）
- **路径**：`file_path` 必须来自文件选择器（见 §5 防手滑机制）。

#### S3. `ppt-generate` — PPT 质量增强（已有，做增强）

- 现状：3 主题 7 布局，质量已可用。
- **本轮增强**：
  - 新增 `chart` 布局（内嵌柱/饼图）。
  - 新增 `image` 布局的实际配图支持（接 `image_tools` 或用户提供路径）。
  - 模板细节打磨：标题层级间距、配色对比度、页脚页码。
  - 评测样例补齐（见 §7）。

### P1 — 增值技能（强烈建议，本轮全装）

#### S4. `pdf-export` — 导出 PDF

- **能力**：`.docx` / `.pptx` / `.xlsx` / markdown → `.pdf`。
- **实现**：捆绑 LibreOffice headless，`soffice --headless --convert-to pdf`。
- **Tool**：`pdf_export(input_path, output_path?)`。

#### S5. `file-organize` — 文件夹整理

- **能力**：按类型/日期归类、批量重命名、查找重复文件、生成整理报告。
- **实现**：纯 stdlib，无新依赖。
- **Tool**：`file_organize(dir_path, mode, dry_run=true)` —— **默认 dry_run**，
  先出计划给用户确认，再执行。
- **路径**：`dir_path` 须来自文件夹选择器。

#### S6. `translate-doc` — 文档级翻译

- **能力**：翻译 `.docx` / `.txt` / markdown / 纯文本，**保留排版**。
- **实现**：复用现有 LLM 通道（无新依赖）；docx 走 `doc_read` → 逐段翻译
  → `doc_edit` 回填，样式不动。
- **Tool**：复用 `doc_*` + 一个轻量 `translate_text(text, target_lang)`。

#### S7. `web-read` — 单页摘要

- **能力**：给一个 URL，读正文 + 出结构化摘要。比 `deep-research` 轻，
  定位"帮我读这一篇"。
- **实现**：复用现有 `web_extract_article` tool + LLM 总结，**无新 tool**，
  纯 SKILL.md 编排。

#### S8. `screenshot-ocr` — 截图取字

- **能力**：对截图 / 图片做 OCR，提取文字（中英文）。
- **实现**：`rapidocr-onnxruntime`（纯 pip，自带模型，无需系统 Tesseract，
  中文识别好）。
- **Tool**：`image_ocr(image_path)`。
- **依赖体积**：onnxruntime + 模型约 +60~80MB，需在瘦身评估中确认。

---

## 4. 架构与文件清单

### 新增 / 修改文件

```
backend/deskpet/tools/
  office_paths.py        [新] 共用：office 文件路径授权 + 解析（防手滑核心）
  excel_tools.py         [新] excel_create
  doc_tools.py           [新] doc_create / doc_edit / doc_read
  pdf_tools.py           [新] pdf_export（调 LibreOffice）
  file_organize_tools.py [新] file_organize
  ocr_tools.py           [新] image_ocr（RapidOCR）
  translate_tools.py     [新] translate_text
  ppt_tools.py           [改] +chart 布局 +image 配图

backend/deskpet/skills/builtin/
  excel-generate/SKILL.md   [新]
  doc-edit/SKILL.md         [新]
  pdf-export/SKILL.md       [新]
  file-organize/SKILL.md    [新]
  translate-doc/SKILL.md    [新]
  web-read/SKILL.md         [新]
  screenshot-ocr/SKILL.md   [新]
  ppt-generate/SKILL.md     [改] 增强后同步

backend/deskpet/skills/builtin/__init__.py  [改] 更新 docstring 技能清单

tauri-app/src-tauri/src/
  file_picker.rs         [新] office_pick_file / office_pick_save / office_pick_dir
  lib.rs                 [改] mod file_picker; 注册 3 个 command

tauri-app/src/bindings/
  file_picker.ts         [新] IPC 封装

backend/pyproject.toml   [改] +openpyxl +python-docx +rapidocr-onnxruntime

tauri-app/src-tauri/     [改] LibreOffice headless 资源捆绑（tauri.conf.json resources）

docs/beta/安装包瘦身评估.md  [改] 追加 LibreOffice / onnxruntime 体积条目
README.md                    [改] 技能清单章节
```

### 数据流（以 doc-edit 改用户文档为例）

```
用户："帮我把这份合同里的甲方名字改成 XXX"
  → 模型调 office_pick_file tool（前端弹 Tauri 文件选择器）
  → 用户选中 contract.docx → 路径进入"本会话已授权"集合
  → 模型调 doc_read(path) 拿到段落结构
  → 模型调 doc_edit(path, [{op:"replace", find:"甲方：旧名", repl:"甲方：XXX"}])
     → doc_tools 校验 path ∈ 已授权集合 → 通过 → 写回
  → 回复用户：已修改，第 3 段
```

---

## 5. 文件路径策略（D2 落地 —— 防手滑机制）

办公工具**不进 `file_tools` 的 workspace 沙盒**。但要防模型幻觉路径乱写
系统文件。机制（"防手滑"级，非沙盒）：

1. **会话授权集合**：`office_paths.py` 维护一个进程内 `set[str]`，记录
   "本会话中用户通过文件选择器显式选定过的路径"。
2. **读 / 改已有文件**：`doc_edit` / `doc_read` / `pdf_export` / `file_organize`
   的输入路径**必须 ∈ 授权集合**，否则返回明确错误，提示模型先调
   `office_pick_file`。
3. **新建文件输出**：`*_create` 的 `output_path` 允许：① 授权集合内的目录；
   ② 系统 temp；③ 不传则默认 temp + 自动文件名。
4. **Tauri 文件选择器**：`file_picker.rs` 三个 command 用
   `tauri-plugin-dialog` 的 `FileDialogBuilder`，返回用户选定路径并
   通过 IPC 回传后端注册进授权集合。
5. **黑名单兜底**：即便在授权集合内，仍拒绝写入 Windows 系统目录
   （`C:\Windows`、`Program Files`、注册表路径等）—— 最后一道防手滑。

> 符合 MEMORY.md「不要沙盒/权限限制 —— 只防手滑级破坏」的定位。

---

## 6. LibreOffice 捆绑方案（D3 落地）

| 步骤 | 内容 |
|---|---|
| 6.1 选型 | LibreOffice Portable（仅保留 `program/soffice` + Writer/Calc/Impress 过滤器，裁掉 UI 资源/语言包）目标压到 ~150MB |
| 6.2 捆绑 | 放进 Tauri `resources/libreoffice/`，`tauri.conf.json` 的 `bundle.resources` 声明 |
| 6.3 路径解析 | 运行时 `paths.rs` 解析资源目录 → 后端通过环境变量 `DESKPET_SOFFICE_PATH` 拿到 `soffice` 绝对路径 |
| 6.4 调用 | `pdf_tools.py` 子进程 `soffice --headless --convert-to pdf --outdir <tmp> <input>`，超时 60s，失败回退"未安装"提示 |
| 6.5 体积评估 | 在 `docs/beta/安装包瘦身评估.md` 单列：LibreOffice ~150MB + onnxruntime ~70MB，给出最终安装包预估 |

> **风险**：与"安装包瘦身"目标直接冲突。6.5 评估若发现总包过大，需回到
> 用户决策：是否把 pdf-export 改为"首次使用时按需下载 LibreOffice"。

---

## 7. 质量基线（"高质量"如何验收）

每个生成类技能配一组**评测样例**，放 `backend/tests/fixtures/skills/`：

| 技能 | 评测样例 | 验收标准 |
|---|---|---|
| excel-generate | 3 个：财务表(公式+条件格式)、项目排期(甘特感)、数据统计(图表) | 文件能被 Excel 打开无报错；公式可计算；图表渲染正常 |
| doc-edit | 新建 2 个 + 改已有 3 个（查找替换/插段/改表格） | 改后文档样式不破坏；只改目标处 |
| ppt-generate | 4 个：商务汇报、技术 Demo、营销、研究报告转 PPT | 6-14 页；bullet ≤5 条；主题配色协调 |
| pdf-export | docx/pptx/xlsx 各 1 | PDF 还原度：排版/字体/图表不丢 |
| screenshot-ocr | 中文截图 / 英文截图 / 中英混排 各 1 | 字符准确率 >95% |

验收方式：**生成真实文件 → 实机打开检查**（非脚本断言），截图存
`plans/test-screenshots/`。

---

## 8. TDD — 测试计划

### 8.1 后端单元测试（pytest）

| 测试文件 | 覆盖 |
|---|---|
| `test_deskpet_office_paths.py` | 授权集合增删；未授权路径被拒；系统目录黑名单；temp 输出放行 |
| `test_deskpet_excel_tools.py` | spec→xlsx；多 sheet；公式写入；图表对象存在；坏 spec 报错不崩 |
| `test_deskpet_doc_tools.py` | create/read/edit 三件；查找替换只改目标；改后 docx 可重新打开；未授权路径被拒 |
| `test_deskpet_pdf_tools.py` | soffice 缺失时优雅降级；mock soffice 成功路径；超时处理 |
| `test_deskpet_file_organize.py` | dry_run 不动文件；归类计划正确；查重；未授权目录被拒 |
| `test_deskpet_ocr_tools.py` | 已知图片 OCR 结果断言；坏图片报错 |
| `test_deskpet_translate_tools.py` | mock LLM；段落映射不丢；空文本处理 |
| `test_deskpet_ppt_tools.py` [改] | 新增 chart/image 布局断言 |

### 8.2 Rust 测试（cargo test）

- `file_picker.rs`：路径回传序列化；取消选择返回 `None` 不 panic。

### 8.3 回归

- backend `pytest`（当前 1662，新增后应 ≥1750，0 回归）
- Rust `cargo test`（当前 59）
- frontend `vitest`（当前 255）
- 全绿才算 Sprint 完成。

### 8.4 人工点击测试脚本（windows-mcp 实机）

每个技能一条端到端用例，写进 `plans/2026-05-22-builtin-skills-manual-test.md`：

| # | 技能 | 操作 | 预期 |
|---|---|---|---|
| B1 | excel-generate | 对桌宠说"做一个本月开支统计表" | 生成 .xlsx，资源管理器高亮，打开无报错 |
| B2 | doc-edit 新建 | "写一份请假条" | 生成 .docx，排版正常 |
| B3 | doc-edit 改 | 选一个已有 docx，"把标题改成 XXX" | 仅标题变，其余不动 |
| B4 | doc-edit 防手滑 | 让模型改一个没选过的路径 | 工具拒绝 + 提示先选文件 |
| B5 | ppt-generate | "做一份关于 X 的 PPT" | 6-14 页，主题协调 |
| B6 | pdf-export | 把 B2 的 docx 导出 PDF | PDF 生成，排版还原 |
| B7 | file-organize | 选一个乱文件夹，"帮我整理" | 先出 dry_run 计划，确认后执行 |
| B8 | translate-doc | "把这段翻译成英文" | 译文正确，排版保留 |
| B9 | web-read | 给一个 URL"帮我读这篇" | 输出结构化摘要 |
| B10 | screenshot-ocr | 给一张中文截图 | 正确提取文字 |

---

## 9. 实施排期（Sprint 划分）

> 多 Agent 并行策略：Sprint 1 / 2 的技能模块彼此独立，可 Git Worktree 隔离
> 后并行 spawn（Claude 写一半 + codex 写一半 / review）。

### Sprint 0 — 共用地基（串行，阻塞后续）

- [ ] `office_paths.py` 路径授权模块 + 测试
- [ ] `file_picker.rs` 三个 Tauri command + `lib.rs` 注册 + binding + 测试
- [ ] `pyproject.toml` 加依赖（openpyxl / python-docx / rapidocr-onnxruntime）
- [ ] 验证依赖在 Windows + Python 3.11 + PyInstaller 链能打包

### Sprint 1 — P0 办公三件套（可并行）

- [ ] excel_tools.py + excel-generate SKILL.md + 测试 + 评测样例
- [ ] doc_tools.py + doc-edit SKILL.md + 测试 + 评测样例
- [ ] ppt_tools.py 增强 + SKILL.md 同步 + 评测样例

### Sprint 2 — P1 文件/网页/翻译（可并行）

- [ ] file_organize_tools.py + file-organize SKILL.md + 测试
- [ ] translate_tools.py + translate-doc SKILL.md + 测试
- [ ] web-read SKILL.md（纯编排，无新 tool）

### Sprint 3 — PDF + OCR（重，单列）

- [ ] LibreOffice Portable 裁剪 + 捆绑 + 路径解析
- [ ] pdf_tools.py + pdf-export SKILL.md + 测试
- [ ] ocr_tools.py + screenshot-ocr SKILL.md + 测试
- [ ] `docs/beta/安装包瘦身评估.md` 体积复评

### Sprint 4 — 收尾验收

- [ ] 全套测试回归（pytest / cargo / vitest 全绿）
- [ ] windows-mcp 实机点击测试（B1-B10）+ 截图
- [ ] README 技能清单 + builtin/__init__.py docstring 更新
- [ ] 内测包构建 + 安装体积确认

---

## 10. 风险登记

| # | 风险 | 影响 | 缓解 |
|---|---|---|---|
| R1 | LibreOffice +150MB 与瘦身目标冲突 | 安装包过大 | Sprint 3 体积复评；超阈值则改"按需下载" |
| R2 | onnxruntime / RapidOCR 打包失败（PyInstaller hook） | OCR 技能不可用 | Sprint 0 先验证打包链；失败则 OCR 降级为 P2 延后 |
| R3 | 任意路径写入误伤系统文件 | 用户数据损坏 | §5 系统目录黑名单 + dry_run 默认 + B4 实机测试 |
| R4 | "高质量"主观、验收扯皮 | 交付争议 | §7 评测样例集 + 实机打开检查（非脚本断言） |
| R5 | docx 复杂排版翻译/编辑后样式破坏 | 用户文档变形 | doc_edit 走"定点操作"而非整体重写；评测样例含复杂排版 |
| R6 | python-docx / openpyxl 与现有 torch/pandas 依赖链冲突 | 后端起不来 | Sprint 0 先装先验证 import 链 |

---

## 10.5 实施偏差记录（2026-05-22 实现时）

| 计划 | 实际 | 原因 |
|---|---|---|
| `file_picker.rs` Rust IPC 文件选择器 | 后端 `picker_tools.py`（PowerShell WinForms 原生对话框）作为 `office_pick_file` 工具 | 后端本就与用户同机（`ppt_tools` 已 shell `explorer`）；单层实现、可单测、无需 Rust/JS 胶水。用户侧体验一致（弹原生对话框）|
| `translate_tools.py` 翻译 tool | `translate-doc` 改为**纯 SKILL.md 编排** | 模型本身即 LLM，翻译是其本职；复用 `doc_read`/`doc_edit` 做 I/O，无需 LLM-in-tool 管线 |
| 改 `tauri.conf.json` / `lib.rs` / binding | 本轮**零 Rust / 前端改动** | 文件选择器下沉到后端工具后，前端无新增 |

工具最终清单（8 个，全部注册到 `office` toolset，自动发现）：
`office_pick_file` / `excel_create` / `doc_create` / `doc_read` / `doc_edit` /
`file_organize` / `pdf_export` / `image_ocr`。

## 11. 验收标准（Definition of Done）

1. 8 个技能全部随内测包发布，首次启动自动播种到用户技能目录。
2. 后端 / Rust / 前端三套测试全绿，0 回归。
3. B1-B10 实机点击测试全部通过，截图留证。
4. §7 评测样例生成的真实文件实机打开检查通过。
5. 安装包体积有明确数字，且用户知情确认。
6. README + beta 文档同步更新。
