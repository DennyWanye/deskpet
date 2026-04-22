# P3-S5 — Tauri bundle 吸纳冻结 backend（handoff）

**Branch:** `worktree-p3-s5-tauri-bundle-backend`
**Plan:** `docs/superpowers/plans/2026-04-22-p3s5-tauri-bundle-backend.md`
**Status:** 静态接线完成 + cargo test 18/18 绿；**UI 层 E2E 等用户本地跑**，跑完再 merge

---

## 这个 slice 干了什么

把 P3-S4 的 `backend/dist/deskpet-backend/` 接进 Tauri 的 bundle：

1. **`tauri.conf.json::bundle.resources`** 加 map：
   ```json
   "resources": {
     "../../backend/dist/deskpet-backend": "backend"
   }
   ```
   Tauri bundler 会把整个冻结产物（`deskpet-backend.exe` + `_internal/`
   下 2973 个文件）递归拷进 `resource_dir/backend/`。安装后布局：
   ```
   C:\Program Files\DeskPet\
   ├── deskpet.exe            (Tauri launcher)
   └── resources\backend\
       ├── deskpet-backend.exe   ← supervisor 认这条
       └── _internal\...
   ```

2. **`process_manager::start_backend` 多一行 stderr log**：
   ```
   [backend_launch] Bundled exe=C:\...\resources\backend\deskpet-backend.exe
   ```
   或
   ```
   [backend_launch] Dev python=G:\...\python.exe backend_dir=G:\...\backend
   ```
   给 e2e smoke / 故障排查抓分支的凭据。

3. **新增 `scripts/e2e_frozen_tauri.ps1`**：Tauri → 冻结 backend 端到端
   smoke。校前置 → 起 `npm run tauri dev` → 轮询 `/health` 90s → 断言
   → 从日志 grep Bundled 字样。后半段（UI 交互）交给用户。

## 为什么 UI E2E 留给用户本地跑

三个原因：

- **涉及麦克风** + **实时讲话 + 听 TTS**，自动化脚本没法验证"人听起
  来通顺"这层。用户 memory "Real Test" 要求 UI-level E2E + 截图留证，
  这类交互只能用户亲自走。
- **环境里有 30+ 个 node / 4 个 python 进程**（并行开发流的），脚本
  里 `Get-Process -Name deskpet, deskpet-backend` 是保守过滤——不碰
  那些可能是别的项目在用的 node。但 tauri dev 会自己起 node+vite+
  rustc，再叠一个 GUI 窗口，如果我主动 kill 会影响其他 worktree 在
  跑的东西。
- **tauri dev 首启要 copy 600+ MB / 2973 个文件到 target/debug/**
  （Tauri 2 每次 invoke 都会重跑 resource copy，不像 webpack 有增量
  机制）。让用户亲自感受一下首跑时长，心里有数。

## 用户：请按下面清单跑 UI E2E

### 前置条件 check

```powershell
# 1. 冻结 backend 产物要在（P3-S4 产出）
Test-Path G:\projects\deskpet\.claude\worktrees\p3-s5-tauri-bundle-backend\backend\dist\deskpet-backend\deskpet-backend.exe
# 期望 True

# 2. 模型目录要在
Test-Path G:\projects\deskpet\backend\models\faster-whisper-large-v3-turbo
# 期望 True

# 3. OS 钥匙串里应该已经有云 LLM API key（P3-S3 E2E 时存过）
```

### 跑起来

```powershell
cd G:\projects\deskpet\.claude\worktrees\p3-s5-tauri-bundle-backend
powershell scripts\e2e_frozen_tauri.ps1
```

脚本会起 tauri dev（后台）然后等 `/health`。首次启动预计 60–90 秒
（resources copy + Rust 编译 + PyInstaller 冷启动）。PASS 后 Tauri
窗口会开着，此时：

1. **看 Tauri 日志**（`.claude/tauri_dev.log`）里是否有
   `[backend_launch] Bundled exe=…` —— 这是 Bundled 分支被正确选中的
   直接证据。如果只看到 `Dev python=…`，说明 resource_dir 里没找到
   exe（resources copy 失败或 glob 没匹配），需要回头 debug。
2. **点麦讲话"你好"**，和 P3-S3 E2E 时一样走完 ASR + AI 回复 + TTS
   播放一整轮。
3. **截图** 保存到
   `docs/superpowers/handoffs/p3-s5-screenshots/`（这个目录 .gitignore
   掉就行，路径在 HANDOFF 里引用即可）。
4. **Ctrl+C** 关 tauri dev。脚本写的不是 daemon，你的终端前台会收到
   npm 的输出。

### 如果失败

- **`/health` 超时**：先看 `.claude/tauri_dev.log` 最后 50 行，通常是
  Python backend 起不来（模型文件缺失 / 端口被占 / DESKPET_MODEL_ROOT
  没传进去）。
- **Bundled 字样没出现**：去 `target/debug/resources/backend/` 看 exe
  是不是被拷过来了。没有的话检查 `tauri.conf.json::bundle.resources`
  语法是不是被 Tauri 吃进去了（`tauri info` 会打印当前 config）。
- **端口 8100 被占**：先 `Get-NetTCPConnection -LocalPort 8100` 找出
  占位的 pid 杀掉。

## 没做的（留给后面）

- **`tauri build` 出 installer** + 体积基线记录。`tauri build` 要十
  几分钟，而且 NSIS/MSI 产出还要 code-signing 配置确认，这层留给后续
  专门的 release slice（P3-S11）——本 slice 先把开发态跑通就够了。
- **dev 模式跳过 resources copy 的优化**（env var 短路 Priority 1）。
  如果用户发现 tauri dev 首跑 90+s 不能忍，我们再加一条优化路径；
  当前先观察真实数据。
- **Models 进 bundle**（P3-S6）。现在 E2E 靠 `DESKPET_MODEL_ROOT` env
  指向 `backend/models/`，这个是脚本自己设的，用户只要确保目录存在
  即可。P3-S6 会把模型也挂进 `resources`。

## Cargo / Python 测试

- cargo test --lib：**18/18 全绿**（P3-S3 的 backend_launch 测试无
  回归；本 slice 没加新测试，因为改动全在配置层和非测试代码路径
  上，单元级不好 cover）。
- pytest：无变化（本 slice 不动 backend Python 代码）。

## 下一步

等用户跑完 UI E2E + 贴截图 → merge `worktree-p3-s5-tauri-bundle-backend`
→ push → 开 P3-S6（models 进 bundle）。
