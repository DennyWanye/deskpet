# DeskPet 内测发布流程 SOP（WI-08）

**日期**: 2026-05-22
**适用范围**: `0.6.0-beta.N` 系列内测构建的打包、签名、灰度、回滚

> 本文是「发布负责人」执行内测发布的逐步操作手册。面向更底层的签名密钥管理 /
> 历史背景见 [`docs/RELEASE.md`](docs/RELEASE.md)（旧 packaging 文档）；
> 代码签名集成见 [`docs/signing.md`](docs/signing.md)。

---

## 1. 版本号规范

内测版本号统一格式：

```
0.6.0-beta.N
```

- `0.6.0` 是当前主版本基线，内测期内不变。
- `N` 是内测构建序号，**每出一个新内测包就 +1**：`beta.1` → `beta.2` → `beta.3` …
- git tag 形如 `v0.6.0-beta.1`（带 `v` 前缀）。
- 三处版本号必须一致：
  - `tauri-app/src-tauri/tauri.conf.json` 的 `version`
  - git tag
  - `latest.json` 的 `version` 字段

> 当前 `tauri.conf.json` 的 `version` 是 `0.6.0-phase4-rc3`——首个内测构建前
> 必须先把它改成 `0.6.0-beta.1`。

---

## 2. 发布前检查清单

打包前确认：

- [ ] `0.6.0-beta-readiness` 计划的 Go/No-Go checklist 全绿
      （见 `plans/archive/`（已归档）下相关 readiness checklist；
      新版本可参考最近的 `plans/2026-05-XX-*-readiness.md`）
- [ ] backend 测试全绿：`cd backend && pytest tests/ -q`
- [ ] frontend 测试全绿：`cd tauri-app && npm test`
- [ ] `tauri.conf.json` 的 `version` 已更新为目标 `0.6.0-beta.N`
- [ ] feature flag 默认态符合预期（见 `docs/beta-feature-flags.md`）
- [ ] 内测三件套文档（内测协议 / 隐私说明 / 已知问题）已随包准备好

---

## 3. 构建 MSI

内测版用 `scripts/build-msi.ps1` 构建（已封装了 WiX multi-cab 补丁、
`CARGO_TARGET_DIR` 重定向等踩坑修复）：

```powershell
# 在仓库根目录执行
pwsh scripts\build-msi.ps1
```

构建做了几件事：

1. `tauri build`（前端 `npm run build` → Rust 编译 → WiX 打包）。
2. 由于 dist 体积大，脚本会 hot-patch WiX 的 `.wxs`，把单 cab 拆成
   multi-cab，绕过 `light.exe` 的 2 GiB 单 cab 上限。
3. 因为 `tauri.conf.json` 里 `bundle.createUpdaterArtifacts = true`，
   构建会**额外产出 updater 工件**——即带签名的安装包 + `.sig` 签名文件。

### 代码签名

若已拿到代码签名证书，构建时一并签名（参数见 `docs/signing.md` §5）：

```powershell
pwsh scripts\build-msi.ps1 -CertThumbprint $env:DESKPET_CERT_THUMBPRINT
```

若内测前还没拿到证书 → 发未签名 MSI，并确保《已知问题》文档里有 SmartScreen
引导（见 `docs/signing.md` §7 降级方案）。

### 产物

构建产物在 `tauri-app/src-tauri/target/release/bundle/` 下（若设了
`CARGO_TARGET_DIR` 则在对应目录）：

```
bundle/msi/DeskPet_0.6.0-beta.1_x64_en-US.msi        # 安装包
bundle/msi/DeskPet_0.6.0-beta.1_x64_en-US.msi.sig    # updater 用的 minisign 签名
```

---

## 4. 生成并签名 `latest.json`

Tauri updater 靠一个叫 `latest.json` 的清单文件判断「有没有新版」。

### updater 配置（来自 `tauri.conf.json`，确认无误）

```jsonc
"plugins": {
  "updater": {
    "active": true,
    "endpoints": [
      "https://github.com/DennyWanye/deskpet/releases/latest/download/latest.json"
    ],
    "dialog": true,
    "pubkey": "dW50cnVzdGVkIGNvbW1lbnQ6IG1pbmlzaWduIHB1YmxpYyBrZXk6IDVGNjIzRTVDREJBQTRDNUEK..."
  }
}
```

- **endpoint**：固定指向 GitHub 最新 Release 里的 `latest.json`。
- **pubkey**：base64 编码的 minisign 公钥（key ID `5F623E5CDBAA4C5A`）。
  客户端用它校验更新包的签名。公钥可以安全地嵌在客户端里；**对应的私钥
  绝不能进 git**（私钥管理见 `docs/RELEASE.md`）。

### `latest.json` 结构

```json
{
  "version": "0.6.0-beta.2",
  "notes": "本次更新内容摘要……",
  "pub_date": "2026-05-22T12:00:00Z",
  "platforms": {
    "windows-x86_64": {
      "signature": "<DeskPet_0.6.0-beta.2_x64_en-US.msi.sig 文件的内容>",
      "url": "https://github.com/DennyWanye/deskpet/releases/download/v0.6.0-beta.2/DeskPet_0.6.0-beta.2_x64_en-US.msi"
    }
  }
}
```

字段说明：

- `version`：必须和 `tauri.conf.json` / git tag 一致；客户端比它和自己的
  版本号决定要不要更新。
- `signature`：**直接粘贴 `.msi.sig` 文件的全部内容**（minisign 签名文本）。
- `url`：该版本 MSI 在 GitHub Release 里的直链下载地址。

### minisign 签名步骤

`.sig` 文件由构建过程（`createUpdaterArtifacts`）自动生成，前提是签名私钥
已注入环境：

```powershell
# 把 updater 签名私钥导入环境（构建前执行）
$env:TAURI_SIGNING_PRIVATE_KEY = Get-Content $env:USERPROFILE\.tauri\deskpet.key -Raw
$env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD = "<私钥口令>"
# 然后再跑 build-msi.ps1 —— 产物里会带 .msi.sig
```

把 `.msi.sig` 的内容填进 `latest.json` 的 `signature` 字段即可。
**不要手动改 `.sig` 内容**——任何改动都会让客户端校验失败。

---

## 5. 发布到 GitHub Release

```powershell
# 1. 打 tag（确认 tauri.conf.json version 已同步）
git tag v0.6.0-beta.1
git push origin v0.6.0-beta.1

# 2. 创建 GitHub Release（也可在网页上操作）
gh release create v0.6.0-beta.1 `
    --title "DeskPet 0.6.0-beta.1" `
    --notes "首个 100 人内测构建" `
    --prerelease `
    "path\to\DeskPet_0.6.0-beta.1_x64_en-US.msi" `
    "path\to\latest.json"
```

要点：

- **务必勾选 `--prerelease`**（内测版不是正式版）。
- 上传到 Release 的资产**至少要有**：MSI 安装包 + `latest.json`。
- updater 的 endpoint 指向 `releases/latest/download/latest.json`——
  `latest` 解析为「最新一个 Release」，所以新内测包发出后，旧版客户端
  下次启动就能检测到。

> 若仓库已配 `.github/workflows/release.yml`，push tag 即触发 CI 自动
> 打包+签名+上传，无需手动 `gh release create`。手动流程是 CI 不可用时的
> 兜底。

---

## 6. 灰度放量节奏

**不要一次性发给 100 人。** 按以下节奏分批：

```
阶段 1  ── 5 人灰度 ──────────────────────────────
   对象：团队成员 + 2-3 个早期信任用户
   目标：抓最致命的「装不上 / 起不来 / 立刻崩」级问题
   动作：发 MSI + 内测三件套文档；引导他们当天就装上试用

阶段 2  ── 48 小时观察 ───────────────────────────
   观察：5 人有无 P0 崩溃、安装失败、数据丢失
   判定：48h 内无 P0 问题  →  进入阶段 3
         发现 P0 问题      →  停止放量，修复后出 beta.N+1 重走阶段 1

阶段 3  ── 100 人放量 ────────────────────────────
   对象：完整内测名单
   动作：群里发安装包 + 文档 + 安装引导（含 SmartScreen 步骤）
```

每个阶段都要确认收到反馈渠道（内测群）畅通。

---

## 7. 回滚预案

内测期一定会发 `beta.2`、`beta.3`……一旦某个新版出严重问题，要能快速止血。

### 情况 A：新版有严重问题，让用户退回上一版

由于 Tauri updater 只会「向前更新」（检测到更高版本才更新），**没有自动降级**。
退回上一版要手动：

1. **立刻在内测群公告**：暂停安装/更新到 `beta.N`，说明问题。
2. 把出问题的 GitHub Release 的 `latest.json` **改回指向上一个好版本**
   `beta.N-1`（编辑 Release 资产里的 `latest.json`，把 `version` / `url` /
   `signature` 都换成 beta.N-1 的）。这样还没更新的客户端不会再被推上坏版本。
3. 已经更新到坏版本的用户：让他们手动下载 `beta.N-1` 的 MSI 覆盖安装。
   - 因为数据存在独立的 `userdata/` 目录（便携模式），覆盖安装**不会丢对话历史**。
4. 标记坏 Release 为 draft 或在 notes 里大字写明「已知严重问题，请勿安装」。

### 情况 B：紧急 hotfix

问题能快速定位修复时，优先走 hotfix 而非降级：

1. 在出问题的 commit 基础上修复。
2. 版本号直接 +1（`beta.N` → `beta.N+1`），**不要复用已发过的版本号**。
3. 走第 3-5 节正常流程构建 + 发布。
4. 群里公告：发现了 X 问题，`beta.N+1` 已修复，请更新。
5. hotfix 也要先过 5 人灰度（哪怕缩短观察窗到几小时），别盲发。

### 回滚铁律

- **永远不要复用版本号**。即使是回退，也是发一个新的更高版本号。
- **数据兼容性**：升级和回退都不能丢用户的 `state.db` / 配置。便携模式的
  `userdata/` 独立于程序文件，覆盖安装天然不动它——但每次发版前仍要在
  「老用户升级」路径上实测一次数据不丢。
- 任何回滚/hotfix 操作完成后，在内测群同步结论，避免用户困惑。

---

## 附录 A — Fork 用户的 release 指南（OSS）

如果你 fork 了 DeskPet 想发自己的 build，**不能直接复用本仓的签名密钥和
更新通道**。Tauri updater 用 minisign 验签，私钥泄露 = fork 用户的安装
可被仿冒推送恶意更新。正确做法：

### 1. 生成你自己的 minisign keypair

```bash
# 一次性，在你自己的开发机上跑（不是 CI）
npx @tauri-apps/cli signer generate --ci --password "" \
    -w $HOME/.tauri/yourname.key
```

输出会打印**公钥**（base64），私钥写到 `~/.tauri/yourname.key`。

### 2. 改 `tauri-app/src-tauri/tauri.conf.json`

```jsonc
"plugins": {
  "updater": {
    "active": true,
    "endpoints": [
      // ⚠️ 必改：指向 YOUR fork 的 releases，不是上游
      "https://github.com/<your-name>/deskpet/releases/latest/download/latest.json"
    ],
    "dialog": true,
    // ⚠️ 必改：上面命令打印的公钥（base64），不是上游的
    "pubkey": "<your-public-key-base64>"
  }
}
```

### 3. 把私钥加到 fork 的 repo secrets

GitHub → Settings → Secrets and variables → Actions → New repository secret：

- `TAURI_SIGNING_PRIVATE_KEY` — 粘贴 `~/.tauri/yourname.key` 文件内容
- `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` —（可选）如果生成时设了 passphrase

### 4. 改 `.github/workflows/release.yml` 里仓库 URL（如必要）

`release.yml` 当前用 `${{ github.repository }}` 自动取仓库名，所以 fork
后不用改这个变量。`latest.json` 生成时也会自动用你的 fork URL。

### 5. tag 触发发布

```bash
git tag v0.1.0
git push origin v0.1.0
```

workflow 自动跑，~10 分钟后你的 fork 会出现一个 GitHub Release，里面有
NSIS .exe + MSI + `.sig` + `latest.json`。

### 6. **不要做的事**

- ❌ 复用上游的 `pubkey`（你的 build 没对应私钥，updater 验签会失败）
- ❌ 用上游的 endpoint URL（你的用户会被推到上游的更新，可能引发版本错乱）
- ❌ 把私钥提交到 git（即使只是 `~/.tauri/`，也确认它在你的全局 gitignore 里）
- ❌ 不签名直接发（updater 会拒绝未签名的 `latest.json`，老用户升级失败）

如果你只想构建一次性 binary 不打算用 updater：把 `tauri.conf.json` 里
`plugins.updater.active = false`，然后用 `scripts/release.ps1 -NoSign` 本地构建。
