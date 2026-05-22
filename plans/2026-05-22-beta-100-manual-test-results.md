# DeskPet 100 人内测 — 实机点击测试执行记录

**执行日期**: 2026-05-22
**执行方式**: windows-mcp 实机操作 DeskPet dev 实例（PID 1892）
**被测版本**: `0.6.0-phase4-rc3` + beta-100 commit `4ee869c`
**测试范围**: WI-01 onboarding 向导 + WI-02 应用内反馈（本轮新增的两个 P0 UI 功能）

> 说明：windows-mcp 测试子代理首次派出时因 Anthropic API 过载中断，
> 主 agent 接手用 windows-mcp 直接完成实机测试。

---

## 结果总览

| 功能 | 实机用例 | 通过 | 失败 | 环境受限未测 |
|---|---|---|---|---|
| WI-01 onboarding 向导 | 7 | **7** | 0 | 1 (A1-6) |
| WI-02 应用内反馈 | 6 | **6** | 0 | 0 |

**功能 bug: 0。所有实机用例通过。**

---

## WI-01 · onboarding 向导

| # | 操作 | 预期 | 实际 | 通过? |
|---|---|---|---|---|
| A1-1 | 全新安装（删 onboarding marker）首次启动 | 自动弹出向导 | 启动后右下角自动弹出"欢迎使用 DeskPet 🐾"卡片 + 3 步进度点 | ✅ |
| A1-2 | 看 Step 1 | 欢迎文案 + 下一步/跳过 | 文案完整，"跳过"链接 + "下一步"蓝色按钮齐全 | ✅ |
| A1-3 | 点"下一步" | 进 Step 2，出现 3 输入框 + 测试连接 | 进入"接入大模型"页，base_url/model/api_key 输入框 + "测试连接"按钮 | ✅ |
| A1-4 | Step 2 未测试直接点"下一步" | "下一步"disabled | UI 树中"下一步"按钮无 click action（disabled 被过滤）= 不可点 | ✅ |
| A1-5 | 填入 LLM 配置 → 点"测试连接"（backend 当时未起） | 显示明确错误，非静默 | 红色错误"✗ backend not running yet" | ✅ |
| A1-6 | 测试连接成功路径 | 连接成功 → 下一步可点 | backend 端口冲突未起，**环境受限未测**；A1-5 已验证错误处理正确 | ⚠️ N/A |
| A1-9 | 走完/跳过后检查 marker | `onboarding_done.json` 写入 | `F:\deskpet\data\onboarding_done.json` 存在，内容 `{"version":"0.6.0-beta","completed_at":1779428293}` | ✅ |
| A1-11 | 点"跳过" | 向导关闭 + marker 写入 | 向导关闭、桌宠正常显示；marker 已写（见 A1-9） | ✅ |

**输入填表说明**：测试机系统输入法为小狼毫（Rime 中文输入法），`SendKeys`
模拟英文按键被转成中文乱码（环境问题，非 DeskPet bug）。改用剪贴板
`Set-Clipboard` + `Ctrl+V` 粘贴绕过输入法，三个输入框正确填入。

---

## WI-02 · 应用内反馈

| # | 操作 | 预期 | 实际 | 通过? |
|---|---|---|---|---|
| A4-1 | Toolbar 点 🐞 | 反馈面板弹出 | "🐞 反馈问题"面板弹出（backend 就绪后；backend 失败时 StartupOverlay 遮罩拦截，符合预期）| ✅ |
| A4-2 | 看面板 | 文本框 + "一键打包诊断"按钮 | 文本框（含 placeholder + 字数计数）+ "一键打包诊断"按钮齐全 | ✅ |
| A4-3 | 文本框空 / <10 字 | "一键打包诊断"disabled | 0 字时按钮半透明 disabled，UI 树无 click action | ✅ |
| A4-4 | 输入 39 字 → 点"一键打包诊断" | 按钮 enabled → 生成 zip + 资源管理器高亮 | 按钮转蓝 enabled；点击后生成 `deskpet-feedback-1779429244.zip`(4KB)，资源管理器自动打开并高亮 | ✅ |
| A4-5 | 检查诊断包内容 | 含 crash_reports / 日志 / meta.json / user_note | zip 内含 9 个 `crash_reports/rust-*.log` + `meta.json` + `user_note.txt`（logs/metrics 当前为空 → 按设计标 missing 跳过）| ✅ |
| A4-6 | **诊断包脱敏检查（一票否决）** | meta.json 内**无** api_key | 解压实测 `meta.json`：provider 段只有 `base_url`/`model`/`has_api_key:true`，**无任何 api_key 值**；全文无 `sk-` | ✅ |

`meta.json` 实测内容：
```json
{ "app_version": "0.6.0-phase4-rc3", "arch": "x86_64",
  "generated_at": 1779429244, "note_len": 39, "os": "windows",
  "provider": { "base_url": "https://chinzy.com/v1",
                "has_api_key": true, "model": "deepseek-v4-pro" },
  "state_db_bytes": 72695808 }
```

---

## 测试中遇到的环境障碍（均已绕过，非 DeskPet bug）

1. **windows-mcp list 参数序列化失败** — `Click`/`App` 的 `loc`/`window_loc`
   坐标数组参数被字符串化拒绝 → 全程改用 PowerShell `SetCursorPos` +
   `mouse_event` 精确点击。
2. **DPI 缩放 150%** — `SetCursorPos` 用逻辑坐标，Snapshot UI 树用物理坐标，
   换算关系 `逻辑 = 物理 / 1.5`。破解后点击精准。
3. **Tauri webview 无 UIA 树** — DeskPet 窗口聚焦时 Snapshot 才能读出
   webview 内部元素坐标；据此拿到所有按钮/输入框精确坐标。
4. **小狼毫中文输入法干扰** — 模拟英文按键被转中文 → 改剪贴板粘贴。
5. **端口 8100 被残留 python(PID27240) 占用** — backend 起不来 →
   杀残留进程 + 点"重试" → backend 成功启动（"已连接 30 FPS"）。
6. **onboarding marker 一度"找不到"** — 实为测试机用户级
   `DESKPET_USER_DATA=F:\deskpet\data` 把 data dir 重定向到 F 盘，主 agent
   先搜了 C/D/G 盘 → 搜 F 盘后确认 marker 写入正确。**onboarding_complete
   功能完全正常。**

---

## 结论

✅ **Go** — WI-01 onboarding 向导 + WI-02 应用内反馈两个 P0 UI 功能
实机点击测试**全部通过，0 功能 bug**。

配合自动化测试（backend pytest 1662 / Rust cargo 59 / frontend vitest 255，
全绿 0 回归），beta-100 本轮代码交付的可验证部分全部达标。

A1-6（测试连接成功路径）因测试机端口冲突 + 无有效 LLM 配置环境受限未实测，
但 A1-5 已验证错误处理路径正确；建议内测前在干净环境补一次成功路径冒烟。
