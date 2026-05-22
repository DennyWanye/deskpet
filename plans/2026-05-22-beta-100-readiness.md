# DeskPet 100 人内测就绪计划 — PRD + 技术设计 + TDD

**日期**: 2026-05-22
**版本基线**: `0.6.0-phase4-rc3`
**目标**: 把 DeskPet 从"功能可用"推到"可以放给 100 个真实用户连续使用 2-4 周"
**配套文档**: 人工点击测试脚本见 [`2026-05-22-beta-100-manual-test.md`](./2026-05-22-beta-100-manual-test.md)

---

## 0. 现状核实（2026-05-22 实测代码库）

开内测前我逐一核对了基础设施，**修正了 5-07 known-issues 文档里的过时判断**：

### ✅ 已具备（不用再做）

| 能力 | 证据 | 说明 |
|---|---|---|
| 配置保存（release 模式）| `tauri-app/src/bindings/config.ts` 已改走 `invoke("update_cloud_config")` | known-issue #1 的 mixed-content 阻塞**已修**——前端不再直接 fetch http |
| Rust 崩溃落盘 | `src-tauri/src/crash_reports.rs` `install_panic_hook()` | Tauri 线程 panic → `crash_reports/<ts>.txt` |
| Python backend 崩溃落盘 | `main.py:75 install_crash_reporter()` | backend 未捕获异常也落盘 |
| 自动更新基础设施 | `tauri.conf.json` `updater.active=true` + GitHub endpoint + `pubkey` 已配 | 框架就位，但**端到端没验证过**（见 WI-09）|
| 启动遮罩 + 失败兜底 | `src/components/StartupOverlay.tsx` | 启动中 spinner；失败给「重试 / 打开日志 / 退出」|
| 干净退出按钮 | `Toolbar.tsx` `onExit` ⏻ 按钮 | known-issue #7 已修 |
| 每日成本账本 | `billing_ledger` 服务 + `BillingConfig` | 数据层有，UI 提示待确认（见 WI-04）|

### ❌ 仍缺 / 待完善（本计划要解决）

| 缺口 | 影响 |
|---|---|
| 首次启动**没有配置向导** | 用户装完面对一个不会说话的桌宠，不知道要去设置里填 API key / 等模型下载 |
| 崩溃报告**落了盘但发不出来** | `crash_reports/` 有文件，但没有应用内"反馈/发送"入口——等于 100 人崩溃你也收不到 |
| MSI **未做代码签名** | SmartScreen 红框「未识别的应用程序」劝退非技术用户 |
| 成本超额**提示链路未验证** | 用户用自己云 key，烧超预算如果静默失败会引发投诉 |
| 没有**内测协议 / 隐私说明 / 用户版已知问题** | 合规 + 预期管理缺失 |
| 卸载**是否清理 `%AppData%\deskpet`** 未确认 | 残留数据 / 重装冲突 |
| updater **没跑通过一次真实升级** | 配了 ≠ 能用；100 人内测必然要发 v2 |
| 新功能 feature-flag 默认态**没有审计** | memory-v2 / ppt / deep-research 是否按预期 OFF 未系统检查 |
| 没有**最小可观测** | 内测结束无法回答"启动成功率多少 / LLM 调用成功率多少" |

### 重新分级后的工作项总览

```
P0 阻塞（不做内测无法进行）   WI-01 onboarding 向导
                              WI-02 应用内反馈入口
                              WI-03 代码签名 + SmartScreen

P1 强烈建议（影响内测质量）   WI-04 成本护栏可见化
                              WI-05 release 进程生命周期验证
                              WI-06 内测协议 / 隐私 / 已知问题清单
                              WI-07 卸载清理
                              WI-08 内测构建与灰度流程

P2 加分（可内测中迭代）        WI-09 updater 端到端验证
                              WI-10 安装包瘦身 / 模型按需下载
                              WI-11 feature-flag 默认态审计
                              WI-12 最小可观测埋点
```

---

# 第一部分 · PRD（产品需求文档）

每个工作项格式：**用户故事 → 验收标准 → 范围边界**。

---

## WI-01 · 首次启动 onboarding 向导　【P0】

**用户故事**
> 作为一个刚装好 DeskPet 的非技术用户，我希望应用第一次启动时手把手告诉我"接下来要做什么"，而不是丢给我一个不会回应的桌宠。

**验收标准**
1. 全新安装（`%AppData%\deskpet\` 不存在）首次启动 → 自动弹出 onboarding 向导。
2. 向导含 3 步，每步可「下一步 / 跳过」：
   - **Step 1 欢迎**：一句话介绍 + 桌宠能做什么。
   - **Step 2 接入大模型**：填 `base_url` / `model` / `api_key`，带「测试连接」按钮（实际打一次 LLM，成功才允许「下一步」，失败给明确错误）。
   - **Step 3 本地能力**：说明 BGE-M3 记忆模型在后台下载（286MB），下载完成前记忆功能降级 mock——告知而非阻塞。
3. 走完向导写一个 `%AppData%\deskpet\onboarding_done.json`（含版本号 + 完成时间），后续启动不再弹。
4. 向导任意一步「跳过」也写标记，但桌宠首条气泡提示"随时可在设置里配置大模型"。
5. 已是老用户（`onboarding_done.json` 存在）→ 绝不弹窗（zero regression）。

**范围边界**
- 不做账号系统、不做云同步。
- 不做多语言——内测先中文。
- Step 2 的"测试连接"复用现有 `update_cloud_config` IPC 的 backend 探测逻辑，不新写探测器。

---

## WI-02 · 应用内反馈入口　【P0】

**用户故事**
> 作为内测用户，我遇到 bug 时希望点一个按钮就能把问题连同日志发出去，而不是去翻 `%AppData%` 找文件再传微信群。

**验收标准**
1. Toolbar 增加一个「反馈」图标按钮（💬 或 🐞）。
2. 点击弹出反馈面板：
   - 文本框：描述问题（必填，≥ 10 字）。
   - 复选框：「附带诊断包」（默认勾选）。
   - 「一键打包诊断」按钮。
3. 诊断包 = 一个 zip，含：
   - `crash_reports/` 全部文件
   - `%AppData%\deskpet\logs\` 最近 3 个日志文件
   - 一个 `meta.json`：app 版本、OS 版本、`state.db` 体积、内存占用、当前 LLM provider（**脱敏：不含 api_key**）。
4. 打包完成 → 在系统文件管理器里高亮该 zip，并把路径复制到剪贴板。
5. 提供一个固定收集渠道（内测群文件 / 邮箱 / GitHub Issue 模板链接），面板里直接展示。
6. 诊断包 **绝不包含** `api_key`、`llm_runtime.json` 的密钥字段、OS 凭据库内容。

**范围边界**
- 不做自动上传（避免做服务端）；用户手动把 zip 发到渠道。
- 不做截图标注。

---

## WI-03 · 代码签名 + SmartScreen　【P0】

**用户故事**
> 作为内测用户，我双击安装包时不希望看到刺眼的「Windows 已保护你的电脑 / 未识别的应用程序」红框，让我怀疑这是病毒。

**验收标准**
1. MSI 与主 exe 都用一张代码签名证书签名（OV 或 EV）。
2. 签名后用 `signtool verify /pa` 验证通过。
3. 安装时 SmartScreen 不再出现「未识别的发布者」——发布者显示为证书主体名。
4. `tauri.conf.json` / `build-msi.ps1` 集成签名步骤，CI 可复现。
5. 如内测期暂时拿不到 EV 证书（OV 证书需积累 SmartScreen 信誉）→ 在内测说明里明确告知用户"会有一次警告，点『更多信息 → 仍要运行』"，并给截图。

**范围边界**
- 证书采购是**外部依赖**（需要公司主体 / 预算），本计划只负责"拿到证书后的集成 + 验证"。
- 如内测前确实拿不到证书 → 降级为"内测说明里的引导截图"，不阻塞，但记为风险。

---

## WI-04 · 成本护栏可见化　【P1】

**用户故事**
> 作为用自己云 API key 的内测用户，我希望在快烧光每日预算时被明确提醒，而不是某天发现对话静默失败、key 欠费。

**验收标准**
1. `billing_ledger` 当日累计达到 `daily_budget_cny` 的 **80%** → 桌宠气泡提示一次「今日预算快用完了」。
2. 达到 **100%** → 后续对话前置拦截，UI 明确提示「已达每日预算上限，明日重置 / 可在设置调高」，**不静默吞掉**。
3. 设置面板显示「今日已用 X.X / Y.Y 元」实时数字。
4. LLM 调用因 key 欠费 / 401 / 配额失败 → 错误信息透传到 UI，区分"你的 key 有问题"vs"DeskPet bug"。
5. 预算为 0 或未配置 → 不拦截（视为无限额），但仍显示累计用量。

**范围边界**
- 不做用量图表 / 历史曲线。
- 价格表沿用现有 `BillingConfig.pricing`，不重新设计计费。

---

## WI-05 · release 进程生命周期验证　【P1】

**用户故事**
> 作为内测用户，我关闭 DeskPet 后不希望有残留进程偷偷占内存 / 占麦克风，也不希望重启时报「端口被占用」。

**验收标准**
1. 点 ⏻ 退出 → `deskpet.exe`、Python backend、所有子进程在 5 秒内全部消失（任务管理器核对）。
2. 任务栏右键退出 / Alt+F4 / 关机重启 → 同样无残留。
3. 退出后立即重启 → 不报端口占用、不报 state.db 锁。
4. backend 崩溃 → Rust supervisor 能感知并走 StartupOverlay 失败态，不留僵尸。
5. 连续「启动→退出」10 次 → 无进程泄漏、无 `state.db.bak.*` 文件爆炸（5-21 已修 backup 保留策略，此处回归验证）。

**范围边界**
- 这是**验证 + 修缺陷**任务，不是新功能。`process_manager.rs` 已存在，重点是 release 模式实测。

---

## WI-06 · 内测协议 / 隐私说明 / 用户版已知问题　【P1】

**用户故事**
> 作为内测用户，我希望清楚知道：我的对话数据存在哪、会不会上传、这版有哪些已知毛病，免得我把"已知问题"当新 bug 反复报。

**验收标准**
1. 产出三份面向用户的文档（Markdown + 可转 PDF）：
   - **内测协议**：内测性质、保密、反馈义务、无 SLA 承诺。
   - **隐私说明**：`state.db` 存完整对话（本地）；api_key 存 Windows 凭据库；诊断包内容与脱敏说明；**是否有遥测**（取决于 WI-12 的决定，需明示）。
   - **用户版已知问题清单**：从 `2026-05-07-msi-known-issues.md` 提炼，去掉构建侧问题，只留用户可见项 + 规避方法。
2. onboarding Step 1 或 about 面板里有入口能看到这三份。
3. 隐私说明与代码实际行为**逐条核对一致**（不能写"不上传"但 WI-12 偷偷上传）。

**范围边界**
- 不做法务级合规审查——内测级别，措辞清楚诚实即可。

---

## WI-07 · 卸载清理　【P1】

**用户故事**
> 作为内测用户，我卸载 DeskPet 后希望它要么干净走人，要么明确告诉我"对话数据还留在 `%AppData%`，需要手动删"。

**验收标准**
1. 通过控制面板卸载 → 程序文件、开始菜单、自启动项全部移除。
2. 用户数据（`%AppData%\deskpet\`：state.db / 模型 / 配置）的处理策略二选一并落实：
   - 方案 A：卸载时弹「是否同时删除个人数据」复选框。
   - 方案 B：保留数据，但卸载完成页 / 文档明确写出残留路径。
3. 重装后能正确识别旧数据（旧 onboarding 标记、旧 state.db）并正常迁移 / 启动。
4. 自启动注册表项 / 计划任务在卸载后不残留。

**范围边界**
- WiX/NSIS 卸载脚本改动；不做"卸载问卷"。

---

## WI-08 · 内测构建与灰度流程　【P1】

**用户故事**
> 作为发布负责人，我希望有一条可复现的内测发布流水线：打包 → 签名 → 生成 `latest.json` → 小范围灰度 → 全量。

**验收标准**
1. 一份 `RELEASE.md`：从 tag 到 GitHub Release 的完整步骤（含签名、`createUpdaterArtifacts`、`latest.json` 上传）。
2. 内测版本号规范：`0.6.0-beta.N`，每次内测构建 N 递增。
3. 灰度：先发 5 人（团队 + 早期用户）→ 观察 48h → 再放 100 人。
4. 有一个"回滚预案"：发现严重问题时如何让用户退回上一版（或紧急 hotfix 流程）。
5. 内测用户清单 + 渠道（群 / 邮件）建好。

**范围边界**
- 不搭建独立 CI/CD 服务器——沿用现有 `build-msi.ps1` + GitHub Releases。

---

## WI-09 · updater 端到端验证　【P2】

**用户故事**
> 作为内测用户，我希望 DeskPet 有新版时能提示我一键更新，而不是让我重新下 5GB 安装包。

**验收标准**
1. 用 `0.6.0-beta.1` → `0.6.0-beta.2` 实跑一次完整升级：旧版检测到新版 → 提示 → 下载 → 替换 → 重启 → 版本号变化。
2. `latest.json` 的签名用 `tauri.conf.json` 里配的 minisign pubkey 能验过。
3. 更新失败（网络断 / 签名错）→ 优雅降级，不让应用变砖。
4. 升级**不丢用户数据**（state.db / 配置 / onboarding 标记保留）。
5. UI 有「检查更新」入口 + 「当前已是最新」反馈。

**范围边界**
- updater 框架已配，本项是**验证 + 补 UI 入口 + 补缺陷**。

---

## WI-10 · 安装包瘦身 / 模型按需下载　【P2】

**用户故事**
> 作为内测用户，我不想为了试用一个桌宠下载 5.4GB 安装包、还要额外腾 7GB C 盘空间。

**验收标准**
1. 评估把 BGE-M3 / Whisper / CosyVoice 等大模型从 MSI 内移出，改首启动按需下载。
2. 目标：MSI 体积 < 800MB（理想 < 500MB）。
3. 模型下载有进度条 + 断点续传 + 校验（sha256）。
4. 下载未完成时对应能力降级（记忆走 mock、TTS 走 edge-tts 在线），不阻塞启动。
5. known-issue #2「装机额外需 7GB 临时空间」一并缓解。

**范围边界**
- 这是**较大改动**，如内测排期紧 → 可降级为"内测说明里写清磁盘要求"，本项转入内测期迭代。
- 需要一个能放模型的下载源（GitHub Release assets / 对象存储）。

---

## WI-11 · feature-flag 默认态审计　【P2】

**用户故事**
> 作为发布负责人，我希望确认这两个月新加的 memory-v2 (Phase A-E)、ppt-generate、deep-research 在内测版里处于**预期的**开关状态，不会意外暴露半成品。

**验收标准**
1. 列一张表：每个新功能 → 默认 ON/OFF → 内测期望 ON/OFF → 实际配置文件值。
2. memory-v2 五个 flag（eval/facts/rerank/workspace/reflection）确认默认 OFF（Strangler-Fig 已保证，此处核对 + 写明）。
3. ppt-generate / deep-research 两个 skill：确认是否要在内测放出。若放出 → 走一遍人工冒烟；若不放 → 确认 SkillLoader 不加载或 task_type 不触发。
4. code-mode、supervisor 等既有高级功能的内测可见性也一并过一遍。
5. 产出 `docs/beta-feature-flags.md` 作为放行依据。

**范围边界**
- 纯审计 + 配置核对，无新代码（除非发现 flag 默认值错了）。

---

## WI-12 · 最小可观测埋点　【P2】

**用户故事**
> 作为发布负责人，内测结束时我希望能回答："启动成功率多少？LLM 调用成功率多少？哪个功能最常用？"

**验收标准**
1. 本地落一个 `%AppData%\deskpet\metrics.jsonl`，每行一个事件：
   - `app_start` / `app_start_failed`
   - `llm_call_ok` / `llm_call_failed`（带错误分类）
   - `skill_invoked`（skill 名）
   - `crash`（关联 crash_reports 文件名）
2. 事件**不含**任何对话内容、不含 api_key——只有计数与分类。
3. 反馈面板（WI-02）的诊断包**包含** `metrics.jsonl`（这样才回收得到）。
4. **隐私一致性**：WI-06 隐私说明里如实写"本地记录匿名使用计数，仅在你主动发反馈包时才会传出"。
5. 若决定**完全不做遥测** → 也是合法选择，但 WI-06 文档要相应写"无任何使用数据收集"。

**范围边界**
- 不搭遥测服务端——数据只在本地，靠 WI-02 诊断包被动回收。
- 不做实时上报。

---

# 第二部分 · 技术设计文档

---

## WI-01 技术设计 · onboarding 向导

**涉及文件**
- 新增 `tauri-app/src/components/OnboardingWizard.tsx`
- 新增 `tauri-app/src/components/OnboardingWizard.test.tsx`
- 改 `tauri-app/src/App.tsx`：启动后判断是否首次 → 条件渲染向导
- 新增 Rust IPC `onboarding_status` / `onboarding_complete`（`src-tauri/src/commands.rs`）
- 标记文件 `%AppData%\deskpet\onboarding_done.json`，由 Rust `user_data.rs` 读写

**数据流**
```
App mount
  → invoke("onboarding_status")  [Rust 读 onboarding_done.json]
  → 不存在 → 渲染 <OnboardingWizard/>
       Step2「测试连接」→ invoke("update_cloud_config", {update, dry_run:true})
                          [复用现有 backend 探测，加 dry_run 参数只测不存]
  → 走完 → invoke("onboarding_complete", {version})  [写标记文件]
  → 卸载向导，进入正常桌宠
```

**关键决策**
- 标记文件放 `%AppData%` 而非 state.db：onboarding 在 backend / DB 起来之前就要判断。
- `update_cloud_config` 加可选 `dry_run` 字段——`dry_run:true` 时 backend 只发一次探测请求不落盘，复用现成探测逻辑。
- 向导是纯前端组件，失败路径全部 inline error，不弹系统 dialog（与 StartupOverlay 同理由）。

**降级**
- `onboarding_status` IPC 失败 → 保守当作"老用户"不弹窗（宁可漏弹不可重复弹）。
- backend 还没起来时 Step2「测试连接」按钮 disabled + 提示"等待服务启动"。

---

## WI-02 技术设计 · 应用内反馈入口

**涉及文件**
- 新增 `tauri-app/src/components/FeedbackPanel.tsx` + `.test.tsx`
- 改 `tauri-app/src/components/Toolbar.tsx`：加按钮 + `onFeedback` prop
- 新增 Rust IPC `build_diagnostic_bundle`（`commands.rs`）
- 新增 `src-tauri/src/diagnostics.rs`：收集 + zip + 脱敏

**诊断包构建（Rust 侧，避免前端碰文件系统）**
```
build_diagnostic_bundle(user_note: String) -> { zip_path: String }
  1. 解析 %AppData%\deskpet\
  2. 收集 crash_reports/*  +  logs/ 最近 3 个  +  metrics.jsonl
  3. 生成 meta.json：app_version / os / state.db 体积 / RAM / provider 名
     —— provider 配置读出后 **删掉 api_key 字段再写入**
  4. zip 到 %TEMP%\deskpet-feedback-<ts>.zip
  5. 调系统「在资源管理器中显示」+ 写剪贴板
```

**脱敏清单（硬编码黑名单，单元测试覆盖）**
- `api_key` / `apiKey` / `X-Shared-Secret` / `password` / `token` / `secret`
- `llm_runtime.json` 整个文件**不入包**（只入脱敏后的 provider 名 + base_url）
- OS 凭据库内容永不读取

**降级**
- 某个目录不存在 → 跳过该项，meta.json 标 `"logs": "missing"`，不中断打包。
- zip 失败 → 返回错误 + 提示用户手动去 `crash_reports/` 取文件。

---

## WI-03 技术设计 · 代码签名

**涉及文件**
- 改 `scripts/build-msi.ps1`：MSI 产出后插入 `signtool sign`
- 改 `tauri.conf.json`：`bundle.windows.certificateThumbprint` 或走 `signCommand`
- 新增 `docs/signing.md`：证书安装、thumbprint 获取、CI secret 注入

**流程**
```
build-msi.ps1
  → tauri build  (产出未签名 MSI + exe)
  → signtool sign /fd SHA256 /tr <时间戳服务器> /td SHA256 \
       /sha1 <证书thumbprint>  deskpet.exe  deskpet.msi
  → signtool verify /pa /v deskpet.msi   (CI 断言)
```

**关键决策**
- 证书私钥**不进 git**——本地走证书存储 thumbprint，CI 走 secret + 临时导入。
- 加时间戳服务器（`/tr`）：证书过期后已签名的包仍有效。

**风险**
- EV 证书贵 + 需公司主体；OV 证书便宜但 SmartScreen 信誉要积累。
- **Fallback**：内测前拿不到 → WI-06 文档加「首次运行警告」引导截图，本项标记为"内测期补"。

---

## WI-04 技术设计 · 成本护栏

**涉及文件**
- 改 `billing_ledger` 服务：加 `check_budget(estimated_cost) -> BudgetVerdict`
- 改 backend chat 链路：每次 LLM 调用前 `check_budget`
- 改前端：气泡提示组件 + 设置面板用量数字
- 新增 ws 事件 `budget_warning` / `budget_exceeded`

**判定逻辑**
```
每次对话前:
  used = ledger.today_total()
  budget = config.billing.daily_budget_cny
  if budget <= 0:           # 未配额
      allow, 仅推送 used 数字
  elif used >= budget:      # 超额
      block, 推 budget_exceeded
  elif used >= budget*0.8 且 今天没提醒过:
      allow, 推 budget_warning（每天一次）
  else:
      allow
```

**关键决策**
- 拦截发生在**调 LLM 之前**——避免"先花钱再说超额"。
- 80% 提醒每天去重（落一个 `last_warned_date`）。
- 401 / 欠费错误：复用现有 `error_classifier`，新增 `provider_billing` 分类，UI 文案区分。

---

## WI-05 技术设计 · 进程生命周期

**涉及文件**
- 审查 `src-tauri/src/process_manager.rs` / `backend_launch.rs`
- 新增 `scripts/e2e_process_lifecycle.ps1`：自动跑「启动→退出」×10 + 进程核对

**验证矩阵**
| 退出方式 | 预期 |
|---|---|
| ⏻ 按钮 | backend + 子进程 5s 内全退 |
| Alt+F4 / 关闭 | 同上 |
| 任务栏右键退出 | 同上 |
| 关机 / 注销 | Rust 收到 `WM_QUERYENDSESSION` → 优雅关 backend |
| backend 自身崩溃 | supervisor 感知 → StartupOverlay failed 态，无僵尸 |

**修复方向（若实测有残留）**
- Rust `Drop` / `on_window_event(CloseRequested)` 里确保 `process_manager.kill_all()`。
- backend 注册 `atexit` + 信号处理，子进程用 job object（Windows）绑定父进程生命周期。

---

## WI-09 技术设计 · updater 验证

**涉及文件**
- 新增 `src/components/UpdateChecker.tsx`（「检查更新」入口 + 状态）
- 验证 `tauri-plugin-updater` 已在 `Cargo.toml` + `lib.rs` 注册
- `RELEASE.md` 补 `latest.json` 生成 + minisign 签名步骤

**latest.json 结构**
```json
{
  "version": "0.6.0-beta.2",
  "notes": "...",
  "pub_date": "2026-...",
  "platforms": {
    "windows-x86_64": {
      "signature": "<minisign 签名>",
      "url": "https://github.com/.../deskpet_0.6.0-beta.2_x64.msi"
    }
  }
}
```

**验证步骤**：见人工测试脚本 §WI-09。

---

## WI-12 技术设计 · 最小可观测

**涉及文件**
- 新增 `backend/observability/metrics_sink.py`：append-only `metrics.jsonl` 写入器
- backend chat / startup / skill 链路打点
- WI-02 诊断包收集器把 `metrics.jsonl` 纳入

**事件 schema**（每行一个 JSON）
```json
{"ts": 1747900000.0, "event": "llm_call_failed", "detail": {"error_class": "timeout"}}
```
- 白名单字段：`ts` / `event` / `detail`（detail 只允许枚举值，**不允许自由文本**，杜绝对话内容泄漏）。
- 文件按大小轮转（> 2MB 截断保留尾部）。

---

# 第三部分 · TDD 计划

## 3.1 自动化测试（pytest + vitest）

> 原则：先写测试再写实现；feature-flag OFF 时行为字节不变；新测试不破坏现有 1635 backend + 242 frontend。

### WI-01 onboarding
| 测试 | 类型 | 断言 |
|---|---|---|
| `test_onboarding_status_fresh_install` | Rust/集成 | 无标记文件 → status=needs_onboarding |
| `test_onboarding_status_existing_user` | Rust | 有标记 → status=done |
| `test_onboarding_complete_writes_marker` | Rust | 调用后标记文件存在且含版本 |
| `OnboardingWizard renders 3 steps` | vitest | 三步可见、下一步/跳过可点 |
| `wizard step2 test-connection success gates next` | vitest | mock IPC 成功 → 下一步 enabled |
| `wizard step2 failure shows error keeps next disabled` | vitest | mock IPC 失败 → 错误可见 |
| `wizard skip still writes marker` | vitest | 跳过 → onboarding_complete 被调 |
| `existing user never sees wizard` | vitest | status=done → 组件不渲染 |

### WI-02 反馈
| 测试 | 类型 | 断言 |
|---|---|---|
| `test_diagnostic_bundle_excludes_api_key` | Rust | zip 内无任何 api_key / secret 字段 |
| `test_diagnostic_bundle_missing_dir_graceful` | Rust | logs 缺失 → meta 标 missing，不崩 |
| `test_diagnostic_bundle_includes_crash_and_metrics` | Rust | crash_reports + metrics.jsonl 入包 |
| `test_meta_json_redacts_provider` | Rust | provider 名在、key 不在 |
| `FeedbackPanel requires note >=10 chars` | vitest | 短文本 → 按钮 disabled |
| `FeedbackPanel shows bundle path after build` | vitest | mock IPC → 路径展示 + 复制 |

### WI-04 成本护栏
| 测试 | 类型 | 断言 |
|---|---|---|
| `test_check_budget_under_threshold_allows` | pytest | used<80% → allow，无事件 |
| `test_check_budget_80pct_warns_once_per_day` | pytest | 跨 80% → 一次 warning；同日再调不重复 |
| `test_check_budget_exceeded_blocks` | pytest | used>=budget → block |
| `test_check_budget_zero_budget_never_blocks` | pytest | budget=0 → 永远 allow |
| `test_billing_401_classified_as_provider_billing` | pytest | 401 → error_class=provider_billing |
| `budget_exceeded ws event renders block UI` | vitest | 事件 → 拦截提示可见 |

### WI-05 进程生命周期
| 测试 | 类型 | 断言 |
|---|---|---|
| `e2e_process_lifecycle.ps1` | 脚本 | 启动/退出 ×10，每轮后 0 残留 deskpet/python 进程 |
| `test_no_backup_file_explosion` | pytest | 10 轮后 `state.db.bak.*` ≤ MAX_BACKUPS |

### WI-09 updater
| 测试 | 类型 | 断言 |
|---|---|---|
| `test_latest_json_schema` | pytest | 生成的 latest.json 字段完整 |
| `test_latest_json_signature_verifies` | 脚本 | minisign verify 通过 |
| `UpdateChecker shows up-to-date state` | vitest | mock 无新版 → "已是最新" |

### WI-12 可观测
| 测试 | 类型 | 断言 |
|---|---|---|
| `test_metrics_sink_append` | pytest | 写事件 → jsonl 多一行 |
| `test_metrics_sink_no_freetext` | pytest | detail 含非枚举值 → 拒绝 / 丢弃 |
| `test_metrics_sink_rotation` | pytest | >2MB → 截断保尾部 |
| `test_metrics_no_conversation_content` | pytest | 任何事件都不含对话原文 |

### 回归门控
- 每个 WI 合并前：`cd backend && pytest tests/ -q`（≥1635 passed）+ `cd tauri-app && npm test`（≥242 passed）。
- onboarding / feedback 涉及 App.tsx → 重点跑 `sessionsStore.test.ts` 等既有前端用例零回归。

## 3.2 人工点击测试

完整逐步脚本独立成文件 → [`2026-05-22-beta-100-manual-test.md`](./2026-05-22-beta-100-manual-test.md)。

该文件特点：
- 每个 WI 一节，**逐步可勾选**（`- [ ]` 复选框）。
- 每步含「操作 / 预期结果 / 实际结果 / 通过?」四列。
- 覆盖**全新安装**与**老用户升级**两条路径。
- 末尾是 **go/no-go 放行 checklist**。
- 计划执行完毕后，由我（Claude）用 computer-use / windows-mcp 实机走一遍并填写结果 + 截图。

---

# 第四部分 · 执行排期

| 阶段 | 工作项 | 预估 | 出口标准 |
|---|---|---|---|
| **W1 P0** | WI-01 onboarding | 2-3 天 | 自动化测试绿 + 人工 §WI-01 过 |
| | WI-02 反馈入口 | 2 天 | 脱敏测试绿 + 诊断包人工核验 |
| | WI-03 签名 | 1 天（拿到证书后）| signtool verify 过 |
| **W1 灰度** | 5 人内部灰度 | 2 天观察 | 无 P0 崩溃 |
| **W2 P1** | WI-04 成本护栏 | 2 天 | 超额拦截人工验证 |
| | WI-05 进程验证 | 1 天 | ×10 循环 0 残留 |
| | WI-06 文档三件套 | 1 天 | 与代码逐条核对一致 |
| | WI-07 卸载清理 | 1 天 | 卸载 + 重装人工验证 |
| | WI-08 发布流程 | 1 天 | RELEASE.md 可复现 |
| **W2 放量** | 100 人内测开始 | — | go/no-go checklist 全绿 |
| **内测期** | WI-09/10/11/12 | 滚动 | 边测边补 |

**关键路径**：WI-03 签名依赖外部证书采购——**今天就要启动采购流程**，否则会卡住整条线。

---

# 第五部分 · 内测放行 Go / No-Go Checklist

放 100 人前，以下**全绿**才放行（详细人工步骤见 manual-test 文件）：

- [ ] 全新安装 → onboarding 向导正常走完，能配通 LLM
- [ ] 老用户升级 → 不弹 onboarding、数据不丢
- [ ] 反馈入口能生成诊断包，且**确认包内无 api_key**
- [ ] 崩溃（手动触发）→ crash_reports 落盘 → 能进诊断包
- [ ] MSI 已签名（或：内测说明含 SmartScreen 引导截图 + 风险已记录）
- [ ] 成本超额 → 明确拦截提示，不静默失败
- [ ] 启动/退出 ×10 → 0 残留进程、0 备份爆炸
- [ ] 卸载 → 干净 / 残留路径已明示
- [ ] 内测协议 + 隐私说明 + 已知问题清单 → 三份齐全且与代码一致
- [ ] feature-flag 审计表产出，新功能默认态符合预期
- [ ] updater：beta.1→beta.2 实测升级成功、数据不丢
- [ ] backend 1635+ / frontend 242+ 测试全绿，0 回归
- [ ] 人工点击测试脚本全部 `通过`

---

## 附:与历史文档的关系

- `2026-05-07-msi-known-issues.md`：本计划的 WI-06 会把它提炼成用户版；其中 #1/#7 已修，复核后从清单移除。
- `2026-05-21-memory-system-survey.md` / `2026-05-22-ppt-deepresearch-survey.md`：对应功能由 WI-11 审计其内测可见性。
- `MANUAL-TEST-PLAN-2026-05-20.md`：已有的人工测试计划，本计划的 manual-test 文件会复用其适用部分。
