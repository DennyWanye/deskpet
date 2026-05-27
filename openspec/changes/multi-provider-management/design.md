## Context

DeskPet 当前 LLM provider 是单实例：`backend/main.py` lifespan 阶段从 `config.toml [llm.local]` + keychain 构造一个 `OpenAICompatibleProvider`，注入 `service_context["llm_provider"]`。所有 chat、companion、code、supervisor 都用它。

P5-S1 引入 supervisor 时已经发现这个单点的脆弱性：supervisor LLM 也走同一 endpoint，the relay 抽风时跟主 agent 同归于尽（2026-05-10 实测确认）。当时 workaround 是手写 fallback_llm 二号实例（已被用户要求删掉，因为模型语气混搭）。

P5-S2 sensor + circuit breaker + AutoResume 都做完了，现在该解决根因：**只有一个 provider 就没有 chain 可言**。本 change 把 provider 从"单实例"升级为"registry + chain"，并把控制权下放给用户（settings UI + per-session UI）。

## Goals / Non-Goals

**Goals:**

- 一个清晰的 registry，所有 provider 操作（list/add/remove/reorder/toggle）都走它
- chat 失败时自动按 priority 走 fallback chain，不需要用户介入（保留 P5-S2 的 AutoResume 兜底）
- Code 模式 per-session 可以覆盖全局优先级（一个项目用 the relay、另一个用 ollama 都可以）
- 拖拽 UI keyboard-accessible（aria + 键盘 reorder）
- 旧 `[llm.local]` 单 provider 配置自动迁移到新 schema，**用户无感升级**
- API key 永远不在 ws response 中以明文出现

**Non-Goals:**

- ❌ Provider 健康检查 ping (followup)
- ❌ Per-provider token 计费隔离
- ❌ 引入 OpenAI/Anthropic 原生 SDK
- ❌ Provider 模板市场 / preset import
- ❌ 流量分配（A/B test 不同 model 处理同一请求）

## Decisions

### D1: Provider 列表存哪里？config.toml vs SessionDB vs JSON

**选项**:
- A. 全部存在 `config.toml` `[[llm.providers]]`（用户可手编 + 我们 ws 改写）
- B. 全部存 SessionDB 新表 `llm_providers`
- C. config.toml 存 schema/默认值，SessionDB 存运行时改动 overlay

**选 A**。理由：
- toml 用户自己能 vim 改，符合 deskpet "本机软件" 哲学
- 我们 ws 改 provider 时也直接重写 toml（已有 `_save_user_config()` 辅助）
- 跟现有 `[llm.local]` 升级路径自然
- SessionDB 是状态层，配置层放 toml 更合理
- 简单——不需要管 SessionDB schema migration 是否兼容现有用户

权衡：toml 改动需要文件锁防止并发写。已有 `permissions_auto_mode.json` 用同样模式，没踩过坑。

### D2: API key 存哪里？keychain vs toml plaintext vs encrypted toml

**选项**:
- A. 全部存 OS keychain（Windows credential manager），toml 只存 keychain 引用 ID
- B. 全部存 toml 明文，依赖 OS 文件权限
- C. AES 加密的 toml（密钥派生自机器 ID）

**选 A**。理由：
- 现有 the relay api_key 已经走 keychain（`_resolve_cloud_api_key`）
- Windows 用户对 credential manager 有信任基础
- toml 里只存 `api_key_ref = "deskpet.provider.<provider_id>"`，明文 toml 可以放 git/share
- 加密方案 (C) 复杂度高且不解决"备份/迁移机器"难题

实现：`save_provider(p)` 时如果 `api_key` 字段是新值（不是 `from-keychain`），写入 keychain，toml 存 ref。

### D3: 失败 fallback 触发条件

**选项**:
- A. 任何错误都试下一个 provider
- B. 只有 transient 错误（timeout / connect / 5xx）fallback；permanent 错误（4xx / parse error）直接错
- C. 让 supervisor LLM 决定

**选 B**。理由：
- 复用 P5-S2 已有的 `errors.classify()` 三类分类（Transient/Permanent/Hallucination）
- Permanent 错误（比如 invalid args）换 provider 也救不了，浪费 token
- AutoResume 是 last resort，跟这层不冲突
- 选项 C 引入更多 supervisor 调用，前面已经发现 supervisor 自己也会挂

具体：chain 内 fallback 只对 `LLMProviderError`（network/protocol 类）+ `TransientToolError` 触发；其他直接抛。

### D4: Per-session override 优先级

session 配了 provider_id → 单 provider，**不参与 chain fallback**（用户明确表达"我就要这个 provider"）。
session 配了 preferred_model 但没配 provider_id → 走 chain，但每次请求 model 字段强制改成 preferred_model。
session 啥也没配 → 完全走全局 chain。

权衡：per-session "single provider, no fallback" 最容易出"我这个 session 老挂"的体验。会在 UI 标"已固定到 X，未启用 fallback"。

### D5: 前端拖拽库选 @dnd-kit/core vs react-beautiful-dnd vs 原生 HTML5

**选 @dnd-kit/core**。理由：
- React 19 兼容（react-beautiful-dnd 已 archived，主要支持 React 17）
- 有 keyboard sensor 内置，a11y 满分
- vitest-friendly（测试 hook 而不是 DOM 拖拽事件）
- 体积小（~30KB minified vs react-beautiful-dnd ~80KB）
- TypeScript 一等公民

依赖增量：`@dnd-kit/core` + `@dnd-kit/sortable` + `@dnd-kit/utilities` 三个包，总 ~50KB。

### D6: Migration 时机 — startup vs lazy vs on-write

**选 startup**。lifespan 第一步（在所有 service 注册之前）：
1. 读 config.toml
2. 如果有 `[[llm.providers]]` → 直接用
3. 如果有旧 `[llm.local]` → 转换成 list 第一项 + write back to config.toml + 加注释 `# auto-migrated 2026-05-11`
4. 如果都没有 → 留空（用户需要至少添加一个才能 chat）

理由：lazy migration 容易在第一次 chat 时撞 lock；on-write 不解决新装用户。startup 一次性最清晰。

### D7: provider_id 命名

**选 user-supplied kebab-case**（用户自己起，比如 `the relay-deepseek`、`ollama-qwen`、`openrouter-claude`）。

理由：UUID 用户认不出；hash 不稳定（改 base_url 就变）；自动化命名（`base_url + model`）有特殊字符问题。要求用户起一个简短易记的 id 是合理的负担。后端校验：kebab-case + 1-32 字符 + 在 registry 里唯一。

## Risks / Trade-offs

| Risk | Mitigation |
|---|---|
| 拖拽 UI 在 Tauri WebView2 行为异常 | 用 @dnd-kit/core（已知 WebView2 兼容），加 vitest 覆盖排序逻辑（hooks 层）；live E2E 实测拖拽 |
| toml 并发写丢失 | 单 backend 进程串行写 + 文件锁；ws handler 串行处理 settings 消息 |
| Fallback chain 全挂时用户体验 | 复用 P5-S2 AutoResume 兜底（弹窗 ask_user）+ 显示 chain 状态"3/3 都试了" |
| 旧用户升级时 keychain api_key 找不到 | Migration 时如果 keychain 读不到 → 写入 toml 注释 `# api_key from keychain not found, please re-enter`，UI 提示 |
| 拖拽改顺序后 chain 行为不直觉 | 拖完立刻在 settings 显示"下次失败会按 X→Y→Z 顺序重试"小提示 |
| Per-session override 让 fallback chain 失效 | UI 标 "已固定到 X，未启用 chain fallback" + 提供"清除覆盖"按钮回到全局 chain |
| 添加 provider 时密钥粘贴露明文（screen recording / shoulder surf） | input type=password，value 永不回显（即使填错也只能再粘贴）；Settings UI 提供 "重新输入" 按钮覆盖旧 key |

## Migration Plan

启动时 lifespan 头部加一段：

```python
def _migrate_legacy_provider_config(config_toml_path: Path) -> None:
    """One-time migration from [llm.local] single provider to
    [[llm.providers]] list. Idempotent — checks for existing list first.
    """
    cfg = tomllib.load(open(config_toml_path, 'rb'))
    if cfg.get("llm", {}).get("providers"):
        return  # already migrated
    legacy = cfg.get("llm", {}).get("local")
    if not legacy:
        return  # fresh install, no migration needed
    new_provider = {
        "id": "legacy-default",
        "name": legacy.get("model", "default") + " (auto-migrated)",
        "base_url": legacy["base_url"],
        "model": legacy["model"],
        "api_key_ref": "deskpet.cloud_api_key",  # existing keychain entry
        "priority": 1,
        "enabled": True,
    }
    # ... write back via toml-edit (preserving comments) or rewrite
    logger.info("migrated_legacy_llm_local_to_providers id=%s", new_provider["id"])
```

回滚策略：删 `[[llm.providers]]` 段 + 恢复 `[llm.local]` 段；启动时 migration 跳过（已有 providers）逻辑会重新触发。所以**回滚就是删段**，简单。

## Open Questions

- 加 provider 时要不要立刻做一次 health ping 让用户知道配置对不对？（建议：放 followup，本 change 不做）
- Code 模式 session 卡片下拉的"使用全局 chain"是不是应该是 default option？（建议：是，明确标注）
- chat handler 在 chain fallback 时要不要 emit ws event 让用户看到"切到 provider B 了"？（建议：Phase 5 加，frontend 显示一个小角标 "via openrouter"）
