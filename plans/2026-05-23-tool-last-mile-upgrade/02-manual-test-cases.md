# 人工测试用例 — DeskPet 工具调用 Last-Mile 升级

- **配套 PRD/TDD**：`00-PRD.md` / `01-TDD.md`
- **日期**：2026-05-23
- **状态**：v2.1（按二轮评审 N3 统一计数语义；MR-9 加 ephemeral 信任面 9-5）
- **执行者**：人 + opus-4.7 子代理（验收循环）
- **环境基线**：Windows 11，Tauri 2.x，Python 3.11，Node 20，git worktree 隔离
- **变更日志**：
  - v2：MR-13 file_exists 升一票否决；MR-9 verify 计数语义统一为「失败计数=3」+ ephemeral subagent 救援；MR-3 加 4 工具子矩阵；MR-5 emoji slug 期望明确；MR-7 加 LLM 不听话回退；MR-19 改为 DPAPI/Keychain 验证；新增 MR-21 flag invariant；MR-22 click metric；MR-23 卸载迁移；MR-24 toolchain 缺失 skip

---

## §1 测试场次与一票否决

| 场次 | 编号 | 一票否决 | 目的 |
|---|---|---|---|
| 零回归 | MR-0 | ✅ | flag 全 off 时与 main 字节级一致；现有 builtin skills + memory-v2 全部不退化 |
| Artifact UX | MR-1 ~ MR-4 | — | 端到端验证产物可被点开、保存、预览 |
| Path 策略 | MR-5 ~ MR-6 | — | 默认路径 / 用户覆盖 / collision 表现 |
| Outline 预览 | MR-7 | — | PPT dry_run UX（含 LLM 不听话回退） |
| Receipt + Verify | MR-8 ~ MR-12 | MR-8 ✅ | fake-completion 真的被抓 + ephemeral subagent 救援 |
| Outcome Verifier | MR-13 ~ MR-15 | **MR-13 ✅** | 四件套触发与回灌闭环；**MR-13 file_exists 升一票否决（outcome verification 心脏）** |
| 跨平台 | MR-16 | — | macOS Tier 2 |
| 性能 / 长会话 | MR-17 ~ MR-18 | — | 延迟 + 长会话稳定 |
| 安全 / 隐私 | MR-19 ~ MR-20 | MR-19 ✅ | HMAC key DPAPI/Keychain + receipt 不进 diagnostic bundle |
| **Flag / 度量 / 迁移 / Toolchain** | **MR-21 ~ MR-24** | — | flag 组合 invariant、点击率埋点、卸载迁移、toolchain 缺失 skip |

> **一票否决（v2 共 4 项）**：MR-0、MR-8、**MR-13**、MR-19 任一不过，整次升级**不允许 merge**，必须修复后重测。

---

## §2 通用前置条件

每场测试开始前完成：

1. `git status` 干净，无 uncommitted。
2. 启动后端：`python backend/main.py`（端口 8200，worktree 隔离环境）。
3. 启动前端：`scripts/dev-worktree.ps1`。
4. 等 Tauri 窗口出现，桌宠空闲态。
5. 打开 DevTools console（前端）+ tail `logs/deskpet.log`（后端）。
6. **截图作为开场基线**。

---

## §3 测试用例

---

### MR-0 — 第一代零回归（一票否决）

**目的**：所有 flag 默认 off 时，DeskPet 行为与 main 字节级一致。

| 步骤 | 操作 | 期望 |
|---|---|---|
| 0-1 | 确认 `config.toml` 不含 `[tools.last_mile]` 与 `[tools.verifier]` 段 | 后端启动无 warning |
| 0-2 | 桌宠对话框输入「现在几点」 | 与 main 回复一致（含字数、emoji） |
| 0-3 | 输入「帮我生成 PPT，主题是 2026 Q2 团队规划，5 页」 | 工具调用流程、卡片样式、回复措辞与 main 一致；产物路径仍在 `tempfile.gettempdir()` |
| 0-4 | 跑 `pytest backend/tests/` 全套 + memory-v2 smoke | 全绿，无新 warning |
| 0-5 | 跑 `npm test` 前端全套 | 全绿 |
| 0-6 | 跑 `tests/golden/tool_result_*.json` 对账 | 0 差异 |

**验收**：6 步全过 + 截图 3 张（桌宠、对话气泡、log 末 50 行）。

---

### MR-1 — 生成 PPT 并一键打开

**前置**：flag `artifact_envelope=true, frontend_artifact_card=true, tauri_artifact_ops=true, default_artifact_dir="<user_data>/artifacts"`。

| 步骤 | 操作 | 期望 |
|---|---|---|
| 1-1 | 对话输入「帮我生成 PPT：DeskPet Beta 100 复盘，4 页」 | 桌宠进入"思考"动效，30s 内出气泡 |
| 1-2 | 检查气泡 | 看到 `ArtifactCard`：title "DeskPet Beta 100 复盘.pptx"、mime icon、size、4 个按钮（打开 / 在文件夹中显示 / 复制路径 / 另存为） |
| 1-3 | 点「打开」 | PowerPoint 启动，文件正确加载，4 张幻灯片 |
| 1-4 | 点「在文件夹中显示」 | Explorer 打开 `<user_data>/artifacts/2026-05-23/ppt_create/`，文件被选中 |
| 1-5 | 点「复制路径」 | 剪贴板含完整路径，DeskPet 顶部出现 toast「已复制」 |
| 1-6 | 点「另存为」 | 原生 file picker 弹出，建议名 "DeskPet Beta 100 复盘.pptx"；保存到桌面后桌面有此文件 |

**验收**：4 个按钮全部能用 + 截图（气泡 + PowerPoint 已打开 + Explorer 选中 + 桌面文件）。

---

### MR-2 — 缺依赖时不再静默 fallback

**前置**：临时 `pip uninstall python-pptx`（用 venv 隔离）。

| 步骤 | 操作 | 期望 |
|---|---|---|
| 2-1 | 对话输入「生成一份 PPT」 | 后端 `ppt_create` 返回 `ok=false, error_class="missing_dependency"` |
| 2-2 | 前端气泡 | 显示**错误卡片**（红色边框），明确文案「python-pptx 未安装，无法生成 PPT。请运行 `pip install python-pptx` 或联系管理员。」，**不**回退成 markdown |
| 2-3 | LLM 续写 | LLM 自述里**不**包含「已为您生成 PPT」类措辞（受 verify gate 影响，见 MR-8） |

**验收**：错误卡可见 + LLM 不撒谎。

---

### MR-3 — Excel / Word / PDF / 图像产物同样可点开

重复 MR-1，把工具换成 `excel_create` / `doc_create` / `pdf_create` / `image_generate`。每个跑 1 次最简流程。

**4 工具子矩阵（v2 新增）**：

| 工具 | mime | 默认 actions | 期望默认 app |
|---|---|---|---|
| `excel_create` | `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` | open / show_in_folder / copy_path / save_as | Excel |
| `doc_create` | `application/vnd.openxmlformats-officedocument.wordprocessingml.document` | 同上 | Word |
| `pdf_create` | `application/pdf` | 同上 + preview（PDF 内联预览卡） | 默认 PDF reader / Edge |
| `image_generate` | `image/png` 或 `image/jpeg` | open / show_in_folder / copy_path / save_as | 系统图片查看器 |

**验收**：4 类产物各能打开 + mime icon 正确 + 截图。

---

### MR-4 — Markdown / 表格 / URL 类产物正确分发

| 步骤 | 操作 | 期望 |
|---|---|---|
| 4-1 | 调 `web_fetch` 抓一段网页 | `kind="url"` artifact，卡片显示 favicon + title + 「在浏览器中打开」按钮 |
| 4-2 | 调 `deep_research` 输出研究报告（markdown） | `kind="text"` artifact，卡片折叠/展开 markdown，「复制」「另存为 .md」按钮 |
| 4-3 | 调 `excel_read` 输出预览表格 | `kind="table"` artifact，前 10 行表格预览 |

**验收**：3 种 kind 各能渲染 + 截图。

---

### MR-5 — 默认保存路径生效 + 中文/emoji 标题

| 步骤 | 操作 | 期望（按 PRD D4 `title_slug` 规则） |
|---|---|---|
| 5-1 | 对话「生成 PPT，标题：营销周报 📊」 | 文件落到 `<user_data>/artifacts/2026-05-23/ppt_create/营销周报-📊-<8hex>.pptx`（emoji **保留**） |
| 5-2 | Explorer 看路径 | 中文 / emoji 正确显示，无 mojibake |
| 5-3 | 再次生成同名 PPT | 文件名末尾加 `-<不同 8hex>`，未覆盖前一文件 |
| 5-4 | 对话「生成 PPT，标题：`Q2 / 2026!`」（含 `/`） | 文件名为 `Q2-2026!-<hash>.pptx`（`/` 折叠为 `-`） |
| 5-5 | 对话「生成 PPT，标题：`<<<`」（全非法字符） | 文件名 fallback 为 `untitled-<hash>.pptx` |
| 5-6 | 对话「生成 PPT，标题：超长 200 字」 | 文件名截到 60 grapheme + `-<hash>.pptx` |

**验收**：6 步全过 + 截图。

---

### MR-6 — 用户自定义 `artifact_dir`

| 步骤 | 操作 | 期望 |
|---|---|---|
| 6-1 | 配 `artifact_dir = "~/Documents/DeskPet"` 后重启 | 启动日志显示「artifact_dir = C:\Users\X\Documents\DeskPet」 |
| 6-2 | 生成 PPT | 文件落到 `C:\Users\X\Documents\DeskPet\2026-05-23\ppt_create\...` |
| 6-3 | 配置无效路径（如 `Z:\nope`） | 启动 warning + fallback 到默认路径 |

**验收**：3 步全过。

---

### MR-7 — PPT outline 预览模式

| 步骤 | 操作 | 期望 |
|---|---|---|
| 7-1 | 配 `outline_preview_default=true` | system prompt 自动加入引导 |
| 7-2 | 对话「帮我做一份 10 页 PPT：2026 产品路线图」 | LLM 先调 `ppt_create(dry_run=true)`，前端显示**只读 markdown outline 预览卡片**，不生成 .pptx |
| 7-3 | 卡片底部应有「确认生成」和「让我调整 outline」两个按钮 | 点「让我调整 outline」→ 输入「把第 3 页换成 Roadmap 季度时间线」 |
| 7-4 | LLM 第二轮调 `ppt_create(dry_run=true)` 重出 outline | 调整后展示新 outline |
| 7-5 | 点「确认生成」 | LLM 调 `ppt_create(dry_run=false)`，出 ArtifactCard，按 MR-1 检查产物 |
| 7-6 | **LLM 不听话回退**：注入 LLM 测试桩直接调 `dry_run=false`（无视 prompt 引导） | agent_loop 在 system message 重述引导一次（仅一次）：「检测到 ≥5 页 PPT 未走预览，建议下次先用 dry_run。」；第二轮 LLM 仍直接生成 → **不再阻拦**，按 MR-1 正常出产物（用户自由意志优先） |

**验收**：3 轮交互流畅 + 5 张截图（初稿 outline、调整请求、调整后 outline、最终 PPT、不听话回退的 system 提醒）。

---

### MR-8 — Fake-completion 抓获（一票否决）

**前置**：`verify_gate_mode = "strict"`。

| 步骤 | 操作 | 期望 |
|---|---|---|
| 8-1 | 用测试桩注入 LLM 输出：「已为您生成 marketing.pptx」**但不调** `ppt_create` | VerifyGate 拒 end_turn，回灌 system message：`[verify-gate] iteration=1 blocked end_turn. Failures: 1. [unmatched_claim] "已生成 marketing.pptx" — no receipt for ppt_create` |
| 8-2 | LLM 第二轮（脚本）正确调 `ppt_create` 并自述 | VerifyGate 通过，产 ArtifactCard |
| 8-3 | 用测试桩注入：「已保存到 D:\fake\path.pptx」但 receipt 显示 path 在 `/artifacts/...` | VerifyGate 拒，回灌 `[path_mismatch]` |
| 8-4 | 跑 50 条 fake-claim fixture | 抓获 ≥ 47/50（95%） |
| 8-5 | shadow 模式（`verify_gate_mode="shadow"`）跑同 8-1 | 通过 end_turn，但 log 有 warn |

**验收**：所有 5 步通过 + 抓获率达标。

---

### MR-9 — Verify 失败的三轮升级链 + 无限回路防护（v2 语义统一）

**计数语义统一（v2.1）**：`failure_count` 起始 0，每次 verify 失败 += 1；**`failure_count == 3` 的瞬间立即调度 ephemeral subagent，不再回灌主 LLM**；ephemeral 仍判 fail 才强退。

| 步骤 | 操作 | 期望 |
|---|---|---|
| 9-1 | 注入 LLM 始终输出 fake claim、永不调工具 | failure_count 1→2 时：每次回灌 system message，主 LLM 重试；**failure_count 走到 3 的瞬间**：**不**再回灌主 LLM，直接调度 `ephemeral_verifier_subagent`（log 显示 `model=haiku, ledger_input_bytes=X, sig_filtered=0`） |
| 9-2 | mock ephemeral subagent 返回 `final_verdict=pass`（即 subagent 认为 ledger 里其实有匹配 receipt，主 regex 漏判） | 整体 verify 通过 → 正常 end_turn；metric `verify.ephemeral_rescued += 1` |
| 9-3 | mock ephemeral subagent 返回 `final_verdict=fail` | 强制 end_turn + 状态标 `verify_exhausted`，前端气泡显示「已达验收重试上限，请人工介入」 |
| 9-4 | 同 9-1 但 `extractor_fallback_enabled=false`，且强行注入 ephemeral 不可用（mock 异常） | failure_count 走到 3 直接强退，不死循环 |
| **9-5** | **ephemeral 信任面（防 ledger 注入）**：构造 ledger 含 3 条 receipt，1 条手动改字节使 `hmac_verify=False` → 触发 ephemeral | ephemeral 实际接收的 ledger 只有 2 条 sig-valid（被剔除的不可见）；metric `verify.sig_invalid_filtered = 1`；告警面板出现 P1 alert |

**验收**：救援链生效 + 信任面隔离 + 不出现无限循环 + 用户看到错误提示。

---

### MR-10 — ClaimPattern 热加载

| 步骤 | 操作 | 期望 |
|---|---|---|
| 10-1 | 修改 `verify/claim_patterns.yaml` 加一条新规则 | 5s 内日志 `claim_patterns reloaded, 11 patterns` |
| 10-2 | 触发匹配新规则的会话 | 命中 |

**验收**：热加载生效 + log 证据。

---

### MR-11 — Receipt 写盘正确

| 步骤 | 操作 | 期望 |
|---|---|---|
| 11-1 | 生成 PPT 后 `cat <user_data>/receipts/<session_id>.jsonl` | 新增 1 行 receipt，含 receipt_id / tool_name="ppt_create" / args_hash / sig |
| 11-2 | 验签 | `python scripts/verify_receipt.py <receipt_line>` 输出 `OK` |
| 11-3 | 篡改 jsonl 文件中的 `tool_name` 字段后重新验签 | 输出 `INVALID_SIGNATURE` |

**验收**：3 步全过。

---

### MR-12 — Receipt 不含敏感信息

| 步骤 | 操作 | 期望 |
|---|---|---|
| 12-1 | 调 facts 相关查询工具，生成 receipt | receipt JSON **不含** facts 表内容，仅 args_hash |
| 12-2 | 调含 API key 的工具（如 web_fetch 用代理），receipt 中 args_hash 后无明文 args | grep 不出 secret |

**验收**：grep 验证 + 截图。

---

### MR-13 — Outcome verifier: file_exists 抓 LLM 路径幻觉 **（v2 升一票否决）**

> **为什么是一票否决**：file_exists 是 outcome verification 的"心脏"——它是把"工具说生成了"和"文件真在那"对齐的最后一道。失效则整个 Stage 2 形同虚设，PRD G4 不成立。

| 步骤 | 操作 | 期望 |
|---|---|---|
| 13-1 | LLM 输出「已生成 /tmp/wrong.pptx」+ 调用 `ppt_create` 实际产文件在 `/artifacts/...` | file_exists verifier 检测 path 不匹配 → 阻 end_turn，回灌 `[path_mismatch]` 行 |
| 13-2 | 工具调用成功但文件被外部删了（手动 `del`）| file_exists 抓不到 → 回灌「声称的文件 X 不存在」（`[file_missing]`） |
| 13-3 | 文件存在但 size=0（手动 truncate） | file_exists 标 fail（`[file_missing]` reason="zero_size"） |
| 13-4 | 文件存在但 sha256 与 receipt 不符（手动改字节） | file_exists 标 fail（`[sha256_mismatch]`） |

**验收**：4 步全过 + 回灌截图 + receipts/<session>.jsonl 中 `error_class="missing_file"` 行。

---

### MR-14 — Outcome verifier: build 回灌

**前置**：`tools.verifier.run_build = true`。

| 步骤 | 操作 | 期望 |
|---|---|---|
| 14-1 | LLM 调 `file_write` 写一段含 ts 错误的 React 组件 | `npm run build` 失败，verifier 提取末 20 行错误 + 分类 `build_error`，回灌 |
| 14-2 | LLM 第二轮按错误信息修正 | 二次 build 通过 → end_turn |

**验收**：build 失败 → 修复链路闭环 + 截图回灌的 system message。

---

### MR-15 — Outcome verifier: test 回灌

| 步骤 | 操作 | 期望 |
|---|---|---|
| 15-1 | LLM 改 `backend/foo.py` 引入测试失败 | scoped pytest 失败 → 回灌末 20 行 + 分类 `test_error` |
| 15-2 | LLM 第二轮修复 | 测试通过 → end_turn |

**验收**：同 MR-14。

---

### MR-16 — macOS Tier 2 兜底

**前置**：在 mac 上跑（M1/M2 任意）。

| 步骤 | 操作 | 期望 |
|---|---|---|
| 16-1 | 重复 MR-1 全部步骤 | 4 按钮全部能用（用 `open` / `open -R` / `pbcopy`） |
| 16-2 | 在 Linux 上跑同流程 | UI 自动隐藏 actions 按钮，title 仍可见，纯路径文本可手动复制 |

**验收**：mac 全过；Linux 优雅降级。

---

### MR-17 — 性能预算

| 步骤 | 操作 | 期望 |
|---|---|---|
| 17-1 | 端到端跑 100 次 PPT 生成（自动化脚本） | flag 全 on 的 p95 延迟相比基线增量 ≤ 800ms |
| 17-2 | 1000 次 receipt 写盘 | 平均 < 5ms |

**验收**：直方图 + p95 数值附在测试报告。

---

### MR-18 — 长会话稳定

| 步骤 | 操作 | 期望 |
|---|---|---|
| 18-1 | 单会话连续 50 轮工具调用 | 内存不泄漏（前后 RSS 增量 < 50MB）；receipt jsonl 行数 = 50；ledger 大小可控（in-mem 仅保留当前 run） |
| 18-2 | 100+ artifacts 一次性渲染 | 前端虚拟滚动启用，无卡顿（FPS ≥ 30） |

**验收**：内存监控截图 + FPS 数。

---

### MR-19 — HMAC key 私密性（一票否决，v2 改为 DPAPI/Keychain）

| 步骤 | 操作 | 期望 |
|---|---|---|
| 19-1 | Windows：用 PowerShell `Get-Item 'HKCU:\...'` 不应能枚举到 DeskPet HMAC key；测试 `<user_data>/secrets/` **不存在该文件**（已走 DPAPI keystore） | OK |
| 19-2 | macOS：`security find-generic-password -s 'deskpet.receipt_hmac'` 能查到（且仅当前用户）；裸文件不存在 | OK |
| 19-3 | 生成 diagnostic bundle | bundle zip 内 `grep -ri receipt_hmac` 0 命中；`grep -r 'secrets/'` 0 命中；`receipts/` 整目录不在包内 |
| 19-4 | 启动期 sanity HMAC echo：注入 mock keystore 取 key 失败 | 自动重生 key + warn log；旧 receipts 迁移到 `receipts/archived/<old_key_hash_prefix>/`，附 `INVALID_SIG_REASON.txt` |
| 19-5 | 极端：DPAPI/Keychain/libsecret 全部不可用（mock 三连失败）→ 回退裸文件 | warn log 显著提示；`<user_data>/secrets/receipt_hmac.key` 创建（0600/ACL）；服务能启动；后续 receipts 用裸 key 签名 |

**验收**：5 步全过 + 命令输出截图 + diagnostic bundle 解压验证截图。

---

### MR-20 — Receipt 与 artifact 链路不串号（多会话并发）

| 步骤 | 操作 | 期望 |
|---|---|---|
| 20-1 | 同时开 3 个会话各生成 1 个 PPT | 3 份 receipt 分别落 3 个 session_id 的 jsonl；artifacts sha256 各不相同 |
| 20-2 | 把 session A 的 receipt 复制到 session B 的 jsonl | 验签发现 session_id 不匹配 → 标 `cross_session_receipt`（log warn） |

**验收**：跨会话不串扰 + 防伪造提示。

---

### MR-21 — Flag 组合 invariant 启动校验（v2 新增）

按 PRD §3 D10 invariant 矩阵：

| 步骤 | 操作 | 期望 |
|---|---|---|
| 21-1 | 改 config.toml：`emit_receipts=false` + `verify_gate_mode="strict"` 启动 | 拒启动，控制台报 `ConfigError(VG-INVARIANT-1: ...)`，进程退出码非 0；日志清晰指出冲突 flag 对 |
| 21-2 | 改 config.toml：`run_build=true` + `verify_gate_mode="off"` 启动 | 启动成功 + warn log + 实际生效配置 `verify_gate_mode=shadow` |
| 21-3 | 改 config.toml：`frontend_artifact_card=true` + `artifact_envelope=false` 启动 | 启动成功 + warn log + 实际生效配置 `frontend_artifact_card=false` |
| 21-4 | 改 config.toml：`artifact_dir_retention_days=0` 或 `=400` 启动 | 拒启动，`ConfigError(retention out of range)` |
| 21-5 | 改回合法配置 | 启动成功，无 warn |

**验收**：5 步全过 + 截控制台错误信息。

---

### MR-22 — 工具产物点击率埋点链路（v2 新增）

**目的**：兑现 PRD §5 度量「工具产物用户点击率 ≥ 60%」的采集前提。

| 步骤 | 操作 | 期望 |
|---|---|---|
| 22-1 | flag `artifact_envelope=true, frontend_artifact_card=true` 启动 | metrics.jsonl 写入路径存在 |
| 22-2 | 生成 PPT → 依次点 4 个按钮（open / show_in_folder / copy_path / save_as） | metrics.jsonl 末尾追加 4 条 `{"event":"artifact_action","action_id":"...","tool_name":"ppt_create","ok":true,"ts":"..."}` |
| 22-3 | grep `path` / `\\` 在 4 条事件里 | 0 命中（脱敏：不含路径） |
| 22-4 | 跑 10 次（5 次点开、5 次不点）| 统计点击率 = 50%，与日志一致 |

**验收**：4 步全过 + grep 输出截图。

---

### MR-23 — 卸载/重装后 user_data 迁移（v2 新增）

| 步骤 | 操作 | 期望 |
|---|---|---|
| 23-1 | 正常使用 1 小时累计 ≥ 5 个 session 的 receipts | `<user_data>/receipts/` 下有 5 个 jsonl |
| 23-2 | 用 uninstaller 卸载 DeskPet | 弹窗询问「是否保留 user_data？」；点保留 → `<user_data>/` 不删 |
| 23-3 | 重装 DeskPet（同版本或新版本） | 首次启动检测：HMAC keystore 可能因 ACL/profile 变化不可读 → 触发 D11 的 `hmac_key_unreadable` 流程 |
| 23-4 | 查 `<user_data>/receipts/archived/pre_reinstall/` | 旧 jsonl 整体迁移到此目录，附 `INVALID_SIG_REASON.txt`；新 receipts 写入主目录 |
| 23-5 | 点不保留卸载 → `<user_data>/` 整删 | 重装后 receipts 目录全新 |

**验收**：5 步全过 + Explorer 截图。

---

### MR-24 — Outcome verifier 缺 toolchain 时优雅 skip（v2 新增）

| 步骤 | 操作 | 期望 |
|---|---|---|
| 24-1 | 用户机器无 Node：mock `which npm` 返回 None；开 `run_build=true` | 触发 build verifier → `prepare()` 返回 `status=skipped, reason="missing_npm"`；**不阻 end_turn**；emit metric `verifier.skipped_due_to_missing_toolchain{tool="npm"}` |
| 24-2 | 同上但无 pytest | test verifier skip，reason="missing_pytest" |
| 24-3 | cwd 不在 git repo（删除 `.git/`） | git_diff verifier skip, reason="not_a_git_repo" |
| 24-4 | 全部 toolchain 缺失场景下连续 5 次会话 | 用户始终拿到 end_turn 完成，无 verifier error 反馈污染对话 |

**验收**：4 步全过 + metric 日志截图。

---

## §4 opus-4.7 子代理测试循环（验收阶段）

完成上面所有 MR 后，派 opus-4.7 子代理（架构师视角）执行：

1. **真测**（不是脚本回放）：按 §3 顺序逐条跑，**用 windows-mcp + Tauri DevTools + Explorer + PowerPoint** 真实操作。
2. **截图归档**：每条 MR 至少 1 张截图，存 `plans/2026-05-23-tool-last-mile-upgrade/manual-results-<run>/`。
3. **缺陷分级**：
   - **S0**：一票否决项不过（MR-0/MR-8/MR-19）→ 立即回报，停止后续测试。
   - **S1**：其他 MR 任意步失败 → 列入修复清单。
   - **S2**：UX 抱怨（不影响功能但难用）→ 列入 follow-up。
4. **测试报告**：写到 `MANUAL-TEST-REPORT-<run>.md`，含：
   - 通过 / 失败矩阵
   - S0/S1/S2 缺陷列表 + 复现步骤
   - 性能数（MR-17）
   - 推荐 ship/no-ship 结论

5. **迭代**：开发者按报告修复 → 子代理重测 → 直到所有 S0/S1 清零。

---

## §5 退出标准（Definition of Done）

- [ ] **MR-0 / MR-8 / MR-13 / MR-19 四个一票否决项**全过（v2 新增 MR-13）
- [ ] 其余 MR（含 MR-21~24）全部 S1 清零
- [ ] 子代理推荐 ship
- [ ] golden file 0 差异
- [ ] beta 100 灰度 30 天工单 ≤ 1
- [ ] PRD §5 度量全部达标（点击率埋点已验，见 MR-22）

---

## §6 工具准备 / 数据准备

| 资产 | 路径 | 用途 |
|---|---|---|
| 50 条 fake-claim fixture | `tests/fixtures/fake_claims_50.jsonl` | MR-8 8-4 |
| 性能基线脚本 | `scripts/perf_baseline_tool_last_mile.py` | MR-17 |
| receipt 验签 CLI | `scripts/verify_receipt.py` | MR-11 |
| 长会话压测脚本 | `scripts/longrun_tool_loop.py` | MR-18 |
| 跨平台 mac runner | 内部 mac mini，CI 标签 `tier2-mac` | MR-16 |
