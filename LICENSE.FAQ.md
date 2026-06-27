# LICENSE FAQ — 关于 DeskPet 的开源许可证

> 中文友好的常见问题解答。本文档**不是**法律意见，正式条款以 [`LICENSE`](./LICENSE) 文件为准。

---

## TL;DR

- DeskPet 使用 **Business Source License 1.1 (BUSL-1.1)**
- **个人自用、研究、学习、贡献代码、做非竞争性产品 → 没问题，随便用**
- **不能做的事**：把 DeskPet 作为托管/嵌入服务**卖给第三方**，跟原作者付费版本**直接竞争**
- **2030-05-27 之后**：自动转 **Apache License 2.0**，所有限制解除

---

## Q1: BUSL-1.1 是什么？为什么不用 MIT / Apache？

**BUSL** 全称 *Business Source License*，由 MariaDB 在 2017 年发起，目前 HashiCorp (Terraform/Vault)、Sentry、CockroachDB、Couchbase 等都在用。

它是一种 **"延迟开源"** 协议：
- 发布时附带一条 **商业使用限制**（防止云厂商白嫖你的代码做托管服务跟你竞争）
- 但设定一个 **Change Date**（本项目：2030-05-27），到期后自动转成一个标准 OSI 许可证（本项目：Apache 2.0）

**为什么用 BUSL 而不是 MIT/Apache**：保留商业可持续性的可能。如果项目未来发展出付费版本，BUSL 防止有人把代码原封不动包装成 SaaS 服务卖。但 4 年后会完全开源，社区可以放心 fork。

**为什么不用 AGPL**：AGPL 的传染性会让所有集成方都被强制开源，使用门槛太高。BUSL 不传染。

---

## Q2: 我能用 DeskPet 做什么？（✅ 允许）

只要不构成 "competitive offering"，**几乎所有用途都允许**，包括商业用途：

- ✅ **个人桌面使用**（无论你是不是商业用户）
- ✅ **公司内部使用**（团队/全公司部署 DeskPet 给员工当桌宠，完全可以）
- ✅ **研究、学习、教学**
- ✅ **fork 修改**（自己玩、PR 回上游、做学术实验）
- ✅ **基于 DeskPet 开发上层应用**（只要你的产品不是直接拿 DeskPet 当服务卖）
- ✅ **在 DeskPet 里集成你自己的付费服务**（比如接你公司的 API）
- ✅ **写博客/做视频/出书介绍 DeskPet**

---

## Q3: 我不能用 DeskPet 做什么？（❌ 受限）

只有一种情况受限：

- ❌ **把 DeskPet 作为托管 (hosted) 或嵌入 (embedded) 服务卖给第三方，跟 DeskPet 官方的付费版本直接竞争**

具体看 [`LICENSE`](./LICENSE) 里的 "Additional Use Grant" 段。关键定义：

- **"Competitive offering"** = 付费提供给第三方、且功能跟官方付费版本显著重叠的产品
- **"Hosted"** = 提供给最终用户作为服务运行（典型如 SaaS）
- **"Embedded"** = 把 DeskPet 的源码或可执行代码打包进竞品里

**当前情况说明**：DeskPet 现阶段**没有付费版本**，所以严格说目前没有 "competitive offering" 这个标的物。但为了保留未来可能性，条款先放在这里。

**模糊地带怎么办**：如果你不确定你的用途算不算 "竞争"，[开个 issue](https://github.com/DennyWanye/deskpet/issues) 问。

---

## Q4: Change Date (2030-05-27) 之后会发生什么？

- 2030 年 5 月 27 日，BUSL-1.1 的所有限制**自动失效**
- 协议**自动转为 Apache License 2.0**
- Apache 2.0 是 OSI 认证的标准开源协议，无任何商业限制
- 这是**协议保证的、不可撤销的**承诺 —— 即使原作者后续不维护或失联，到期自动生效

---

## Q5: 我贡献代码 (PR) 时，代码归属怎么算？

- 提 PR 即默认你同意你的贡献以 BUSL-1.1 发布（以及 2030-05-27 后转 Apache 2.0）
- 不需要签 CLA
- 你的版权归你自己（DeskPet 不要求版权转让）

---

## Q6: DeskPet 引用了哪些第三方组件？它们的许可证是什么？

见 [`licenses/`](./licenses/) 目录：

| 组件 | 许可证 |
|---|---|
| Live2D Cubism Core | Live2D 专有 EULA（**注意**：npm metadata 写 ISC 是错的） |
| Hiyori 示例模型 | Live2D Free Material License |
| `pixi-live2d-display` | MIT |
| 其他 npm/cargo/pip 依赖 | 各自原始许可证（见 `licenses/README.md` 索引） |

---

## Q7: 有疑问/想用于不确定的场景怎么办？

- 看完本 FAQ 还有疑问 → [开 issue](https://github.com/DennyWanye/deskpet/issues)
- 法律层面的咨询请找你的律师，本项目维护者不提供法律意见

---

## Q8: 我能商业使用 DeskPet 吗？

**绝大多数商业场景：可以**。

| 场景 | 可以吗 |
|---|---|
| 公司给员工部署 DeskPet 当工作伙伴 | ✅ 可以 |
| 做 PPT/办公辅助工具卖钱，里面用 DeskPet 当 UI | ✅ 可以（前提是 DeskPet 不是你产品的主体卖点） |
| 在 DeskPet 上接付费 API 服务 | ✅ 可以 |
| 卖一个"基于 DeskPet 的桌宠 SaaS 服务" | ❌ 这个是 hosted 竞争 |
| 把 DeskPet 改头换面包装成自己的桌宠产品卖 | ❌ 这个是 embedded 竞争 |

模糊就问。

---

## 参考链接

- BUSL-1.1 原文：[LICENSE](./LICENSE)
- BUSL 官方说明：https://mariadb.com/bsl11/
- HashiCorp 的 BUSL FAQ（可作参考）：https://www.hashicorp.com/license-faq
- Sentry 的 BUSL 解释：https://blog.sentry.io/relicensing-sentry/

---

*Last updated: 2026-05-27*
