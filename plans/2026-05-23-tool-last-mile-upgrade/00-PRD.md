# PRD — DeskPet 工具调用 Last-Mile 升级（Artifact UX + Outcome Verification）

- **日期**：2026-05-23
- **作者**：架构师视角（20y 经验）
- **状态**：v2.1（吸纳二轮评审 5 个 N 项残留）
- **变更日志**：
  - v1 (2026-05-23 上午)：首版
  - v2 (2026-05-23 下午)：按架构评审报告修订，关键变化：
    - D3 路径白名单补 UNC/mapped drive/8.3 短名
    - D5 ToolReceipt 字段对齐至 12 项（与 TDD §C.2 同源），sha256 异步化
    - D6 ClaimExtractor 改为 strategy 模式，regex 白盒 + 小 LLM 兜底；第 3 次失败前调度 ephemeral verifier subagent 救援
    - D7 outcome verifier 强制带 `changed_files` scope + 缺 toolchain skip
    - D10 增加 flag 组合 invariant 启动校验
    - D11 HMAC key 走 DPAPI/Keychain，不裸存文件
    - 新增 R11/R12/R13（HMAC 多用户、卸载迁移、toolchain 缺失）
    - §4 Stage 1 新增 WI-T1.7（埋点）；Stage 3 新增 WI-T3.5（acceptance 脚本化）
    - 术语"失败计数 = 3 即强退"全文统一
  - v2.1 (2026-05-23 晚)：吸纳二轮评审 5 N 项：
    - N1 D6 ephemeral 输入 ledger 仅含 sig-valid receipts（防恶意/异常 receipt 注入误判）
    - N2 默认 `claim_patterns.yaml` 必须 100% re2 编译通过（TDD T9-12b 正向用例）
    - N3 D6/MR-9 统一为「failure_count==3 时立即调度，不再回灌主 LLM」
    - N4 sha256_pending receipt 在 VerifyGate 的语义（path/title 匹配即放行 + skip+warn，patch 后异步重核）
    - N5 D10 invariant：`ephemeral_subagent_model` 缺省时默认 `"haiku"` + warn
    - §5 度量表补充 metric 健康区间
- **关联调研**：`plans/2026-05-22-ppt-deepresearch-survey.md`、`plans/2026-05-21-memory-system-survey.md`，以及 2026-05-23 本仓的"工具调用层 + 2026 harness 最佳实践"调研报告
- **范围合并依据**：调研报告综合判断"DeskPet 侧 UX 断点"与"codingsys 侧 outcome verifier/tool receipt"是同一主题——**工具调用真正抵达用户**——故合并为同一 PRD。

---

## §1 问题陈述

### 1.1 现状（基于调研）

DeskPet 后端工具层架构已是 2025 主流水平：function-calling 协议 + ReAct 自循环 + 并发分发 + PermissionGate + CircuitBreaker。`ppt_create`/`excel_*`/`doc_*`/`pdf_*`/`image_*` 等 20+ 工具已注册可用。**问题不是工具能不能调，而是工具调完之后的"最后一公里"和"是否真的完成了"。**

### 1.2 四个具体断点（调研锁定）

| # | 断点 | 复现路径 | 受影响工具 |
|---|------|---------|-----------|
| B1 | 产物路径只显示为纯文本 | 「帮我生成 PPT」→ 用户拿到 `/tmp/deskpet-ppt-1716...pptx` 一串路径，没有「在文件夹中显示」「用 PowerPoint 打开」按钮 | ppt/excel/doc/pdf/image/file_organize |
| B2 | 默认存 `tempfile.gettempdir()` | 重启/系统清理 → 文件丢失；用户不知道在哪 | 同上 |
| B3 | 复杂工具的 outline / 参数 0 中间预览 | LLM 一次性构造 PPT outline JSON，错就整张重来 | ppt/research/deep_research |
| B4 | `_HAS_PPTX` 等可选依赖缺失时静默 fallback | 用户以为生成了 PPT，实际拿到 markdown 字符串 | ppt/excel/doc/pdf/ocr |

### 1.3 第五个跨工具断点：plausible completion trap（来自 2026 业界共识）

agent_loop 在 LLM 输出 `end_turn` 后即认为完成，**没有"自述对账"环节**——LLM 可能：

- 说「已为您生成 PPT」但**根本没调** `ppt_create`（幻觉）
- 调了但工具返回 `{"ok": false, "error": "..."}`，LLM 仍按"完成"措辞回复
- 调了但文件实际不在它声称的路径（路径被截断/拼接错误）

这正是 MEMORY 里反复出现的 `feedback_real_test` / `feedback_real_e2e_not_script_replay` / `feedback_simulate_manual_test` 的根因。

### 1.4 为什么现在做

- 2026 Anthropic / OpenAI / Cognition 已收敛共识：**outcome-based verification 取代 transcript trust**，HMAC 工具收据（arXiv 2603.10060 / NABAOS）成为生产标准。
- DeskPet 即将进入 beta 100 用户范围（见 `2026-05-22-beta-100-readiness.md`），此时上 fake-completion 防护成本最低。
- memory-v2 升级（plans/2026-05-22-memory-system-upgrade）落地后，agent 决策更复杂，更需要 outcome 校验兜底。

---

## §2 目标 & 非目标

### 2.1 目标 G1-G5

| ID | 目标 | 验收信号 |
|---|------|---------|
| G1 | 用户在对话气泡里能**一键操作**任何工具产物（打开/在文件夹显示/复制路径/另存为） | 手测 MR-1/MR-3 通过 |
| G2 | 工具产物有**可预测的默认保存位置**，用户可配置覆盖 | `<user_data>/artifacts/<YYYY-MM-DD>/<tool>/` 默认；config 可改 |
| G3 | LLM 自述「已完成 X」必须**对应一条带 HMAC 签名的 ToolReceipt**，否则 VerifyGate 阻止 end_turn 并回灌反馈 | MR-4 通过，回归 fake-completion 抓获率 ≥ 95% |
| G4 | agent_loop 增加 **outcome verifier**：声明涉及文件/构建/测试时，自动跑 `file_exists` / `build` / `pytest` / `git diff` 校验 | MR-5/6/7 通过 |
| G5 | 所有变更**默认 feature flag off**；flag off 时与现状字节级一致（包括 tool_result JSON 字段顺序） | 第一代零回归 MR-0 通过（一票否决） |

### 2.2 非目标 N1-N5

| ID | 非目标 | 原因 |
|---|--------|------|
| N1 | 不重写工具协议（仍用 OpenAI function-calling + Anthropic 适配） | 协议层稳定，无需改动 |
| N2 | 不在本 PRD 引入云端可信执行环境（remote attestation） | 单机桌宠场景过度设计 |
| N3 | 不替换现有 PermissionGate / CircuitBreaker | 已稳定运行，正交职责 |
| N4 | 不做多 agent peer-to-peer 协调（Cognition 已论证不可靠） | 与现有 orchestrator+subagent 模式不冲突。**注**：D6 的 ephemeral verifier subagent 是 orchestrator 内部短时子任务（继承 ledger、单轮、无对等通信），不属于 peer-to-peer 协调，与 N4 不冲突 |
| N5 | 不引入新的前端框架；ArtifactCard 在现有 React + Tailwind 基础上扩展 | 控制变更面 |

---

## §3 核心决策 D1-D12

### D1 — 工具产物统一信封 `ToolArtifact`

所有"产生用户可操作产物"的工具，`execute_tool` 返回 `result.artifacts: ToolArtifact[]`。**不破坏现有 `result.ok / result.path` 字段**（向后兼容期 3 个版本）。

```python
@dataclass
class ToolArtifact:
    kind: Literal["file", "url", "text", "image", "table"]
    path: str | None              # 绝对路径（file/image 必填）
    url: str | None               # http(s) URL（url 必填）
    mime: str | None              # MIME type，用于前端图标
    title: str                    # 用户可见标题（e.g. "营销周报.pptx"）
    preview: str | None           # 文本/markdown 预览（≤ 2KB，长则截断）
    size_bytes: int | None
    sha256: str | None            # 用于 receipt 对账
    created_at: str               # ISO 8601
    actions: list[ArtifactAction] # 可执行动作（前端按 kind 给默认集）
```

`ArtifactAction.id ∈ {"open", "show_in_folder", "copy_path", "save_as", "preview"}`。前端按 `kind` 选择默认 actions 集合，工具可自定义覆盖。

### D2 — 前端 `ArtifactCard` 按 `kind` 分发

`MessageBubble.tsx` 现有 `ToolResultCard`（L307）升级为分发器：

- 如果 `result.artifacts` 存在 → 渲染 `ArtifactCard[]`（每个 kind 对应一个子组件：`FileArtifactCard` / `UrlArtifactCard` / `TableArtifactCard` …）。
- 否则保留旧 `ToolResultCard` 渲染（JSON fold）——**这是 G5 的字节级一致保证点**。
- `ArtifactCard` 顶部固定 `title + kind icon`，底部固定 `actions toolbar`（按 D1 的 actions[]）。

### D3 — 文件操作走 Tauri，安全限定

新增 Tauri command（Rust 侧 `src-tauri/src/artifact_ops.rs`）：

| Command | 功能 | 安全策略 |
|---|------|---------|
| `artifact_open(path)` | 调系统默认 app 打开 | 经 `canonicalize_path` 后必须在 `<user_data>/artifacts/` 子树或 `<user_data>/downloads/` 内，否则拒绝 |
| `artifact_show_in_folder(path)` | Windows: `explorer /select,`；macOS: `open -R` | 同上路径白名单 |
| `artifact_save_as(src, suggested_name)` | 弹原生 file picker → 复制 | 源路径白名单；目标路径用户选择 |
| `artifact_copy_path(path)` | 写剪贴板 | 路径白名单（防恶意 LLM 把 `C:\Windows\System32\...` 塞进来） |

**路径白名单的标准化（关键 — 防 Windows 上的常见绕过）**：

`canonicalize_path(raw)` 必须依次处理：

1. **8.3 短文件名展开**：Windows `PROGRA~1`/`PROGRA~2` 用 `GetLongPathNameW` 展开为长名。
2. **UNC 路径拒绝**：以 `\\?\UNC\` / `\\server\share\` / `\\.\` 开头的，直接拒（除非显式落在 user_data 的 UNC 形式上，本期不支持）。
3. **Mapped drive 解析**：`Z:\foo` 用 `WNetGetUniversalNameW` 反查是否实际指向 UNC；若指向 UNC，按规则 2 拒。
4. **符号链接 / Junction 解析**：用 `GetFinalPathNameByHandleW`（Windows）/ `realpath`（macOS）追踪到最终目标，再比对。
5. **大小写归一**：Windows 用 `normcase`；比对前后路径都过同样规则。
6. **再比对白名单**：`final.starts_with(allowed_root)`，避免字符串拼接漏洞。

实现位于 `src-tauri/src/artifact_paths.rs`，单测见 TDD TG-4 T4-1 ~ T4-9。

**安全模型**：白名单不是为了对抗用户（这是单机桌宠，按 MEMORY `feedback_no_sandbox_constraints` 不加 Claude-Code 风格沙箱），而是防止 **LLM 幻觉路径或上游工具 bug** 误操作系统目录——只防手滑级破坏。

### D4 — 默认保存路径策略

```
<user_data>/artifacts/<YYYY-MM-DD>/<tool_name>/<title_slug>-<short_hash>.<ext>
```

例：`%APPDATA%/deskpet/artifacts/2026-05-23/ppt_create/marketing-weekly-a3f7b.pptx`

- `tempfile.gettempdir()` 仅在 `config.tools.last_mile.artifact_dir` 显式留空且新 flag 关闭时使用（兼容旧行为）。
- 同名 collision 时追加 `-<short_hash>`（hash 来自 args + ts）。
- 提供 `artifact_dir` 配置项，用户可改到 `~/Documents/DeskPet/` 之类。
- 提供清理策略：保留最近 N 天，N 默认 30（M2 引入清理脚本，本期仅约定 schema）。

**`title_slug` 规则**（明确以解决文档间漂移）：

1. NFC 标准化 → 保留 Unicode letter/number/CJK；空白折叠为 `-`。
2. **保留中文与常用 emoji**（emoji 不丢，提升用户辨识度）：通过白名单允许 `\p{L}\p{N}\p{Emoji_Presentation}\p{Emoji}-_`。
3. 去除文件系统非法字符 `<>:"/\\|?*` 与控制字符。
4. 截断到 60 个 grapheme（不是 byte，避 emoji 截半）。
5. 最终长度若为 0，回退为 `untitled`。

例：`营销周报 📊` → `营销周报-📊`；`Q2 / 2026!` → `Q2-2026!`；`<<<` → `untitled`。

单测见 TDD TG-6 T6-5；手测见 MR-5。

### D5 — `ToolReceipt` + HMAC

每次 `execute_tool` 在工具函数返回后，**由 registry 强制生成 receipt**（不依赖工具自报，防止工具 bug 或恶意伪造）：

```python
@dataclass
class ToolReceipt:
    # 12 个字段，与 TDD §C.2 JSON Schema required 同源
    receipt_id: str               # uuid4
    tool_name: str
    args_hash: str                # sha256(canonical_json(args))
    started_at: str               # ISO 8601
    ended_at: str
    duration_ms: int
    ok: bool
    error_class: str | None       # 失败时分类（见 D8）；JSON 中 null
    artifacts: list[str]          # ToolArtifact.sha256 列表（可为 []）
    session_id: str
    iteration: int                # AgentLoop iteration index
    sig: str                      # HMAC-SHA256(secret, canonical_json(above 11 fields, sorted keys))
```

**sha256 计算的异步约束**（防主循环阻塞）：

- artifact 文件 ≤ 10MB：同步算 sha256。
- artifact 文件 > 10MB：丢 `loop.run_in_executor(_sha_pool, _sha256_file, path)` 线程池算。
- **receipt 必须等所有 artifact 的 sha256 完成才能签名落盘**（确保 HMAC 覆盖真值）；若 sha256 阶段超过 30s 仍未完成，receipt 标 `sha256_pending` + log warn，签名时 artifacts 字段填 `[]`，由后台补算 task 后续 patch（单独 jsonl 记录 patch event）。
- 单测见 TG-7 T7-7（100MB artifact 并发，主循环其他工具 < 50ms 调度延迟）。

**持久化**：

- HMAC secret **不裸存文件**（详见 D11）：Windows 走 DPAPI，macOS 走 Keychain，Linux 走 `libsecret` 兜底；裸文件仅在所有方案不可用时回退（首次启动 warn）。
- Receipt 进 `<user_data>/receipts/<session_id>.jsonl`，按会话滚动。
- **启动期自清理**：服务启动时扫 `receipts/`，删除 `ended_at < now - retention_days` 的整文件；HMAC key 因任何原因重生时，旧 receipts 整体迁移到 `receipts/archived/<old_key_hash_prefix>/` 并附 `INVALID_SIG_REASON.txt` 说明。
- 7 天保留期；非 PII。

**`error_class` 与 `UnmatchedClaim.reason` 的关系**（明确文档间命名混用）：

- `error_class`（D8）：是**工具调用 + verify 阶段**的总分类，5 个新增类别（`unmatched_claim` / `missing_file` / `build_error` / `test_error` / `hallucinated_claim`），写入 `ToolReceipt.error_class` 与回灌 system message 顶部的 `Classification:` 行。
- `UnmatchedClaim.reason`（TDD §C.4）：是 **VerifyGate 内部数据结构**的子分类 enum（`no_receipt` / `path_mismatch` / `sha256_mismatch` / `file_missing`），仅用于 VerifyGate → 回灌细节段（`Failures:` 列表的 `[bracket]` 标签）。
- 转换关系：所有 `UnmatchedClaim.reason` 都映射为 `error_class = "unmatched_claim"`（多对一）。两者不混用，TDD/手测文案以此为准。

### D6 — `ReceiptLedger` + `VerifyGate` + Pluggable `ClaimExtractor`

`AgentLoop` 持有 `ReceiptLedger`（in-memory，per-run）：

- 每次 `execute_tool` 返回时 append。
- LLM 输出 `end_turn` 时，触发 `VerifyGate.check(assistant_text, ledger)`：
  - 通过 `ClaimExtractor` 提取自述里的「已生成 X」「已保存到 Y」「已完成 Z」关键短语。
  - 对每条 claim 查 ledger：是否存在匹配的 receipt？路径是否对得上？sha256 是否能在 artifacts/ 里找到？
  - 任一 claim 无对账：**拒绝 end_turn**，把 `VerifyOutcome.unmatched_claims` 作为 system message 回灌，强制 LLM 第二轮。
- 配置：`verify_gate_mode ∈ {off, shadow, strict}`；shadow 仅 warn log，strict 真正拦截。

**`ClaimExtractor` strategy 模式（必须，纯 regex 抓不到 95%）**：

```python
class ClaimExtractor(Protocol):
    def extract(self, assistant_text: str, hints: ExtractHints) -> list[Claim]: ...

# 默认级联：regex 白盒 → 小 LLM 兜底
class CascadeExtractor:
    def __init__(self, primary: RegexExtractor, fallback: SmallLLMExtractor): ...
    def extract(self, text, hints):
        claims = self.primary.extract(text, hints)
        if self._suspicious(text, claims, hints):
            # 触发条件（任一）：
            #   a) 文本含完成性语义关键词集合但 regex 0 命中
            #   b) text 长度 > 80 字 且 ledger 有 receipt 但 0 claim 提取
            #   c) 显式标记 SUSPECT_HINT（如 LLM 输出含否定语气："还没"/"暂时无法"）
            claims += self.fallback.extract(text, hints)
        return dedup(claims)
```

- `RegexExtractor`：加载 `verify/claim_patterns.yaml`，**用 re2 编译（线性时间，防 ReDoS）**；YAML 走 `yaml.safe_load`（拒绝任意对象），加载前做 schema 校验；用户自定义 pattern 必须先经 re2 试编译，失败 reject。
- `SmallLLMExtractor`：调当前会话 LLM 的轻量短 call（system: "判断下面文本是否声称完成了文件/构建/部署等动作，输出 JSON claims[]"），temperature=0，max_tokens=200，硬超时 3s；失败回退到"按 regex 结果走"，不阻 end_turn。
- 度量：每次 fallback 触发 emit 一条 `verify_extractor.fallback_used` metric，beta 期监控触发率（目标 5~20% 为健康区间）。

**Verify 失败的三轮升级链（替代原"3 次硬退"）**：

| failure_count | 行为 |
|---|---|
| 1 → 2 | 回灌 system message（按 D8 schema），交还主 LLM 重试 |
| 2 → 3（即第 3 次失败的瞬间，**不再回灌主 LLM**） | 立即调度 `ephemeral_verifier_subagent(ledger_sig_valid_only, assistant_text, failed_claims)`：单轮、继承 ledger、丢弃中间 assistant 噪声、模型从 `tools.verifier.ephemeral_subagent_model` 取（默认 `"haiku"`）；只做"再判一次 claim 是否实际有对应 receipt 但 regex/LLM 漏抓"，返回 `final_verdict ∈ {pass, fail}` |
| ephemeral 判 pass | 整体 verify 通过，正常 end_turn；emit `verify.ephemeral_rescued += 1` |
| ephemeral 判 fail（或 ephemeral 自身异常） | 强制 end_turn + 标 `verify_exhausted`，前端气泡显式提示「已达验收重试上限，请人工介入」 |

> **与 N4 关系**：ephemeral subagent 是 orchestrator 内部单轮短任务，无对等通信、继承上下文、单次返回，不属 Cognition 论文反对的 multi-agent peer 协作模式。

> **ephemeral 输入的信任面（关键 - 防 ledger 注入）**：传入 `ephemeral_verifier_subagent` 的 ledger 必须 **预先经 HMAC 验签过滤**——任何 `hmac_verify(receipt) == False` 的 receipt 直接剔除（不告知 ephemeral 存在），且单独写 `verify.sig_invalid_filtered += 1` metric。这防止：(a) 恶意 LLM 通过工具调用伪造 receipt 后让 ephemeral 误判 pass；(b) HMAC key 重生后旧 receipt 残留误导。TDD T9-14b 覆盖。

**Verify 重试计数语义统一（全文以此为准）**：

- 计数维度：`failure_count`（per turn）
- 起始值：0
- 每次 verify 失败 → `failure_count += 1`
- `failure_count == 3` 时**立即**调度 ephemeral（**不**先回灌主 LLM 等第 4 轮）
- ephemeral 判 fail → 强退
- TDD T9-6/T9-14 / 手测 MR-9 与此措辞一致。

**`sha256_pending` receipt 在 VerifyGate 的行为（N4 修复）**：

- D5 允许 sha256 30s 超时后 receipt 标 `sha256_pending`，`artifacts=[]`；这种 receipt 真实存在工具调用，只是哈希未算完。
- VerifyGate 比对策略：
  - claim 的 `tool_name + path/title` 匹配 receipt → **放行**（不阻 end_turn），但 `VerifyOutcome.verifier_failures` 追加一条 `VerifierFailure(verifier="file_exists", status="skipped", reason="sha256_pending")` + warn log。
  - patch event 落盘（sha256 算完）后，后台 task 异步重核：若 sha256 与 artifact 实际不符 → 回写一条 `verify.post_hoc_sha256_mismatch` metric + 告警（不回灌当前会话，仅记录）。
- TDD 新增 T9-16 覆盖；手测无新 MR（属边角，metrics 监控即可）。

### D7 — Outcome Verifier 四件套

声明"会动文件/会跑测试/会改代码"的工具调用后，自动跑校验：

| Verifier | 触发条件 | 校验内容 | 失败处理 |
|---|---|---|---|
| `file_exists` | 任何 artifact.kind=file | path 真实存在 + size > 0 + sha256 匹配 receipt | 阻止 end_turn，回灌"声称的文件 X 不存在" |
| `git_diff` | 调用过 `file_write`/`patch_*` 类工具 | `git diff --stat <changed_files>` 输出 ≥ 1 行变更 | 回灌"没有检测到代码变更" |
| `build` | session 涉及 frontend/backend 代码改动且 `tools.verifier.run_build: true` | 仅对 `changed_files` scope 跑 `npm run build` / `pytest --collect-only` 退出码 0 | 回灌最后 20 行编译错误 + 错误分类 |
| `test` | 同上且 `tools.verifier.run_tests: true` | 执行 changed-file-scoped 测试 | 回灌最后 20 行测试失败 + 分类 |

**入参契约**：每个 verifier 接受 `(receipts: list[ToolReceipt], changed_files: list[str], cwd: str)`。`changed_files` 由 ReceiptLedger 聚合（所有 file_write/patch 类工具的 artifacts.path 去重）；build/test 命令拼接时必须用 `--testPathPattern` / `pytest <files>` 等手段把 scope 限定到 `changed_files`，**禁止全仓库扫**（节约资源 + 避免触碰用户其他正在改的代码）。

**Toolchain 缺失处理（用户机现实）**：

每个 verifier 在 `prepare()` 阶段先检测前置工具：

- `build` (frontend): `which npm` + `node_modules/` 存在
- `build` (backend): `which python` + 项目 venv 激活检测
- `test`: 同上 + `which pytest` / `vitest`
- `git_diff`: `which git` + cwd 在 git repo 内

任一前置缺失 → verifier 直接返回 `VerifierOutcome(status=skipped, reason=<missing_X>)`，**不阻 end_turn**，记 metric `verifier.skipped_due_to_missing_toolchain`。

**`build`/`test` 在 dev/CI 全开；用户运行时默认关**（即便用户开了，缺 toolchain 也按上述 skip）。Verifier 自身硬超时 60s，超时按 `verifier_timeout` 处理（log + skip，不阻 end_turn）。

### D8 — 失败反馈回灌

回灌 system message 的 schema 固定：

```
[verify-gate] iteration={N} blocked end_turn.
Failures:
  1. [unmatched_claim] "已生成 PPT" — no receipt for ppt_create found
  2. [missing_file]   "/tmp/x.pptx" — file does not exist
  3. [build_failed]   last 20 lines:
     <code block>
Classification: {test_error|build_error|missing_artifact|hallucinated_claim|...}
Next: please {call the missing tool | correct the path | fix the build error}.
```

错误分类沿用现有 `tools/error_classifier.py` 框架，新增 `verify_gate_*` 类别。

### D9 — PPT outline 预览模式（默认 off）

`ppt_create` 增加 `dry_run: bool` 参数。`dry_run=true` 时不写 .pptx，仅返回 `artifacts=[ToolArtifact(kind="text", preview=outline_markdown)]`。

LLM 可被 system prompt 引导："对于 ≥ 5 张幻灯片的 PPT，先调一次 `ppt_create(dry_run=true)` 让用户确认 outline，再调 `ppt_create(dry_run=false)` 实际生成。"——是 prompt 指导，不是硬约束。Flag：`tools.last_mile.outline_preview_default`，默认 `false`。

### D10 — Feature Flag 拓扑

`[tools.last_mile]` 段（config.toml，默认全 false）：

```toml
[tools.last_mile]
artifact_envelope = false       # D1: 是否在 result 里返回 artifacts[]
frontend_artifact_card = false  # D2: 前端用新卡片渲染
tauri_artifact_ops = false      # D3: 启用 Tauri shell 桥
default_artifact_dir = ""       # D4: 空 = 走旧 tempdir
outline_preview_default = false # D9
artifact_dir_retention_days = 30

[tools.verifier]
emit_receipts = false           # D5: 启用 receipt 生成与持久化
verify_gate_mode = "off"        # D6: off | shadow | strict
extractor_fallback_enabled = true  # D6: 小 LLM fallback（emit_receipts=true 才生效）
ephemeral_subagent_model = "haiku" # D6: 第 3 次失败救援模型
run_build = false               # D7
run_tests = false               # D7
claim_patterns_file = "verify/claim_patterns.yaml"
```

**Flag 组合 invariant 校验（启动期强校验）**：

| 非法组合 | 启动期行为 |
|---|---|
| `verify_gate_mode != "off"` 且 `emit_receipts = false` | **拒绝启动**，报 `ConfigError(VG-INVARIANT-1: verify_gate_mode='%s' requires emit_receipts=true, otherwise ledger is always empty and end_turn is always blocked.)` |
| `run_build = true` 且 `verify_gate_mode = "off"` | warn log + 自动转 shadow（build verifier 仅在 verify gate 链路里生效） |
| `frontend_artifact_card = true` 且 `artifact_envelope = false` | warn log + 自动关 frontend_artifact_card（前端无数据可渲染） |
| `tauri_artifact_ops = true` 且 `frontend_artifact_card = false` | warn log（允许，前端将不暴露按钮但 Rust command 可用，预留给单测） |
| `artifact_dir_retention_days < 1` 或 > 365 | 拒启动，报 `ConfigError(retention out of range)` |
| `verify_gate_mode != "off"` 且 `ephemeral_subagent_model` 为空字符串 | warn log + 自动填默认 `"haiku"`；启动成功 |
| `ephemeral_subagent_model` 非空但不在已注册 LLM 列表内 | 拒启动，报 `ConfigError(VG-INVARIANT-5: unknown ephemeral_subagent_model='%s')` |

校验在 `backend/config.py:_validate_flag_invariants(cfg)` 内集中实现，TG-1 T1-7 / T1-8 覆盖。

**字节级一致保证**：所有 flag off 时，`tool_result` JSON 字段顺序、字段集合（不得新增 `artifacts` 键，即便 = []）、`MessageBubble` DOM 树、receipt/verify 日志均与 main 分支当前 commit 一致（CI 用 golden file diff 验）。flag-off 时 `ToolArtifact` dataclass 不实例化，registry 直接走 legacy path。

### D11 — Receipt 私密性 + HMAC Key 存储

**HMAC key 必须经 OS 级 keystore 包装，不裸存文件**（评审 P0 I4-1）：

| 平台 | 主方案 | 兜底 |
|---|---|---|
| Windows | DPAPI（`CryptProtectData` with `CRYPTPROTECT_LOCAL_MACHINE=false`，绑定当前用户会话） | 失败时回退裸文件 + chmod-equivalent ACL + warn log |
| macOS | Keychain（`security add-generic-password`，service=`deskpet.receipt_hmac`，access group 仅限本 app） | 同上 |
| Linux | `libsecret`（secret-tool） | 同上 |

**Key 生命周期**：

- 首次启动 keystore 内无 entry：生成 32 字节随机 key + 写入 keystore + emit `hmac_key_created` event。
- 启动期 sanity echo：取 key → HMAC("ping") → 期望确定值（仅验 key 可读，不验完整签链）。
- 用户切换账号（Windows 域账号切换 / mac 不同 keychain 实例）→ 旧 key 不可读 → 触发 `hmac_key_unreadable` → 按 D5 "HMAC 重生 + 旧 receipts 归档" 流程走。
- Roaming profile 漫游：DPAPI/Keychain 本身不漫游到云端备份（默认 local-only），自动规避。

**关于裸文件兜底**：仅当三种 keystore 都不可用（极少见，如 server core 无 GUI）才走 `<user_data>/secrets/receipt_hmac.key`（0600）；此时启动 warn log 显著提示。

**Diagnostic bundle 隔离**：

- diagnostic bundle 生成器（`backend/diagnostics/`）加入 `redact_paths = ["secrets/", "receipts/"]`；keystore 内容**不可被任何子系统读取后导出**（keystore 读操作仅 receipt 模块持有权限）。
- Receipt 本身不含工具 args 明文（只存 `args_hash`），可安全进 diagnostic bundle 的"摘要"段（仅 receipt_id + tool_name + ok + duration），不进"完整 dump"段。
- 与现有 MEMORY 的"facts 表不出 diagnostic bundle"策略保持一致。

### D12 — 跨平台优先级

- **Tier 1（必须工作）**：Windows 11 — 桌宠主战场，全部 actions 实现。
- **Tier 2（best-effort）**：macOS — `open` / `open -R` / `pbcopy` 兜底实现。
- **Tier 3（不在范围）**：Linux — Tauri command 提供 stub，UI 自动隐藏 actions 按钮。

---

## §3.1 关键数据契约（IDL）

详细 JSON Schema 见 `01-TDD.md §C`。本节给"接口轮廓"快照：

```
ToolArtifact          → 工具 → registry → tool_result.artifacts[]
ToolReceipt           → registry → ReceiptLedger + .jsonl
ReceiptLedger         → AgentLoop in-memory，per-run
VerifyOutcome         → VerifyGate → system message 回灌
ClaimPattern (YAML)   → VerifyGate 加载，可热加载
```

---

## §4 工作项分解（WI）

> 命名约定：`WI-T<stage>.<seq>`，T 代表 Tool Last-Mile。

### Stage 0 — 体检 + Schema + 基线

| WI | 标题 | 输出 |
|---|------|------|
| WI-T0.1 | 工具产物路径与前端展示现状审计 | `STAGE0-audit.md`：当前 20+ 工具谁返回 path/url，前端实际怎么 render |
| WI-T0.2 | LLM 自述「已完成」关键词频次基线 | 跑 50 条 fixture 会话，统计自述短语出现频率，喂给 D6 的 ClaimPattern |
| WI-T0.3 | config schema `[tools.last_mile]` + `[tools.verifier]` 加载 | `config.py` + `config.toml` + 单测 TG-1 |

### Stage 1 — Artifact UX

| WI | 标题 | 依赖 |
|---|------|------|
| WI-T1.1 | `ToolArtifact` dataclass + `registry.execute_tool` 信封包装 + sha256 异步 | T0.3 |
| WI-T1.2 | `ppt_create` / `excel_*` / `doc_*` / `pdf_*` / `image_*` 工具改造，产 artifacts | T1.1 |
| WI-T1.3 | Tauri 4 commands（D3）+ `canonicalize_path` 6 步标准化 + Rust 单测 | T0.3 |
| WI-T1.4 | 前端 `ArtifactCard` + 5 个子组件（file/url/text/image/table） | T1.1 + T1.3 |
| WI-T1.5 | 默认保存路径策略 D4 + `title_slug` 规则 + 清理脚本 schema | T0.3 |
| WI-T1.6 | PPT outline 预览模式 D9（含 prompt 引导 + LLM 不听话回退） | T1.1 + T1.2 |
| **WI-T1.7** | **前端埋点：artifact action 点击率（open/show_in_folder/copy/save_as），匿名 metric 进 metrics.jsonl** | T1.4 |

### Stage 2 — Outcome Verifier + Receipt

| WI | 标题 | 依赖 |
|---|------|------|
| WI-T2.1 | `ToolReceipt` + HMAC + **DPAPI/Keychain/libsecret 包装**（D11） | T0.3 |
| WI-T2.2 | `registry.execute_tool` 强制产 receipt + `.jsonl` 滚动写 + 启动期自清理 + 重生归档 | T2.1 |
| WI-T2.3 | `AgentLoop.ReceiptLedger` + 注入到 `VerifyGate` | T2.2 |
| WI-T2.4 | `VerifyGate` + `ClaimExtractor` strategy（**RegexExtractor 用 re2 + safe_load + ReDoS 拒**） | T2.3 |
| **WI-T2.4b** | **`SmallLLMExtractor` 二级 fallback + `ephemeral_verifier_subagent` 救援链** | T2.4 |
| WI-T2.5 | 4 个 outcome verifier（D7）+ `changed_files` scope + **toolchain 前置检测** + 失败反馈回灌（D8） | T2.4 |

### Stage 3 — 端到端 + 联合验收

| WI | 标题 | 依赖 |
|---|------|------|
| WI-T3.1 | 端到端"PPT 场景"打通：用户说话 → 生成 → 卡片 → 点开 → outcome 校验 → 完成 | Stage 1 + Stage 2 |
| WI-T3.2 | 跨平台 macOS Tier 2 验证 + Linux stub | T3.1 |
| WI-T3.3 | 字节级一致回归 golden file（所有 flag off + dict key 缺失校验） | 全部 |
| WI-T3.4 | beta 100 内测灰度（10% → 50% → 100%） | T3.3 |
| **WI-T3.5** | **`scripts/acceptance/last_mile_*.py`：把 MR-0/MR-8/MR-11/MR-17/MR-19 自动化，CI 在 PR 上跑（UI 部分 windows-mcp 录像 / Tauri test runner 回放）；对齐 Spec Kit acceptance 模式** | T3.3 |

---

## §5 度量

| 指标 | 现状 | 目标 | 采集方式 |
|---|---|---|---|
| 工具产物用户点击率（开/显示文件夹/复制） | 0%（无按钮） | ≥ 60% on PPT/Excel/Doc 场景 | **WI-T1.7 前端埋点**（action_id + tool_name + ok）→ metrics.jsonl（脱敏后，无 path）；手测 MR-22 验证；TG-5 T5-7 验事件触发 |
| `verify_extractor.fallback_used` 触发率（健康区间）| N/A | **5% ~ 20%** （beta 1 月观测；< 5% 说明 regex 过宽；> 20% 说明默认 patterns 覆盖不足 → 需迭代 yaml）| WI-T2.4b emit metric；监控告警 |
| `verify.ephemeral_rescued` 触发率（健康区间）| N/A | **< 3%**（beta 1 月观测；> 3% 说明 extractor 双层依然漏抓多，需 patterns + LLM prompt 同步迭代） | WI-T2.4b emit metric；监控告警 |
| `verify.sig_invalid_filtered` 累计 | N/A | **= 0**（任何非零都是 anomaly，需立即排查 HMAC key/篡改）| WI-T2.4b emit metric；任一非 0 即 P1 alert |
| fake-completion 自动抓获率 | 0% | ≥ 95% on test fixture（50 条人为构造的 fake claim） | TG-9 自动化测试 |
| 端到端 PPT 场景 p95 延迟 | 当前 X ms | X + ≤ 800 ms（receipt/verify 开销预算） | 性能回归测试 |
| flag-off 字节级一致 | N/A | 100% | TG-12 golden diff |
| 用户报告"PPT 生成但找不到文件" beta 期工单数 | 历史 N | ≤ 1（30 天） | 反馈面板 |

---

## §6 风险表 R1-R10

| ID | 风险 | 影响 | 缓解 |
|---|------|------|------|
| R1 | VerifyGate 的 ClaimPattern 误判（把正常对话当 claim） | 用户体验劣化，无限回路 | shadow 模式先跑 ≥ 2 周，统计误判率 < 1% 才升 strict；每会话 verify 重试硬限 3 次 |
| R2 | HMAC key 文件丢失或权限错误 | receipt 全部失效 | 启动时检查 + 自动重生（旧 receipt 标 `sig_invalid`） |
| R3 | artifact_dir 占盘失控 | 用户磁盘满 | D10 的 retention_days + 启动时 size warning（> 1GB） |
| R4 | LLM 自述用了 ClaimPattern 没覆盖的措辞 | fake-completion 漏抓 | T0.2 用真实会话基线 + beta 期热更新 patterns |
| R5 | 工具 args 含敏感内容（如 facts 表 dump）进 args_hash 还原 | hash 单向，但担心彩虹表 | 仅 hash 不存原文；高熵 args 自动跳过（敏感字段白名单） |
| R6 | Windows path 白名单大小写不一致拒掉合法路径 | 用户点不开自己生成的文件 | 路径标准化（`os.path.normcase` + `realpath`）后比对 |
| R7 | Outcome verifier 的 `pytest` 跑挂 dev 机器 | 开发者抱怨 | 默认 off；开启时仅跑 changed-file-scoped；硬超时 60s |
| R8 | 前端 ArtifactCard 在 100+ artifacts 会话里渲染卡顿 | UX 退化 | 虚拟滚动（react-window）；折叠超过 5 个的展示组 |
| R9 | tool_result JSON 多了 artifacts 字段触发下游 schema 校验失败 | 现有 e2e 测试红 | flag 兜底 + golden file 回归 + dual-write 1 个版本 |
| R10 | receipt 写盘 I/O 拖慢主循环 | 延迟超预算 | 异步 fire-and-forget；批量 flush；磁盘满时降级到内存；**与 memory-v2 facts vector backfill 共存时写盘预算放宽到 < 20ms p95（见 §F）**；可选拆 receipts/ 到独立目录 |
| R11 | HMAC key 在多用户 Windows 机器（共享 PC / 域账号切换 / roaming profile）失效 | receipt 全部 sig_invalid | D11：DPAPI 包装（绑当前用户）+ 启动期 sanity echo + 切账号自动重生 + 旧 receipts 归档 |
| R12 | 用户卸载/重装后 `<user_data>` 迁移 / receipts/ 残留 | 累积冗余 + 旧 sig_invalid 无意义 | uninstaller 询问是否保留 user_data；重装首次启动检测旧 receipts 不可签 → 整目录归档到 `receipts/archived/pre_reinstall/`；MR-23 验证 |
| R13 | 用户机缺 npm/pytest/git toolchain → build/test verifier 自身报错被分类成 `build_error` 回灌，LLM 永远修不好 | LLM 死循环到 verify_exhausted | D7 `prepare()` 阶段 `which` 前置检测，缺则 `status=skipped` 不阻 end_turn；MR-24 验证；metric `verifier.skipped_due_to_missing_toolchain` 监控 |

---

## §7 回退路径

| 触发条件 | 一键回退操作 |
|---|---|
| beta 期发现严重 UX 退化 | `[tools.last_mile].frontend_artifact_card = false` —— 前端立即恢复旧卡片 |
| VerifyGate 误判率 > 1% | `verify_gate_mode = "off"` |
| Outcome verifier 触发误阻 | `run_build = false; run_tests = false` |
| receipt 写盘故障 | `emit_receipts = false`；旧 receipt 文件归档 |
| 整体灾难 | `git revert <merge-commit>` + flag 配置回 main 默认 |
| HMAC keystore 整片不可用（DPAPI 损坏 / Keychain 损坏） | `emit_receipts = false` + 自动降级到裸文件兜底（D11 末段） |
| Flag 组合 invariant 报 ConfigError 启动失败 | 启动期错误信息明确指出冲突 flag 对（D10 表）；用户改 config.toml 任一 flag 即可恢复；CI 必跑 invariant matrix |

**每个 WI 在 commit message 里必须注明所属 flag** + **是否触发 invariant matrix CI**。

---

## §8 验收清单（PRD → 待 TDD/手测兑现）

- [ ] 全部 12 个决策 D1-D12 有对应 WI 落地
- [ ] §5 度量全部上线（含基线对照）
- [ ] §6 风险全部有缓解或 owner
- [ ] TDD（`01-TDD.md`）TG-0 ~ TG-12 全绿
- [ ] 手测（`02-manual-test-cases.md`）MR-0 ~ MR-N 全绿（MR-0 一票否决）
- [ ] opus-4.7 架构子代理评估通过（可执行性 / 兼容性 / 2026 对齐 / 风险回退 四维度）
- [ ] beta 100 灰度 30 天，反馈面板"找不到文件"工单 ≤ 1

---

## 附录 A — 与 memory-upgrade 的关系

| 维度 | 关系 |
|------|------|
| 代码层 | 完全正交。Receipt 写盘路径与 facts 表分离；agent_loop 改动点不重叠（memory-v2 改 retriever/assembler，本期改 dispatch 收尾 + verify gate） |
| Flag 层 | 独立 flag 树，互不依赖 |
| Merge 顺序 | 不强约束。建议 memory-upgrade 先（已 in flight），本 PRD 在其 merge 后开工，避免 receipt 写盘和 facts vector backfill 抢 I/O |

## 附录 B — 与 2026 业界共识的对齐

| 业界做法 | 本 PRD 落点 |
|---|---|
| Spec Kit / Kiro：spec 带 acceptance.sh | 02-manual-test-cases.md + TG-12 golden file + **WI-T3.5 `scripts/acceptance/last_mile_*.py`** |
| outcome-based verification | D7 四件套（带 `changed_files` scope + toolchain skip） |
| HMAC tool receipts (NABAOS / arXiv 2603.10060) | D5 ToolReceipt + D11 DPAPI/Keychain 包装 |
| orchestrator + ephemeral subagent | D6 第 3 次 verify 失败前调度 `ephemeral_verifier_subagent`（单轮、继承 ledger、无对等通信） |
| context reset > compaction | 不在本 PRD 范围（属 harness 层，由 codingsys 侧另行处理） |
| ClaimExtractor pluggable strategy（regex/NLI/LLM 多实现） | D6 `ClaimExtractor` Protocol + 默认 `CascadeExtractor`（regex + small LLM） |

## 附录 C — 已识别但本期 deferred 的架构优化

来自 opus-4.7 架构评审 §跨维度优化建议 O1-O5；本期不做但记录在案，避免遗忘：

| ID | 建议 | 工作量 | Defer 理由 |
|---|---|---|---|
| O1 | Receipt 改 OpenTelemetry span（attested span 天然带签 + 进 trace 查看器） | ~3 人日 | 引入新依赖（OTLP collector），与单机桌宠场景过度；待未来对接观测平台时复用 |
| O2 | `ClaimExtractor` interface 抽完整 strategy（regex/nli/llm 三独立实现） | ~1 人日 | D6 已建 Protocol，但只提供 Regex+CascadeExtractor 两实现；NLI 实现待第三方模型选型成熟（M2）|
| O3 | ArtifactCard actions 走 capability negotiation（工具 declare + Tauri declare） | ~1 人日 | D2 当前 kind-based 默认 + 工具显式覆盖已可满足本期；待 macOS/Linux Tier 升级时再做交集协商 |
| O4 | （已采纳为 WI-T3.5）— | — | 不再 defer |
| O5 | OS keystore + 启动 sanity HMAC echo | ~2 人日 | （已采纳为 D11） — 启动 sanity echo 已纳入 D11，full keystore 包装为本期一等公民 |
