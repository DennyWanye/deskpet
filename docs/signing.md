# DeskPet Windows 代码签名指南（WI-03）

**日期**: 2026-05-22
**适用范围**: MSI 安装包 + `deskpet.exe` 主程序
**目标**: 让内测用户双击安装包时不再撞上「Windows 已保护你的电脑 / 未识别的发布者」红框

---

## 1. 为什么要签名

未签名的 MSI / exe 在 Windows 上会触发 **Microsoft Defender SmartScreen** 拦截，
弹出「Windows 已保护你的电脑 — 未识别的应用程序」。对非技术用户，这一屏几乎等于
「这是病毒」，是内测劝退率最高的环节之一。

代码签名做两件事：

1. **身份**：证明安装包确实由「证书主体名」（公司或个人）发布，安装时发布者
   一栏显示真实名字而非「未知发布者」。
2. **完整性**：签名后任何字节级篡改都会让 `signtool verify` 失败。

---

## 2. OV 证书 vs EV 证书

| 维度 | OV（Organization Validation） | EV（Extended Validation） |
|---|---|---|
| 价格（年） | ¥2000+ | ¥6000+ |
| 申请门槛 | 需公司主体，邮件/电话验证 | 需公司主体 + 更严格的法人核验 |
| 私钥载体 | 文件（.pfx）或 USB token（2023 后 CA 普遍要求硬件） | **强制 USB 硬件 token / HSM** |
| SmartScreen 信誉 | **从零积累**——签名后头几周仍可能弹警告，需累计一定下载量后信誉才转正 | **立即获得信誉**——签名后基本不再弹 SmartScreen |
| 适合场景 | 预算有限、能接受信誉积累期 | 要求首日零警告（推荐正式发布用） |

**结论**：内测阶段如预算紧张可先用 OV；但要知道 OV 签名后**头几周仍可能弹一次
SmartScreen**（信誉未建立），不能指望签了就万事大吉。EV 才是「签了即静默」。

> 证书采购是**外部依赖**（需要公司主体 + 预算 + 1-3 个工作日审核）。本指南只覆盖
> 「拿到证书之后的集成」。采购流程要尽早启动，否则会卡住整条发布线。

---

## 3. 获取证书 thumbprint（指纹）

签名时用 `/sha1 <thumbprint>` 指定证书。证书导入到「当前用户 — 个人」存储后，
用 PowerShell 取指纹：

```powershell
# 列出当前用户证书存储里所有代码签名证书
Get-ChildItem Cert:\CurrentUser\My -CodeSigningCert |
    Format-List Subject, Thumbprint, NotAfter

# 输出示例：
# Subject    : CN=Your Company Co., Ltd., O=Your Company Co., Ltd., C=CN
# Thumbprint : A1B2C3D4E5F6...（40 位十六进制）
# NotAfter   : 2027-05-22 ...
```

`Thumbprint` 就是要填进构建脚本的值（去掉空格，大小写不敏感）。

> EV 证书的私钥在硬件 token 上，signtool 仍用 thumbprint 定位；签名时 token 驱动
> 会弹出 PIN 输入框（无法完全无人值守，CI 上需特殊处理，见 §6）。

---

## 4. signtool 签名命令模板

```powershell
# /fd SHA256         —— 文件摘要算法用 SHA256（SHA1 已被弃用）
# /tr <时间戳服务器>  —— RFC3161 时间戳；证书过期后已签名的包仍有效
# /td SHA256         —— 时间戳摘要算法
# /sha1 <thumbprint> —— 用哪张证书签
# /v                 —— verbose，方便排错
signtool sign `
    /fd SHA256 `
    /tr http://timestamp.digicert.com `
    /td SHA256 `
    /sha1 A1B2C3D4E5F6... `
    /v `
    "DeskPet_0.6.0-beta.1_x64_en-US.msi"
```

**时间戳服务器**（任选其一，建议与证书 CA 一致）：

| CA | RFC3161 时间戳 URL |
|---|---|
| DigiCert | `http://timestamp.digicert.com` |
| Sectigo  | `http://timestamp.sectigo.com` |
| GlobalSign | `http://timestamp.globalsign.com/tsa/r6advanced1` |

**为什么必须加 `/tr`**：没有时间戳，证书一旦过期，所有用它签过的旧包会立刻
失去签名有效性；加了时间戳，签名时刻被「钉」住，证书过期不影响已发出的包。

### 验证签名

```powershell
# /pa 用「默认 Authenticode 策略」校验；CI 里把它当断言
signtool verify /pa /v "DeskPet_0.6.0-beta.1_x64_en-US.msi"
# 期望输出末尾：Successfully verified
```

主 exe（`deskpet.exe`）和 MSI **两个都要签**。建议顺序：先签 exe，再打 MSI，
最后签 MSI（MSI 内已包含已签名的 exe）。

---

## 5. 集成进 `scripts/build-msi.ps1`

在 `build-msi.ps1` 产出 MSI 之后插入签名步骤。建议把签名做成**可选 + 参数化**，
让本地无证书的 dev 构建仍能跑：

```powershell
# build-msi.ps1 顶部增加参数
param(
    [string]$CertThumbprint = $env:DESKPET_CERT_THUMBPRINT,
    [string]$TimestampUrl   = "http://timestamp.digicert.com",
    [switch]$NoSign
)

# ... tauri build / candle / light 产出 MSI ...

$msiPath = "<产出的 .msi 路径>"
$exePath = "<产出的 deskpet.exe 路径>"

if ($NoSign -or [string]::IsNullOrWhiteSpace($CertThumbprint)) {
    Write-Warning "未提供证书 thumbprint —— 跳过代码签名（仅限本地 dev 构建）"
} else {
    foreach ($target in @($exePath, $msiPath)) {
        & signtool sign /fd SHA256 /tr $TimestampUrl /td SHA256 `
            /sha1 $CertThumbprint /v $target
        if ($LASTEXITCODE -ne 0) { throw "signtool sign 失败: $target" }
    }
    # 签名后立即验证，verify 失败直接让构建挂掉
    & signtool verify /pa /v $msiPath
    if ($LASTEXITCODE -ne 0) { throw "signtool verify 失败: $msiPath" }
    Write-Host "代码签名 + 验证通过: $msiPath"
}
```

也可走 Tauri 原生路径：在 `tauri.conf.json` 的 `bundle.windows` 下配
`certificateThumbprint` / `timestampUrl` / `digestAlgorithm`，让 `tauri build`
自动签名。两种方式二选一即可——脚本签名更透明、便于排错，推荐内测期用脚本签名。

---

## 6. CI 里安全注入证书（私钥绝不进 git）

**铁律：私钥（.pfx / .key）任何形式都不能进 git。** 它属于 CI secret 仓库。

### OV 证书（.pfx 文件型）

1. 把 `.pfx` 文件用 base64 编码：
   ```powershell
   [Convert]::ToBase64String([IO.File]::ReadAllBytes("cert.pfx")) |
       Set-Content cert.pfx.b64
   ```
2. 把 `cert.pfx.b64` 的内容存为 GitHub Actions secret `DESKPET_CERT_PFX_B64`，
   把 .pfx 密码存为 `DESKPET_CERT_PFX_PASSWORD`。
3. CI 步骤里**临时落盘 → 导入 → 用完即删**：
   ```powershell
   # 1. 从 secret 还原 .pfx 到临时目录
   $pfx = Join-Path $env:RUNNER_TEMP "cert.pfx"
   [IO.File]::WriteAllBytes($pfx,
       [Convert]::FromBase64String($env:DESKPET_CERT_PFX_B64))

   # 2. 导入到 runner 的当前用户证书存储
   $pwd = ConvertTo-SecureString $env:DESKPET_CERT_PFX_PASSWORD -AsPlainText -Force
   $cert = Import-PfxCertificate -FilePath $pfx `
       -CertStoreLocation Cert:\CurrentUser\My -Password $pwd

   # 3. 用导入后拿到的 thumbprint 签名
   pwsh scripts\build-msi.ps1 -CertThumbprint $cert.Thumbprint

   # 4. 无论成功失败都清理（放 always() 的 step 里）
   Remove-Item $pfx -Force -ErrorAction SilentlyContinue
   Get-ChildItem Cert:\CurrentUser\My |
       Where-Object Thumbprint -eq $cert.Thumbprint |
       Remove-Item -Force -ErrorAction SilentlyContinue
   ```

### EV 证书（硬件 token 型）

EV 私钥在物理 USB token / HSM 上，**无法导出**，因此**无法在标准 GitHub
托管 runner 上签名**。两条路：

- 用 **self-hosted runner**，物理插着 token，配合 token 厂商的「无人值守 PIN」
  方案（部分 token 支持把 PIN 缓存到会话）。
- 用 **云签名服务**（DigiCert KeyLocker、SignPath、Azure Trusted Signing 等），
  私钥托管在云 HSM，CI 通过 API 凭据调用——这是目前 EV + CI 的主流做法。

无论哪种，CI 日志里**永不能出现** PIN / 凭据明文。

---

## 7. 内测前拿不到证书的降级方案

如果内测排期到了证书还没下来——**不阻塞内测**，走降级方案：

1. 发未签名的 MSI。
2. 在「内测协议」和「已知问题」文档里**明确告知**用户会撞上一次
   SmartScreen 警告，并给出操作引导：
   - 双击 MSI → 弹出蓝色「Windows 已保护你的电脑」窗口
   - 点窗口里的 **「更多信息」** 链接
   - 出现 **「仍要运行」** 按钮 → 点它即可正常安装

   > **截图占位**：在内测说明里放两张截图——
   > （A）SmartScreen 蓝窗初始态，红圈标出「更多信息」
   > （B）展开后的「仍要运行」按钮，红圈标出
   >
   > 截图获取方式：在一台干净 Windows 上双击未签名 MSI 实拍。

3. 把「MSI 未签名」记入风险清单，作为内测期补做项（WI-03 fallback）。

降级方案的核心是**预期管理**——用户事先知道会有这一屏、知道这是正常的，
就不会把它当成「中病毒了」而弃用。
