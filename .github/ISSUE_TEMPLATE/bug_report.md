---
name: Bug 报告 / Bug Report
about: 桌宠跑挂了 / 表现不符合预期 / 崩溃 / 不响应
title: '[Bug] '
labels: bug
assignees: ''
---

## 描述

简要描述 bug。期望发生什么？实际发生什么？

## 复现步骤

1. 启动 `npm run tauri:dev`（或运行的 frozen build 版本号）
2. 点击 / 输入 / 操作 …
3. 看到 …

## 环境

- DeskPet 版本：（`git rev-parse --short HEAD` 或 release tag）
- 构建模式：`manual` / `relay`
- 操作系统：Windows 11 / macOS 14 / Ubuntu 22.04
- GPU：NVIDIA RTX 4090 / 无独显 / 其他
- Python 版本：`python --version`
- Node 版本：`node --version`
- 后端模式：本地 Ollama / 远程 LLM / 混合

## 日志 / 截图

- 关键日志：附 `backend.log` 末尾 50 行（敏感字段请打码）
- 截图：如果是 UI 问题，截图说明
- 浏览器 devtools console：Tauri 窗口右键 → Inspect → Console 复制相关行

## 已知 workaround

如果你试过临时绕过这个 bug，请说明（即便不完美也帮助定位）。

## Checklist

- [ ] 已搜索现有 issue 没有重复
- [ ] 已 attach 日志或截图（如适用）
- [ ] 日志中已脱敏个人信息 / API key
