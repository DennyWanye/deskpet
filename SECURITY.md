# Security Policy

## 报告渠道

发现 DeskPet 中存在安全问题？请**不要**直接开公开 issue。请改用：

**[GitHub Security Advisories](https://github.com/DennyWanye/deskpet/security/advisories/new)**

这是 GitHub 提供的私密漏洞披露渠道，只有维护者能看到。我们会在 72 小时
内首次响应，并在修复后协调公开披露。

## 支持的版本

| 版本 | 安全更新支持 |
|---|---|
| `0.5.x` (当前) | ✅ 接收修复 |
| `< 0.5.0` | ❌ 不再维护 |

## 范围

以下视为本项目范围内的安全问题：

- 桌宠主程序或后端 (FastAPI) 的远程代码执行 / 任意文件读写
- 后端 IPC / WebSocket 接口认证绕过
- 写入 OS keychain 的凭据被本地非授权进程读取
- 第三方 LLM Provider 的 API key 在日志 / dump 中泄露
- 用户对话历史 / 本地数据库的越权访问

以下**不在**范围（请走普通 issue 流程）：

- 第三方依赖的已知 CVE — 请先在 issue 区搜索是否已跟踪
- 用户主动配置的 LLM Provider 把数据上传到云端（这是设计行为）
- DEV 模式专属功能在生产构建里不可达
- Live2D Cubism Core 等三方组件本身的漏洞（请走上游 Live2D 渠道）

## 我们承诺

- 收到报告 72 小时内首次响应
- 与你协商合理的修复 & 披露时间表（通常 30~90 天）
- 在公开 advisory 中署名致谢（除非你希望匿名）

## 安全相关设计文档

- 后端 IPC 共享密钥认证：见 `backend/main.py` 控制 ws 路由
- 凭据存储：所有 LLM API key 走 OS keychain（Windows DPAPI / macOS
  Keychain / Linux libsecret），明文**绝不**进入 `config.toml`
- 数据目录隔离：用户数据在 `%APPData%\deskpet\`，不会污染源码仓
