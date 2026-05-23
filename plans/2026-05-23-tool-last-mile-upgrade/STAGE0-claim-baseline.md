# Stage 0 — LLM 自述完成短语基线（WI-T0.2）

- **日期**：2026-05-23
- **方法**：构造代表性 fixture（覆盖 PRD §6 R4 提到的 5 类失败模式）+ 从 fixture 归纳初版 `verify/claim_patterns.yaml`
- **状态**：v1 baseline；待 beta 期收集真实 LLM 日志后迭代

## §1 Fixture 构造

`backend/tests/fixtures/fake_claims_50.jsonl` — 50 条按 5 桶分布（每桶 ≥ 10，符合 TDD T9-10 强约束）：

| Bucket | 数量 | 描述 | 期望被抓 |
|---|---|---|---|
| `zh` | 10 | 纯中文「已生成/已保存」标准措辞 | ✅ regex 100% 命中 |
| `en` | 10 | 纯英文「I created / Generated / Saved to」 | ✅ regex 100% 命中 |
| `mixed` | 10 | 中英混杂（"已 generate 完了"/"Created 营销周报.pptx"）| ✅ regex 应 ≥ 70%，缺口走 LLM fallback |
| `paraphrase` | 10 | 同义改写（"PPT 已就绪"/"妥妥的"/"出炉"）+ emoji | ⚠️ regex 必漏；走 **D6 二级 LLM fallback** |
| `negative` | 10 | 否定/未来时/条件式（"我暂时无法"/"还没生成"/"我将生成"）| ❌ 一律 0 命中（误抓即测试红） |

总计 50 条，覆盖：

- **完成动词**：已生成 / 已创建 / 已完成 / 已保存 / 已输出 / created / generated / saved
- **文件扩展名**：pptx / xlsx / docx / pdf / png / jpg / svg
- **路径形态**：Windows `C:\...` / Unix `/tmp/...` / 中文路径 `D:\下载\report.pdf`
- **干扰要素**：emoji（☕✨）、引号、括号、多文件并列、表情符号

## §2 初版 ClaimPatterns

`verify/claim_patterns.yaml` — v1 含 9 条 pattern：

| ID | 覆盖 | 来源 fixture |
|---|---|---|
| `zh_generated_file` | "已（为您）生成 X.pptx" | zh-01/02/03/06/10 |
| `zh_file_then_generated` | "文件 X.pptx 已生成成功" | zh-08 |
| `zh_saved_to_path` | "已保存到 D:\\..." | zh-04/07 |
| `zh_file_location` | "文件位置：C:\\..." | （para-09 兜底） |
| `zh_done_complete` | "已完成 PPT 制作，文件位于 X" | zh-07 |
| `en_created_file` | "I created / Generated X.pptx" | en-01/02/05/06/09 |
| `en_saved_to_path` | "saved it to /tmp/..." | en-03/04/10 |
| `en_file_has_been_created` | "X.pptx has been created" | en-07 |
| `mixed_generated` | "已 generate 完 X" / "Created 中文.pptx" | mixed-02/03/05/06/10 |

**全部用 google-re2 编译**（无 lookbehind/backreference），无 ReDoS 风险。

## §3 预期抓获率（按桶）

| Bucket | Regex 单层（v1） | + LLM fallback（D6 二级） | 综合目标（PRD §5 G3）|
|---|---|---|---|
| zh | 100% | 100% | ≥ 95% ✅ |
| en | 100% | 100% | ≥ 95% ✅ |
| mixed | ~70% | ≥ 90% | ≥ 95% ✅（联合）|
| paraphrase | ~0% | ≥ 70% | ≥ 50% |
| **negative** | **= 0%** | **= 0%** | **必须 0%**（否决项） |

综合：50 条 fixture，期望抓获 ≥ 47 (95%) — 与 TDD T9-10 一致。

## §4 注意事项

1. **fixture 不是真实分布**：实际 beta 用户场景里，LLM 措辞会更多样；fixture 仅为锁住已知关键场景，避免回归。真实分布在 beta 期通过 `verify_extractor.fallback_used` metric（PRD §5 健康区间 5%~20%）反推。
2. **negative bucket 是测试守门员**：任何 claim 误抓即测试红；ClaimExtractor 迭代时必须保持 negative bucket 0 命中。
3. **paraphrase bucket 的回退依赖 D6 LLM fallback** — 单 regex 路径不可能 ≥ 70%；strict 模式必须配合 `extractor_fallback_enabled=true`。
4. **patterns 文件位于 worktree 根的 `verify/`**（不在 backend/ 下）—— 设计上 verify/ 是跨语言可读的配置，未来 Tauri 也可能用到。

## §5 后续工作

- **WI-T2.4（Stage 2）**：把这些 patterns 实际接入 `RegexExtractor` 并跑 fixture 验证抓获率
- **WI-T2.4b**：实现 `SmallLLMExtractor` 用于 paraphrase bucket
- **beta 收集真实日志**：从 1 周日志里抽 500 条 LLM 自述，归纳新 patterns 提交 PR

## §6 引用

- PRD §3 D6 / §6 R4 / §5 健康区间
- TDD §B TG-9 T9-10/T9-11/T9-12/T9-12b/T9-13 + §C.3 ClaimPattern Schema + §D fixtures
- 二轮架构评审 N2 — 默认 yaml 100% re2 编译通过的正向用例（待 WI-T2.4 落地 T9-12b 验证）
