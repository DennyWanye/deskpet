## 改了什么

简要描述这个 PR 解决什么问题。

Closes #(issue 号 — 如果有相关 issue)

## 类型

- [ ] Bug fix（不破坏现有行为）
- [ ] 新功能（不破坏现有行为）
- [ ] Breaking change（修改/移除现有行为）
- [ ] 重构（行为不变，代码结构变）
- [ ] 文档 / 注释
- [ ] CI / 构建 / 工具链
- [ ] 性能优化

## 如何测试

描述你怎么验证这个 PR 是对的：

```bash
# 示例
cd backend && python -m pytest tests/test_my_new_feature.py -v
```

- [ ] 新加了自动化测试（pytest / vitest / cargo test）
- [ ] 手动测试过（描述步骤）
- [ ] 不需要测试（纯文档 / 注释）

## Checklist

- [ ] 我看过 [CONTRIBUTING.md](../CONTRIBUTING.md)
- [ ] 我的代码符合项目代码风格（black / eslint / cargo fmt）
- [ ] 新文件加了 SPDX header（`scripts/oss/add_spdx_headers.py` 可一键补）
- [ ] 本地跑过相关测试都过了
- [ ] 本地跑过类型检查都过了（`mypy` / `tsc -b` / `cargo clippy`）
- [ ] 没有引入硬编码凭据 / API key
- [ ] 如果加了第三方依赖，在 `licenses/README.md` 登记了 license
- [ ] 如果改了用户可见行为，更新了 README / QUICKSTART / 相关 docs

## 截图 / 视频（如适用）

UI 改动请放截图（before / after）或短视频。

## 备注

reviewer 该特别看什么？有没有故意留的不优雅但有原因的代码？
