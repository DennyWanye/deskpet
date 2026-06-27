# P2-0-S1 图标品牌化（临时占位）设计文档

**Date**: 2026-04-14
**Sprint**: V6 Phase 2 · Sprint P2-0 · Slice S1
**Decision carry-over**: V6 §3.1 · D0-1（AI 生成占位）
**Status**: DRAFT（brainstorm 已通过，等 writing-plans 出实施计划）

---

## 1. 背景

Phase 1 收尾时（`v0.1.0-phase1`）发现 `tauri-app/src-tauri/icons/icon.png` 是纯红色方块占位符，`public/favicon.svg` 是 Vite 模板紫色闪电，整个产品无自有品牌素材。项目内唯一可见的角色资产是 `public/assets/live2d/hiyori/` —— 属 Live2D Inc. Cubism SDK sample，**版权限制不能商用分发**。

V6 §3.1 决策 D0-1 已签：走 AI / 手工占位路径，等未来设计师接手再做品牌体系级替换。本 slice 交付的是一个临时但不丢人的占位图标。

## 2. 目标与非目标

**目标**
- 让首次对外 release（`v0.2.0`）的任务栏 / 托盘 / installer / WebView favicon 看起来像个正经产品
- 替换掉当前的红色方块 / 默认 Vite logo
- 视觉可跨 16×16 到 1024×1024 全尺寸保持辨识度
- 留好"设计师二次替换"的接口（源文件可版本控制、管道可一键重跑）

**非目标**
- ❌ 品牌视觉体系 / logo 规范 / VI 手册（留给真正的设计介入）
- ❌ 多表情 / 多主题图标集
- ❌ 深色 / 浅色模式自适应切换
- ❌ 动画图标（Windows 生态 ROI 低）
- ❌ App Store / 商城级 metadata 图

## 3. 视觉规格

### 3.1 主体

**类别：** 抽象吉祥物（非真实角色，避免与 Live2D Hiyori 或未来设计师版角色冲突）

**形象：** 黏土质感紫色云朵精灵

**构成：**
- 云体：3–4 个重叠圆形组合的经典云朵轮廓，整体接近正方形外接框以适应图标画布
- 面部：两只豆豆眼（实心圆点），一条小微笑弧
- 腮红：两个淡粉圆点（可选，16×16 尺寸可省略）

### 3.2 配色

| 元素 | 色值 | 用途 |
|---|---|---|
| 云身深紫（底部阴影） | `#863bff` | 对齐 `public/favicon.svg` 现有主色，保留品牌紫 |
| 云身中紫 | `#9d63ff` | 渐变中段 |
| 云身浅紫（顶部高光） | `#b899ff` | 立体感过渡 |
| 云身最亮高光 | `#ede6ff` | 顶部反光点，2024 黏土质感的关键 |
| 眼睛 | `#1a0b33` | 近黑深紫，比纯黑更温和 |
| 嘴 | `#1a0b33` | 粗线弧 |
| 腮红 | `rgba(255,158,199,0.5)` | 淡粉半透 |

**渐变类型：** 径向渐变（center ≈ 顶部略偏左，模拟光源从左上打来），方向 `#ede6ff → #b899ff → #9d63ff → #863bff`。

### 3.3 风格参考关键词

"claymorphism icon" · "3D clay mascot" · "soft shaded cloud character" · 参考 Arc browser / Craft / Raycast 2024 图标生态的立体质感。

### 3.4 最小尺寸妥协

16×16 托盘尺寸下：
- 云朵轮廓 + 两只眼睛保留
- 嘴 / 腮红 / 高光细节可能糊成一团 —— 接受
- 合格判据：眯眼看还能辨认是一个**带眼睛的云状物**

## 4. 产出管道

选择了 T3 **无外部 API / 无版权素材** 路径。最终方案是**手写 SVG + 单步渲染成 PNG + `npx tauri icon` 铺全套**。

```
┌─────────────────────────────────────────────────────────────┐
│ Step 1 · 手写 SVG                                           │
│   tauri-app/src-tauri/icons-src/deskpet-cloud.svg           │
│   - 纯代码，含径向渐变 + 圆组合云朵 + 五官                  │
│   - 版本控制，带注释说明色值/结构                            │
└──────────────────────┬──────────────────────────────────────┘
                       ↓
┌─────────────────────────────────────────────────────────────┐
│ Step 2 · SVG → PNG 1024×1024                                │
│   工具候选（plan 阶段敲定）：                                │
│     · npx @resvg/resvg-js-cli                               │
│     · sharp CLI                                              │
│     · Chrome headless screenshot                             │
│   产物：tauri-app/src-tauri/icons-src/deskpet-cloud.png     │
└──────────────────────┬──────────────────────────────────────┘
                       ↓
┌─────────────────────────────────────────────────────────────┐
│ Step 3 · npx tauri icon                                      │
│   cd tauri-app && npx tauri icon src-tauri/icons-src/deskpet-cloud.png │
│   自动铺 icon.ico / icon.png / 各 android/ios 尺寸          │
└──────────────────────┬──────────────────────────────────────┘
                       ↓
┌─────────────────────────────────────────────────────────────┐
│ Step 4 · favicon 同步                                        │
│   复制 deskpet-cloud.svg → tauri-app/public/favicon.svg     │
│   （WebView 内 HTML favicon 用 SVG 原生更清晰）              │
└─────────────────────────────────────────────────────────────┘
```

所有 step 写成 `scripts/rebuild-icons.ps1`（或 `.sh`），一键重跑，未来换设计师版本只替换 `deskpet-cloud.svg`。

## 5. 目录结构变更

```
tauri-app/
├── public/
│   └── favicon.svg            [REPLACE] 云朵 SVG
└── src-tauri/
    ├── icons/                 [REGENERATE via tauri icon]
    │   ├── icon.ico
    │   ├── icon.png
    │   ├── 32x32.png, 64x64.png, 128x128.png, 128x128@2x.png
    │   ├── android/...
    │   └── ios/...
    └── icons-src/             [NEW 目录]
        ├── deskpet-cloud.svg  [NEW 手写源]
        ├── deskpet-cloud.png  [NEW 1024 渲染产物]
        └── README.md          [NEW 说明如何重跑 pipeline]

scripts/
└── rebuild-icons.ps1          [NEW] 一键重跑脚本
```

## 6. 接入点 & follow-up 预留

未来设计师替换真品牌图标的接口：

1. 拿到新版 PNG 或 SVG
2. 如果是 SVG → 覆盖 `icons-src/deskpet-cloud.svg`（保留文件名）
3. 如果是 PNG → 覆盖 `icons-src/deskpet-cloud.png`（保留文件名），跳过 Step 2。**同时删除 `deskpet-cloud.svg` 或在其顶部加注释标记为"已被 PNG 版本替代，请保持 PNG 为真实源"**，避免两个源并存造成维护混淆
4. 跑 `scripts/rebuild-icons.ps1`
5. 提交一个 follow-up slice commit：`feat(branding): replace placeholder icon with designer version`

其他代码（Tauri conf、HTML、autostart）完全不用改。

## 7. 成功判据

| # | 判据 | 验证方式 |
|---|---|---|
| C1 | `icon.png` 不再是纯红方块 | 肉眼 + `file` 命令检查 |
| C2 | 打包后 installer / 任务栏 / 托盘 / WebView favicon 四处图标一致 | 跑 `npm run tauri build` 后人眼过一遍 |
| C3 | 16×16 托盘尺寸能辨认出是云状图形 | 托盘截图检查 |
| C4 | SVG 源在 `icons-src/` 且带注释 | 文件存在 + 注释密度 check |
| C5 | `scripts/rebuild-icons.ps1` 存在且幂等（重跑两次产物一致） | 跑两次 diff |
| C6 | `docs/RELEASE.md` 或 `icons-src/README.md` 说明这是临时占位 | grep |
| C7 | `tsc --noEmit` + `cargo check` 均不受影响 | CI gate |

## 8. 刻意不做（YAGNI 清单）

- 多语言 favicon（中文/英文变体）
- PWA manifest 多尺寸图标（Tauri 不是 PWA）
- `.icns`（macOS）专项调优 —— `tauri icon` 自动处理的基线即可
- 图标的"拟人化 / 摆动动画"静态帧（Phase 2 后面才考虑桌宠表情）
- SVG animation（`<animate>` 标签）—— 浏览器 favicon 里不稳定
- 主题色跟随系统 accent color

## 9. 风险与回落

| 风险 | 影响 | 回落方案 |
|---|---|---|
| SVG 手写的黏土效果不够好看 | 视觉品控未达"不丢人"下限 | 允许用 Python Pillow 脚本后处理加高斯模糊高光；或改用 `ecc-fal-ai-media` / `geek-seedream-imagegen`（如果 key 补上） |
| 16×16 实在糊 | C3 不通过 | 为 16×16 / 32×32 单独手绘一个像素级简化版（只剩轮廓 + 一只眼），在 `tauri icon` 之后单独替换这两档 |
| `npx tauri icon` 对非方形输入有奇怪 padding | icon.png 被加白边 | 预处理确保 SVG 画布 1024×1024 方形，主图居中，留 ~10% padding |
| Tauri build 之后 installer 仍显示旧图标 | Windows 图标缓存问题 | `ie4uinit.exe -show` 或重启资源管理器刷新缓存；写入 RELEASE.md |

## 10. 测试策略

此 slice 是**资产 / 管道类改动**，没有运行时行为。测试策略：

- **无单元测试**（没有业务逻辑）
- **可视检查 clause**：plan 阶段要求 implementer 在 `icons-src/README.md` 贴一张 16/64/256 三尺寸对比截图作为 PR 证据
- **回归检查**：跑一次 `cd tauri-app && npm run tauri build --debug` 确认 build 没因图标改动崩掉
- **E2E 不变**：现有 `drive_via_cdp.py` 五步依然要全绿（图标不影响业务）

## 11. 开工前待确认（plan 阶段次级决策）

本设计已 sign-off。以下两个实现级选择留给 `writing-plans` 阶段敲定，不回头改 spec：

- **实现决策 1**：SVG → PNG 的具体工具。候选 `npx @resvg/resvg-js-cli` > `sharp` CLI > Chrome headless。plan 写第一候选作默认，第二候选作备选；implementer 第一次跑通的那个胜出
- **实现决策 2**：`rebuild-icons` 脚本形态。项目是 Windows 为主，优先产出 `.ps1`；如果 plan 认为值得加 bash 兜底（CI 或未来 macOS 开发），再加一份。优先走单脚本，不要 over-engineer 成跨平台工具

## 12. 下一步

1. ~~Brainstorm~~ ✅
2. Spec self-review（下一步）
3. 用户 review 本文档
4. 通过后进 `sp-writing-plans` → `docs/superpowers/plans/2026-04-14-p2s1-icon-branding.md`
5. Worktree 隔离 + `sp-subagent-driven-development` 执行
