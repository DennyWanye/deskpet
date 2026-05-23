# TDD — DeskPet 工具调用 Last-Mile 升级

- **配套 PRD**：`00-PRD.md`
- **日期**：2026-05-23
- **状态**：v2.1（按二轮评审 5 N 项追加用例）
- **总测试组**：TG-0 ~ TG-12（13 组，约 95+ 用例）
- **变更日志**：
  - v2：TG-1 加 T1-7/T1-8（flag invariant）；TG-2 加 T2-5b（dict 不 emit 键）；TG-3 加 T3-6（outline LLM 不听话）；TG-4 加 T4-7/T4-8/T4-9（UNC/mapped/8.3）；TG-7 加 T7-7（100MB 并发不阻塞 + DPAPI 路径 T7-8）；TG-9 加 T9-11（LLM fallback）/T9-12（re2）/T9-13（ReDoS pattern 拒）/T9-14（ephemeral subagent）；TG-10 改 T10-5 强制 `changed_files`，加 T10-8（toolchain skip）；§C.2 字段对齐 12 项；§C.3 加 re2 + safe_load；§C.4 明示 reason vs error_class；§F 性能预算补充

---

## §A 测试策略总览

### A.1 三层金字塔

| 层 | 工具 | 覆盖目标 |
|---|---|---|
| 单元 | `pytest` / `vitest` / `cargo test` | 每个新增 dataclass / 函数 / 组件 |
| 集成 | `pytest -m integration` / Tauri test | execute_tool + receipt + ledger 联动；前端组件 + ws.ts 联动 |
| E2E | `scripts/e2e_tool_last_mile.py` + `windows-mcp` 子代理 | 真实启动后端 + Tauri + 真实 LLM（mock providers/key）+ 真实文件系统 |

### A.2 字节级一致基线（关键）

CI 跑 `tests/golden/tool_result_*.json`：

- 跑 fixture 会话（无 flag）→ 抓 `tool_result` 完整 JSON dump → 与 main 分支 commit `<MERGE_BASE>` 对比。
- 任一字段名 / 字段顺序 / value 变更 → 测试红。
- 是 G5 / MR-0 一票否决的自动化兜底。

### A.3 Mock 策略

- LLM 调用：用 `tests/fake_llm.py`，按脚本回放（与 memory-v2 smoke 一致）。
- HMAC secret：`tests/fixtures/test_hmac.key`（确定性 32 字节）。
- 文件系统：`tmp_path` fixture（pytest 内置）。
- 时间戳：注入 `clock=fixed_clock("2026-05-23T00:00:00Z")` 让 receipt 可重放。

---

## §B 测试组 TG-0 ~ TG-12

### TG-0 — Smoke 体检（沿用 memory-v2 模式）

| 用例 | 期望 |
|---|---|
| T0-1 | `from tools.artifact import ToolArtifact` 不报错 |
| T0-2 | `from tools.receipt import ToolReceipt, hmac_sign, hmac_verify` 不报错 |
| T0-3 | `from agent.verify_gate import VerifyGate, ClaimPattern` 不报错 |
| T0-4 | `registry.execute_tool` 签名兼容（不破现 7 个 caller） |
| T0-5 | Tauri command `artifact_open` 注册成功（Rust `cargo test --no-run` 通过） |

**全部新建模块在 PRD 落地前需先建空 stub + 此 smoke，避免接口腐烂。**

### TG-1 — Config Schema

| 用例 | 期望 |
|---|---|
| T1-1 | 无 `[tools.last_mile]` 段时，`cfg.tools.last_mile.artifact_envelope is False` 等 7 项默认 |
| T1-2 | `[tools.verifier].verify_gate_mode = "shadow"` 解析正确 |
| T1-3 | `verify_gate_mode` 非 `"off"/"shadow"/"strict"` 时报 `ValueError` 并 log |
| T1-4 | `artifact_dir_retention_days = -1` 拒绝加载 |
| T1-5 | 未知 key 不 crash（沿用现有 `_load_section` 行为） |
| T1-6 | `AppConfig()` 默认（无 config 文件）含可用的 `tools.last_mile` / `tools.verifier` |
| **T1-7** | **flag invariant 矩阵**：`verify_gate_mode != "off"` 且 `emit_receipts = false` → `_validate_flag_invariants` 抛 `ConfigError(VG-INVARIANT-1)`，启动失败 |
| **T1-8** | invariant 自动修复：`run_build=true` + `verify_gate_mode="off"` → warn log + 配置自动转 shadow；启动成功 |
| **T1-9** | `frontend_artifact_card=true` + `artifact_envelope=false` → warn log + 自动关闭前者 |
| **T1-10** | `artifact_dir_retention_days = 0`（或 > 365）→ `ConfigError` 拒启动 |
| **T1-11** | `verify_gate_mode="strict"` + `ephemeral_subagent_model=""` → 启动成功 + warn log + 实际生效 `"haiku"`；同配置但 `ephemeral_subagent_model="unknown_model_x"` → 拒启动 `ConfigError(VG-INVARIANT-5)` |

### TG-2 — `ToolArtifact` 信封 + registry 包装

| 用例 | 期望 |
|---|---|
| T2-1 | `ToolArtifact(kind="file", path="/x.pptx", title="x.pptx").to_dict()` 字段顺序固定 |
| T2-2 | `kind="file"` 但 `path is None` → `ValueError` |
| T2-3 | `kind="url"` 但 `url is None` → `ValueError` |
| T2-4 | `preview` 超 2KB 自动截断 + 末尾标记 `…(truncated)` |
| T2-5 | flag `artifact_envelope=False` 时，`execute_tool` 返回不含 `artifacts` 字段（字节级与现状一致） |
| **T2-5b** | 字典级精确校验：flag off 时 `tool_result.keys()` 不含 `"artifacts"`（不是空列表，是缺键）；用 `assert "artifacts" not in result_dict` |
| T2-6 | flag on 时，工具未返回 artifacts → registry 自动从 `result.path` 推断 1 个 `kind="file"` artifact（向后兼容） |
| T2-7 | 两个工具并发返回，artifacts 不串号 |
| **T2-8** | 大文件 sha256 异步：100MB artifact 触发 `run_in_executor`，registry 不阻塞（同时跑另一个 < 1MB 工具，调度延迟 < 50ms） |
| **T2-9** | sha256 30s 超时 → receipt 标 `sha256_pending`，artifacts 字段 `[]`，patch event 后续追加 |

### TG-3 — 工具改造（ppt/excel/doc/pdf/image）

| 用例 | 期望 |
|---|---|
| T3-1 | `ppt_create` flag on 时返回 `artifacts=[ToolArtifact(kind="file", mime="application/vnd.openxmlformats-officedocument.presentationml.presentation", ...)]` |
| T3-2 | `ppt_create(dry_run=True)` 返回 `kind="text"` artifact，preview 含 outline markdown，**不写 .pptx** |
| T3-3 | `python-pptx` 缺失时返回 `artifacts=[]` + `result.ok=False` + `error_class="missing_dependency"`（不再静默 markdown fallback） |
| T3-4 | `excel_create` / `doc_create` / `pdf_create` / `image_generate` 同 T3-1 模式 |
| T3-5 | artifacts 路径全部位于 `tools.last_mile.artifact_dir` 子树内 |
| **T3-6** | `outline_preview_default=true` 但 LLM 未按引导调 `dry_run=true`，直接 `dry_run=false` → registry/agent_loop 在 system message 重述引导一次（仅一次，避免无限提醒）；若 LLM 第二轮仍直接生成，放行（用户自由意志优先） |

### TG-4 — Tauri Artifact Ops（Rust 单测）

| 用例 | 期望 |
|---|---|
| T4-1 | `is_allowed_path` 接受 `<user_data>/artifacts/foo.pptx` |
| T4-2 | `is_allowed_path` 拒绝 `C:\Windows\System32\cmd.exe` |
| T4-3 | `is_allowed_path` 拒绝符号链接跳出白名单（解析 `realpath` 后比对） |
| T4-4 | 大小写不敏感比对（Windows）通过 `normcase` |
| T4-5 | `artifact_open` 路径不存在 → 返回 `Err("not_found")`，不调系统 shell |
| T4-6 | `artifact_save_as` 用户取消 picker → 返回 `Ok(None)`，不复制 |
| **T4-7** | **UNC path 拒**：`\\server\share\foo.pptx` / `\\?\UNC\server\share\foo` / `\\.\C$\Windows\...` 三种形式全部 `Err("unc_not_allowed")` |
| **T4-8** | **Mapped drive 解析**：测试机 `Z:` → `\\server\share`，传入 `Z:\foo.pptx` 反查到 UNC → 同 T4-7 拒 |
| **T4-9** | **8.3 短文件名展开**：`C:\PROGRA~1\DeskPet\artifacts\foo.pptx` 用 `GetLongPathNameW` 展开为 `C:\Program Files\DeskPet\artifacts\foo.pptx`，然后比对白名单（若不在 → 拒；在 → 允许） |
| **T4-10** | symlink 跳出白名单：`<user_data>/artifacts/escape.lnk` 指向 `C:\Windows\System32\cmd.exe` → 经 `GetFinalPathNameByHandleW` 解析后比对 → 拒 |

### TG-5 — 前端 `ArtifactCard`

| 用例 | 期望 |
|---|---|
| T5-1 | `FileArtifactCard` 渲染 title + size + mime icon |
| T5-2 | `actions=[open, show_in_folder]` 渲染 2 按钮，点击触发 `invoke('artifact_open', {path})` |
| T5-3 | `kind="url"` 走 `UrlArtifactCard`（外链 + 安全 `rel="noopener noreferrer"`） |
| T5-4 | `kind="text"` 走 `TextArtifactCard`（markdown 渲染 + 折叠） |
| T5-5 | result 不含 `artifacts` 字段 → 回落到旧 `ToolResultCard`（DOM 树字节级与 main 一致，用 snapshot test） |
| T5-6 | 100+ artifacts 用虚拟滚动，render time < 16ms |
| **T5-7** | **action 点击埋点**：点 open / show_in_folder / copy_path / save_as 各发送 1 条 `metric_event(name="artifact_action", action_id, tool_name, ok)`；事件不含 path（脱敏验：grep payload 无 `\\` 或 `/` 路径片段） |

### TG-6 — 默认保存路径策略

| 用例 | 期望 |
|---|---|
| T6-1 | flag on + `artifact_dir=""` → fallback to `<user_data>/artifacts/<YYYY-MM-DD>/<tool>/` |
| T6-2 | 用户配置 `artifact_dir="~/Documents/DeskPet"` → 路径展开 + 创建 |
| T6-3 | 同名 collision → `name-<8hex>.ext` |
| T6-4 | 多并发写不同步 short_hash 不冲撞（10 并发 ppt_create，无覆盖） |
| T6-5 | `title_slug` 规则全测：`"营销周报 📊"` → `"营销周报-📊"`；`"Q2 / 2026!"` → `"Q2-2026!"`；`"<<<"` → `"untitled"`；`"a" * 200` → 截到 60 grapheme；NFC 组合字符 `é`(U+00E9) vs `e`+combining acute(U+0065 U+0301) → 同结果 |

### TG-7 — `ToolReceipt` + HMAC

| 用例 | 期望 |
|---|---|
| T7-1 | `ToolReceipt` 字段顺序固定，`hmac_sign` 输出确定（fixed clock + fixed key） |
| T7-2 | `hmac_verify` 篡改 `tool_name` → False |
| T7-3 | `hmac_verify` 篡改 `sig` → False |
| T7-4 | `args_hash` 是 canonical JSON 的 sha256，key 顺序不敏感 |
| T7-5 | HMAC key 不存在 → 经 DPAPI/Keychain/libsecret 链路生成；keystore 不可用三回合后才回退裸文件（warn log） |
| T7-6 | HMAC key 权限不对（裸文件 fallback 路径）→ 警告 log，仍可用 |
| **T7-7** | **大文件 sha256 不阻塞主循环**：构造 100MB artifact，并发触发另一个轻量工具调用，registry `execute_tool` 调度延迟 p95 < 50ms |
| **T7-8** | **DPAPI 路径 mock**：mock keystore 接口（`fake_keystore.py`），覆盖：成功取 / 不存在自动写入 / 取失败回退裸文件路径；启动期 sanity echo 失败 → log warn + 自动重生 + receipts 归档（mock 文件系统验证 `receipts/archived/` 创建） |
| **T7-9** | sha256 30s 超时 → receipt 标 `sha256_pending`，patch event 写入 `<session_id>.patches.jsonl` |

### TG-8 — `ReceiptLedger` 写盘

| 用例 | 期望 |
|---|---|
| T8-1 | `execute_tool` 成功 → `<user_data>/receipts/<session_id>.jsonl` 多 1 行 |
| T8-2 | `execute_tool` 失败 → receipt.ok=False，error_class 填充 |
| T8-3 | 写盘异常（磁盘满） → fallback 内存 + log error，主循环不阻塞 |
| T8-4 | flag `emit_receipts=False` → 不写盘，ledger 仍 in-memory 累积（供 verify gate） |
| T8-5 | receipt 不含 args 明文（仅 args_hash） |
| T8-6 | diagnostic bundle 生成时，receipts/ 目录不被打包（grep 验） |

### TG-9 — `VerifyGate` Claim 对账（核心）

| 用例 | 期望 |
|---|---|
| T9-1 | shadow 模式：LLM 自述「已生成 PPT」+ ledger 有 ppt_create receipt → 通过，无 warn |
| T9-2 | shadow 模式：LLM 自述「已生成 PPT」+ ledger 无对应 receipt → 通过 + warn log（不阻断） |
| T9-3 | strict 模式：同 T9-2 → 拒 end_turn，回灌 system message（schema 同 D8） |
| T9-4 | strict 模式：LLM 自述「已为您保存到 /tmp/x.pptx」+ receipt 显示路径在 `/artifacts/...` → 拒（路径不匹配） |
| T9-5 | strict 模式：sha256 不匹配 → 拒 |
| T9-6 | 一会话 verify 重试 ≥ 3 次仍失败 → 强制 end_turn + 标记 `verify_exhausted`（防无限回路） |
| T9-7 | ClaimPattern 热加载：修改 yaml → 5s 内生效 |
| T9-8 | 多 claim 部分匹配：3 个 claim、1 个失败 → 仅回灌失败的 |
| T9-9 | 中英文混合 claim（"已为您生成 marketing.pptx"）正确提取实体 |
| T9-10 | **Fake-completion 抓获率基准**：50 条人为构造 fake claim fixture（**分桶要求**：纯中文 ≥ 10 / 纯英文 ≥ 10 / 中英混合 ≥ 10 / 同义改写「搞定了」「PPT 出炉」≥ 10 / 否定句「还没」「暂时无法」≥ 10），strict 模式抓获 ≥ 47 (95%)；按桶报告抓获率 |
| **T9-11** | **小 LLM fallback 兜底**：构造 10 条 regex 必然漏抓的 claim（同义改写 + 隐喻），开 `extractor_fallback_enabled=true` 抓获 ≥ 7；关闭则抓获 = 0；fallback 触发 emit metric `verify_extractor.fallback_used` |
| **T9-12** | **re2 编译**：所有 ClaimPattern 用 `google-re2` 编译；用 `re2` 不支持的 PCRE 特性（如 backreference）应 reject pattern（不 fallback 到 Python re） |
| **T9-12b** | **默认 yaml 正向加载**：仓库 `verify/claim_patterns.yaml`（出厂默认）必须 **100% pattern 全部 re2 编译通过**，加载后 `len(patternstore) == len(yaml.patterns)`；任何默认 pattern reject 即测试红（防 ship 后默认 patterns 全 reject 静默失效） |
| **T9-13** | **ReDoS pattern 拒**：热加载 yaml 含 `(a+)+$` 类 catastrophic backtracking pattern → re2 编译报错 → patternstore reject + log error；服务不 crash，旧 patterns 继续生效 |
| **T9-14** | **ephemeral_verifier_subagent 救援链**：mock 主 LLM 始终 fake claim 不调工具；`failure_count` 走到 1→2 回灌；第 3 次失败的瞬间（不再回灌主 LLM）调度 `ephemeral_verifier_subagent`（mock 返回 `pass`）→ 整体 verify 通过 end_turn + metric `verify.ephemeral_rescued`；另一组 mock 返回 `fail` → 强退 + `verify_exhausted` |
| **T9-14b** | **ephemeral 输入信任面**：构造 ledger 含 3 条 receipt，其中 1 条 `hmac_verify == False`（手动改字节）；触发 ephemeral → mock 检查接收的 ledger 只有 2 条 sig-valid receipt（被剔除的不可见）；emit `verify.sig_invalid_filtered = 1` |
| **T9-15** | YAML `safe_load`：注入 `!!python/object/apply` 类危险节点 → reject pattern file |
| **T9-16** | **sha256_pending receipt 在 VerifyGate 中放行**：mock 100MB artifact 触发 sha256 超时 → receipt 标 `sha256_pending`；claim 的 path/title 匹配该 receipt → VerifyGate 放行 end_turn，`VerifyOutcome.verifier_failures` 含 1 条 `status=skipped, reason="sha256_pending"`；后台 task 算完 sha256，若与声明不符 emit `verify.post_hoc_sha256_mismatch` |

### TG-10 — Outcome Verifier 四件套

| 用例 | 期望 |
|---|---|
| T10-1 | `file_exists`：声明 path 不存在 → 阻 end_turn |
| T10-2 | `file_exists`：path 存在但 size=0 → 阻 end_turn |
| T10-3 | `file_exists`：sha256 不匹配 receipt → 阻 end_turn |
| T10-4 | `git_diff`：调过 file_write 但 `git diff --stat` 空 → 阻 |
| T10-5 | `build`：注入 ts 错误到 `changed_files=["src/foo.ts"]` → verifier 拼接 `tsc --noEmit src/foo.ts`（**或等价 file-scoped 命令**）失败 → 回灌末 20 行 + 分类 `build_error`；**断言未改动文件 `src/bar.ts` 的 type error 不在回灌内容里**（scope 隔离） |
| T10-6 | `test`：注入失败用例到 `tests/test_foo.py` → verifier 跑 `pytest tests/test_foo.py` 失败 → 回灌末 20 行 + 分类 `test_error`；断言其他测试文件未跑（用 `--collect-only` 验 collected 数） |
| T10-7 | verifier 自身超时（60s）→ 标 `verifier_timeout`，不阻 end_turn（防自杀） |
| **T10-8** | **toolchain 缺失 skip**：mock `shutil.which("npm")` 返回 None → `build` verifier `prepare()` 返回 `status=skipped, reason="missing_npm"`；不阻 end_turn；emit metric `verifier.skipped_due_to_missing_toolchain{tool="npm"}` |
| **T10-9** | `git_diff` verifier 在非 git 目录：`which git` 存在但 cwd 不在 repo → `status=skipped, reason="not_a_git_repo"` |

### TG-11 — 失败反馈回灌格式

| 用例 | 期望 |
|---|---|
| T11-1 | 回灌 system message 严格匹配 D8 schema |
| T11-2 | 错误分类沿用 `error_classifier.py` 现有标签 + 5 个新增（`unmatched_claim`/`missing_file`/`build_error`/`test_error`/`hallucinated_claim`） |
| T11-3 | 第二轮 LLM 输入 messages 末尾追加 system message，**不混入 user role** |
| T11-4 | 回灌后 LLM 重试，若再失败：迭代计数 +1，到达 3 次硬限触发 T9-6 |

### TG-12 — 端到端 + 字节级一致

| 用例 | 期望 |
|---|---|
| T12-1 | **flag 全 off** 跑 fixture「生成 PPT」会话 → 与 main commit 字节级一致（golden diff） |
| T12-2 | flag 全 off 跑 50 条 fixture 会话 → 字节级一致 |
| T12-3 | flag 全 on 跑 fixture 会话 → 用户最终看到 ArtifactCard + receipt 写盘 + verify 通过 |
| T12-4 | flag 全 on，注入 fake LLM 输出「已生成 PPT」但不调工具 → strict 模式拒，回灌后 LLM 调工具，最终通过 |
| T12-5 | flag 全 on，端到端 p95 延迟 ≤ 基线 + 800ms（重复 100 次） |

---

## §C 数据契约 JSON Schema（节选）

### C.1 `ToolArtifact`

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "required": ["kind", "title", "created_at"],
  "properties": {
    "kind": {"enum": ["file", "url", "text", "image", "table"]},
    "path": {"type": ["string", "null"]},
    "url":  {"type": ["string", "null"], "format": "uri"},
    "mime": {"type": ["string", "null"]},
    "title": {"type": "string", "minLength": 1, "maxLength": 200},
    "preview": {"type": ["string", "null"], "maxLength": 2048},
    "size_bytes": {"type": ["integer", "null"], "minimum": 0},
    "sha256": {"type": ["string", "null"], "pattern": "^[0-9a-f]{64}$"},
    "created_at": {"type": "string", "format": "date-time"},
    "actions": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["id"],
        "properties": {
          "id":    {"enum": ["open", "show_in_folder", "copy_path", "save_as", "preview"]},
          "label": {"type": "string"}
        }
      }
    }
  },
  "allOf": [
    {"if": {"properties": {"kind": {"const": "file"}}}, "then": {"required": ["path"]}},
    {"if": {"properties": {"kind": {"const": "url"}}},  "then": {"required": ["url"]}}
  ]
}
```

### C.2 `ToolReceipt`

**字段数量与 PRD §3 D5 严格同源（12 项 required，含显式 nullable）**：

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "additionalProperties": false,
  "required": ["receipt_id","tool_name","args_hash","started_at","ended_at",
               "duration_ms","ok","error_class","artifacts",
               "session_id","iteration","sig"],
  "properties": {
    "receipt_id": {"type": "string", "format": "uuid"},
    "tool_name":  {"type": "string"},
    "args_hash":  {"type": "string", "pattern": "^[0-9a-f]{64}$"},
    "started_at": {"type": "string", "format": "date-time"},
    "ended_at":   {"type": "string", "format": "date-time"},
    "duration_ms":{"type": "integer", "minimum": 0},
    "ok":         {"type": "boolean"},
    "error_class":{
      "description": "见 PRD D5：与 UnmatchedClaim.reason 是两套不同 enum；error_class 是顶层分类",
      "type": ["string", "null"],
      "enum": [null, "missing_dependency", "permission_denied", "timeout",
               "circuit_open", "tool_internal_error",
               "unmatched_claim", "missing_file", "build_error", "test_error",
               "hallucinated_claim", "sha256_pending"]
    },
    "artifacts":  {"type": "array", "items": {"type": "string", "pattern": "^[0-9a-f]{64}$"}},
    "session_id": {"type": "string"},
    "iteration":  {"type": "integer", "minimum": 0},
    "sig":        {"type": "string", "pattern": "^[A-Za-z0-9+/=]+$"}
  }
}
```

**签名输入**：`hmac_sha256(secret, canonical_json(receipt without 'sig' field, sorted_keys=True))`。canonical JSON 使用 `json.dumps(..., sort_keys=True, separators=(',',':'), ensure_ascii=False)`，确保跨语言可重现。

### C.3 `ClaimPattern` YAML

**加载约束**：
- `yaml.safe_load`（拒任意对象 / `!!python/...` 注入）
- Schema 校验（jsonschema）通过后才生效
- 每条 regex 用 **google-re2** 编译；不支持的 PCRE 特性（backreference / lookahead 在 re2 中受限）→ pattern reject + log error
- 编译失败的 pattern 不进 patternstore；其他 pattern 继续有效
- 热加载：watchdog 监听文件变更，5s 内重 load；reload 失败保留旧 patternstore

```yaml
# verify/claim_patterns.yaml
version: 1
patterns:
  - id: zh_generated_file
    regex: '已(?:为您)?生成(?:了)?\s*(?P<title>[^，。\s]+\.(pptx|xlsx|docx|pdf|png|jpg))'
    tool_hint: ["ppt_create", "excel_create", "doc_create", "pdf_create", "image_generate"]
    artifact_kind: file
  - id: zh_saved_to_path
    regex: '已(?:保存|输出)(?:到)?\s*(?P<path>[A-Z]:\\[^\s，。]+|/[^\s，。]+)'
    artifact_kind: file
  - id: en_created_file
    regex: '(?:I (?:have )?created|Generated)\s+(?P<title>\S+\.(pptx|xlsx|docx|pdf|png|jpg))'
    tool_hint: ["ppt_create", "excel_create", "doc_create", "pdf_create", "image_generate"]
    artifact_kind: file
  - id: en_saved_to_path
    regex: 'saved (?:it|the file) (?:to|at)\s+(?P<path>[A-Z]:\\\S+|/\S+)'
    artifact_kind: file
```

**JSON Schema for yaml**：

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["version", "patterns"],
  "properties": {
    "version": {"const": 1},
    "patterns": {
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["id", "regex", "artifact_kind"],
        "properties": {
          "id":            {"type": "string", "pattern": "^[a-z][a-z0-9_]{1,40}$"},
          "regex":         {"type": "string", "maxLength": 500},
          "tool_hint":     {"type": "array", "items": {"type": "string"}},
          "artifact_kind": {"enum": ["file", "url", "text", "image", "table"]}
        }
      }
    }
  }
}
```

### C.4 `VerifyOutcome`

```python
@dataclass
class VerifyOutcome:
    passed: bool
    claims_extracted: int
    unmatched_claims: list[UnmatchedClaim]
    verifier_failures: list[VerifierFailure]
    elapsed_ms: int
    extractor_used: Literal["regex", "regex+llm_fallback", "ephemeral_subagent"]
    failure_count: int   # 与 PRD D6 "失败计数 = 3 即强退" 一致

@dataclass
class UnmatchedClaim:
    pattern_id: str
    raw_text: str
    expected_kind: str
    expected_path_or_title: str | None
    # 与 ToolReceipt.error_class 是两套不同 enum（详见 PRD §3 D5 末段）
    # 转换：所有 reason 都映射为 error_class="unmatched_claim"（多对一）
    reason: Literal["no_receipt", "path_mismatch", "sha256_mismatch", "file_missing"]

@dataclass
class VerifierFailure:
    verifier: Literal["file_exists", "git_diff", "build", "test"]
    status: Literal["failed", "skipped", "timeout"]
    reason: str                       # 当 skipped：missing_npm / missing_pytest / not_a_git_repo / ...
    log_tail: str | None              # 仅 failed 时填，末 20 行
    error_class: str | None           # build_error / test_error / missing_file / null
```

---

## §D 测试夹具

| Fixture | 路径 | 用途 |
|---|---|---|
| `fake_llm.py` | tests/ | 按脚本回放 LLM 输出，含 tool_use / text |
| `fake_clock.py` | tests/ | 固定时间，receipt 可重放 |
| `golden/tool_result_*.json` | tests/golden/ | 字节级对账基线 |
| `fixtures/fake_claims_50.jsonl` | tests/ | TG-9 T9-10 的 50 条 fake-completion；**分桶字段 `bucket: zh|en|mixed|paraphrase|negative`，每桶 ≥ 10** |
| `fixtures/fake_claims_paraphrase_10.jsonl` | tests/ | TG-9 T9-11 LLM fallback 专用（10 条 regex 必漏抓） |
| `fixtures/redos_patterns.yaml` | tests/ | TG-9 T9-13 ReDoS 注入测试 |
| `fixtures/fake_keystore.py` | tests/ | TG-7 T7-8 DPAPI/Keychain mock |
| `fixtures/test_hmac.key` | tests/ | 确定性 HMAC key（仅测试） |
| `fixtures/ppt_outline_sample.json` | tests/ | TG-3 T3-2 dry_run 输入 |

---

## §E 执行顺序（TDD red-green-refactor）

按 PRD §4 的 WI 顺序：

```
WI-T0.3 → TG-1（红）→ 写 config → TG-1（绿）
WI-T1.1 → TG-2  → 写 envelope + flag 兜底 → TG-2（绿）+ TG-12 T12-1（验证 flag-off 一致）
WI-T1.2 → TG-3  → 改 5 个工具 → TG-3 绿
WI-T1.3 → TG-4  → Rust commands → TG-4 绿
WI-T1.4 → TG-5  → 前端组件 → TG-5 绿
WI-T1.5 → TG-6  → 路径策略 → TG-6 绿
WI-T1.6 → TG-3 T3-2 → dry_run → 绿
WI-T2.1 → TG-7  → HMAC → 绿
WI-T2.2 → TG-8  → ledger → 绿
WI-T2.3 → TG-8 联动
WI-T2.4 → TG-9  → VerifyGate → 绿（含 50 条 fake claim）
WI-T2.5 → TG-10/11 → outcome verifier → 绿
WI-T3.1 → TG-12 T12-3/4 → 端到端 → 绿
WI-T3.3 → TG-12 T12-1/2/5 → 字节级 + 性能 → 绿
```

每个 WI commit 必跑：

1. 所属 TG 全绿
2. TG-0（smoke）全绿
3. TG-12 T12-1 + T12-2（字节级回归）全绿
4. memory-v2 smoke（前一项目）仍全绿（跨项目兼容）

---

## §F 性能预算

| 操作 | 预算 |
|---|---|
| `ToolReceipt` 生成 + 签名 | < 2ms |
| `ReceiptLedger` append | < 1ms in-mem，< 5ms 写盘（独立 deskpet 进程）；**与 memory-v2 facts vector backfill 共存时放宽到 < 20ms p95**（同盘 I/O 竞争） |
| sha256 大文件（100MB）| 必走 `run_in_executor`，主循环调度延迟不受影响（p95 < 50ms） |
| `SmallLLMExtractor` 单次 fallback | < 3s 硬超时；超时按"按 regex 结果走"，不阻 end_turn |
| `ephemeral_verifier_subagent` 救援 | < 5s p95（haiku 模型 + ledger 输入 ≤ 8KB） |
| `VerifyGate.check` 单次 | < 30ms（50 patterns，2KB assistant text） |
| `file_exists` verifier | < 10ms |
| `git_diff --stat` verifier | < 100ms（典型仓库） |
| 端到端"PPT 场景"额外开销 | < 800ms p95 |

超预算的用例必须挂 `@pytest.mark.perf` 标签并入回归基线。
