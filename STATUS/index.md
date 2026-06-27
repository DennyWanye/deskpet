# STATUS 索引

> 本目录存放 DeskPet 的**状态档**——一页式看清项目/模块当前进展。
> 新建状态档后请在此登记一行。功能落地状态以各档为准。

| 文件 | 作用 | 何时读 |
|---|---|---|
| [`status.md`](./status.md) | **全局项目状态**——所有并行 worktree / 核心功能模块完成度 / 最近里程碑 / 已知问题，一页看清整个项目。 | 新 session / 子代理接手项目前先读这里。 |
| [`DeepResearch.md`](./DeepResearch.md) | **deep research 模块专项状态**——`deepresearch`（原 `research_run`）管线与能力清单、plan vs 现状差距、recency 真 bug（已修）、调查盲区、演进建议。 | 要动 deep research / 深度调研功能前先读。 |
| [`AgentLoop.md`](./AgentLoop.md) | **Agent 执行引擎架构调研**——ReAct 主循环、工具注册/分发与权限 gate、四道「防假装完成」守门、TerminationGate / ContextManager 内部实现、main.py 装配与事件转发。 | 要动 agent loop / 工具层 / 守门 / 上下文管理前先读。 |
| [`PPT.md`](./PPT.md) | **PPT 生成模块专项状态**——端到端链路（skill 产大纲 → 工具渲染）、三条渲染路径 + 分派、模板库 + 预览图视觉选模板 + 兜底、AI 配图（gpt-image-2）、视觉评审闭环、关键文件、真机验证、已知短板。 | 要动 PPT 生成 / 大纲质量 / 模板功能前先读。 |

---

## 维护纪律

- 一个模块跑通验收（pytest/vitest/cargo/手工 E2E 全绿）= 完成 → 同步更新对应状态档（见根 `CLAUDE.md` §STATUS 更新纪律）。
- 细节放各 `plans/` 文档，状态档只记状态 + 链接，保持一页能看完。
- 新建专项状态档 → 在本 index 登记一行。
