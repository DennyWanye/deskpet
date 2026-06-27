# Design — Companion-Session Context Isolation & Capability Gate

> 配套 proposal.md。只写关键决策（Dx）+ 数据结构 + 集成点。bug 复现证据见 proposal §Why。

## D1 — Memory recall session-affinity（主修复）

**问题**：`retriever.py` 的 RRF 融合 vec/fts/recency/salience 四信号，**没有 session 维度**。L3 BGE-M3（真模型后）跨 session 语义召回过强，companion session 被 code-session 的项目记忆劫持。

**决策**：RRF 加第 5 信号 `session_affinity`，乘性降权（不是过滤 — 保留"桌宠记得你"）：

```
affinity(mem, cur_session):
  same session                         → 1.0
  cur=companion, mem from code session,
    mem 是"项目/任务"类(is_summary or
    含 tool_calls or code-mode 产生)    → decay   (config, 默认 0.15)
  cur=companion, mem from code session,
    mem 是"人物/偏好/闲聊"类            → 0.8     (轻降, 仍跨 session 记得)
  cur=code, mem from other code session → 0.5
  其它                                  → 1.0
final_score = rrf_score * affinity
```

- "项目/任务类 vs 人物/偏好类"判定：用现成字段 — `is_summary=1` 或 `tool_calls is not null` 或 source session_id 以 `code-` 开头且消息含路径/代码特征 → 项目类。纯文本闲聊 → 人物类。规则优先，不引 LLM。
- `config.toml [companion].memory_cross_session_decay` 控制 decay；`=1.0` 完全退回旧行为（Strangler-Fig）。
- 改在 `retriever.py` 融合阶段乘 affinity，**不动 L3 BGE-M3 / sqlite-vec 查询本身**（那层是对的）。
- 纯函数可测：`_session_affinity(mem_row, cur_sid, cur_kind, decay) -> float`。

## D2 — Capability gate（防漂移）

**问题**：deskpet 无图像生成工具。"画个海报"这种请求 LLM 没合法动作 → 漂移到记忆里的旧项目。

**决策**：agent loop 入口前一个轻量门 `backend/agent/capability_gate.py`：

```
classify_request(user_text, available_tools) -> GateVerdict
  GateVerdict.PASS                  → 正常进 loop
  GateVerdict.REFUSE(reason, alt)   → 不进 loop，直接回 graceful 文案
```

- rule-first：关键词 + 意图模式匹配"生成图片/海报/视频/语音/作曲/3D模型"等且无对应工具 → REFUSE。命中歧义才走一次 haiku-class LLM 兜底（≤300ms，复用 ContextAssembler classifier 基础设施）。
- REFUSE 文案：诚实说做不到 + 建议（"我没有图像生成能力，你可以用 XX；如果要我帮你写生成图片的代码，请进 code 模式指定项目"）。**不是弹窗确认，是直接答复** — 符合 [feedback_no_sandbox_constraints]。
- `available_tools` 从 ToolRegistry 实时取，新增图像工具后门自动放行（不写死黑名单）。
- `[companion].capability_gate_enabled=false` 退回旧行为。

## D3 — Companion write-scope

**问题**：`default` session `mkdir /path/to/deskpet\backend\vpn-cli` 往任意路径写。

**决策**：companion/`default` session 的写盘类工具（write_file/edit_file/mcp_filesystem_*/run_shell mkdir）path 参数限定在 workspace 根下：

```
companion session:
  write path 必须在 resolve(workspace_root) 内
  （默认 %APPDATA%/deskpet/workspace；config 可改）
  越界 → tool 返回 {ok:false, error:"companion session 写盘限定在 workspace；
         要写项目代码请进 code 模式并选择项目"}
code session: 不受影响（已绑 project_root，本来就有边界）
```

- 这**不是沙箱**：不拦读、不拦命令、不弹窗；只是"陪伴模式不该随便往代码仓库写文件"的 scope 区分（session 类型语义，不是权限墙）。是手滑级防护，符合 memory 里允许的 irreversible-guard 例外。
- `[companion].write_scope_enforced=false` 退回。
- 实现点：chat handler 取 session kind，注入到 tool dispatch 的 path 校验；复用 `code_mode/state.py` 已有的 session 分类。

## D4 — config 新段

```toml
[companion]
# D1: 跨 session 项目类记忆在 companion session 的降权系数。
# 1.0 = 不降权（旧行为/回退）；0.15 = 强降权（默认，防劫持）。
memory_cross_session_decay = 0.15
# D2: 无能力请求 graceful refuse 而非漂移。
capability_gate_enabled = true
# D3: companion session 写盘限定 workspace 根。
write_scope_enforced = true
```

## 集成点

| 文件 | D | 改动 |
|---|---|---|
| `backend/memory/retriever.py` | D1 | RRF 融合乘 `_session_affinity`；读 `memory_cross_session_decay` |
| `backend/agent/capability_gate.py` (新) | D2 | `classify_request` + REFUSE 文案 |
| `backend/main.py` chat handler | D2/D3 | loop 入口调 capability_gate；companion session 注入 write-scope |
| `backend/deskpet/code_mode/state.py` | D3 | 复用 session-kind 判定（companion vs code） |
| `config.toml` | D4 | `[companion]` 段 |

## 测试策略

- **回归测试（必须）**：复现 2026-05-16 bug — mock 一条 "code-tyfbt62t 做 VPN" 的高 salience 记忆 + 一条 `default` session "帮我生成海报图片" 请求，断言：(a) retriever 对 VPN 记忆 affinity≤decay，(b) capability_gate REFUSE 图像请求，(c) 不产生任何 write_file/mkdir tool_call。
- 纯函数密集单测：`_session_affinity`、`classify_request`、write-scope path 校验。
- 三个 feature flag 各自的 rollback 单测。
- 终验 real E2E：`default` session 真发"帮我画张海报"，截图证明 graceful refuse 且无文件被建（[feedback_real_test]）。
