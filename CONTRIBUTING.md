# 贡献指南 — DeskPet

谢谢你愿意贡献！这份文档说明 PR 流程、代码风格、提交约定。

---

## 上手前

1. 看过 [README.md](./README.md) 把 dev 环境跑起来
2. 看过 [QUICKSTART.md](./QUICKSTART.md) 理解项目布局
3. 看过 [LICENSE.FAQ.md](./LICENSE.FAQ.md) 理解 BUSL-1.1（你的 PR 自动按 BUSL-1.1 发布）
4. 看过 [CODE_OF_CONDUCT.md](./CODE_OF_CONDUCT.md)

---

## PR 流程

### 1. 先开 issue（除非是 typo / 文档小改）

对于功能 / 重构 / bug fix，**先开 issue 讨论方向**，避免做完发现方向不对。
小修小改可以直接 PR。

### 2. fork → 开分支 → 提交

```bash
git checkout -b feat/short-description    # 功能
git checkout -b fix/short-description     # bug 修复
git checkout -b chore/short-description   # 杂项 / 文档
git checkout -b refactor/short-description
```

分支名 kebab-case，前缀 `feat` / `fix` / `chore` / `refactor` / `docs`。

### 3. 提交规范（Conventional Commits）

```
<type>(<scope>): <subject>

<body>
```

`<type>` 可选 `feat` `fix` `chore` `refactor` `docs` `test` `perf` `ci` `revert`。

示例：
```
feat(memory): add hierarchical chunking for L2 episodic memory
fix(tauri): clean orphan deskpet.exe on TaskStop (Windows)
chore(oss-prep): add SPDX headers to all source files
```

主题 ≤ 70 字符，body 解释"为什么"（不止"什么"）。

### 4. 跑测试 + 类型检查

```bash
# Backend
cd backend && python -m pytest -v
cd backend && mypy . --strict   # 如果你改了 typed module

# Frontend
cd tauri-app && npm test
cd tauri-app && npx tsc -b      # 类型检查
cd tauri-app && npm run lint
```

**红的 PR 不会被 review**。CI 跑同样的 gate，本地先过。

### 5. 提 PR

- title 跟 commit 一致风格（Conventional Commits）
- description 用项目 PR 模板（`.github/PULL_REQUEST_TEMPLATE.md`）
- 关联相关 issue（`Closes #123`）
- 标 reviewer 不强求 —— 维护者会主动 triage

---

## 代码风格

### Python (backend/)

- **PEP 8** 基础 + **black** 格式化（line length 100）
- **类型注解**：新代码必须有 type hints（`from __future__ import annotations`）
- **测试覆盖**：新功能必带 pytest 用例
- **文件头 SPDX**：新文件由 `scripts/oss/add_spdx_headers.py` 自动加，
  或者手动加：
  ```python
  # SPDX-FileCopyrightText: 2026 <你的名字或 GitHub username>
  # SPDX-License-Identifier: BUSL-1.1
  ```

### TypeScript (tauri-app/src/)

- **ESLint** + **Prettier**（项目已配置）
- 严格 TypeScript：避免 `any`，用 `unknown` + type guards
- React function components only，hooks 顺序固定
- 文件头 SPDX：
  ```typescript
  // SPDX-FileCopyrightText: 2026 <你的名字>
  // SPDX-License-Identifier: BUSL-1.1
  ```

### Rust (tauri-app/src-tauri/src/)

- `cargo fmt` + `cargo clippy --all-targets -- -D warnings`
- 文件头：
  ```rust
  // SPDX-FileCopyrightText: 2026 <你的名字>
  // SPDX-License-Identifier: BUSL-1.1
  ```

### 通用

- **不在源码里写凭据 / API key**（pre-commit hook 会拦）
- **中文注释 OK**（项目主语言是中文 + 英文混合）
- 文件命名：snake_case（Python）/ kebab-case 或 camelCase（TS，跟周围一致）

---

## 测试要求

| 改动类型 | 必须的测试 |
|---|---|
| 新后端模块 / 新 API endpoint | pytest unit test，覆盖 happy path + 主要 error path |
| 前端新组件 / 新 store action | vitest test |
| Rust 新 IPC command | `#[cfg(test)]` unit test |
| Bug fix | 一个 regression test 复现原 bug，证明 fix 有效 |
| 重构 | 测试已存在 ✓；新行为 = 0 新测试 |
| 纯文档 / 注释 | 不要求 |

---

## DCO / 版权 / 协议

- DeskPet 不要求签 CLA
- 提交 PR 即默认你同意你的贡献**以 BUSL-1.1 协议发布**（且 2030-05-27 后转 Apache 2.0）
- 你的版权归你自己；只需在你新建的源文件里写自己的 `SPDX-FileCopyrightText` 即可
- 如果引入新的第三方依赖：
  - 在 `licenses/README.md` 里登记 license
  - 优先选 MIT / Apache-2.0 / BSD-3 / ISC 兼容协议
  - GPL 系列 / AGPL 不能直接用（会传染整个项目）

---

## 我能贡献什么？

最容易上手的几类：

| 类型 | 标签 | 推荐起点 |
|---|---|---|
| 文档 / typo | `good first issue` `docs` | 直接 PR |
| 新语言翻译 | `i18n` | 先开 issue 讨论 |
| Bug 修复 | `bug` | 找 `good first issue` + `bug` 双标签的 |
| 新 LLM Provider 适配 | `provider` | 看 `backend/llm/` 里现有适配器 |
| 新工具（Skills） | `skill` | 看 `backend/deskpet/skills/builtin/` 现有例子 |
| 新 Live2D 模型支持 | `live2d` | 看 `tauri-app/src/components/Live2DCanvas.tsx` |

---

## 维护者会怎么 review

通常 1-3 天首次响应。优先级：

1. **是否解决了真实问题** —— 没 issue / 解决的问题不存在的 PR 会被关
2. **是否有测试** —— 无测试的 feature/fix 一律返工
3. **是否破坏现有行为** —— breaking change 必须明示
4. **代码风格** —— 自动化能补的不卡你，但人眼难读的会让你重写
5. **协议合规** —— 引入 GPL 依赖 / 抄了非许可代码 → 直接拒

review 来回 3+ 轮还没收敛的 PR 可能被关闭，可以重开新 PR 重新讨论。

---

## 问题？

开 [GitHub Discussions](https://github.com/DennyWanye/deskpet/discussions) 或
普通 issue。中文 / English 都接受。

谢谢贡献！🐈
