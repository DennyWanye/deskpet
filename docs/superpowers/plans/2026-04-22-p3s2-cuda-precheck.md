# P3-S2 — CUDA 前置检查 & ASR 加载错误结构化上报

- **Slice**: Phase 3 / S2
- **上游**: P3-S1（模型路径三层解析，已合并 `ed2f371`）
- **估工**: 2 天
- **目标分支**: `p3-s2-cuda-precheck`（worktree）
- **合并目标**: `master`
- **产物版本标签**: 无（phase3 rc1 才打 tag）

---

## 1. 背景 / Why

Phase 3 的发行底线是 **NVIDIA-only 桌面应用**：faster-whisper large-v3-turbo 用
`float16` 跑在 CUDA，CPU 回退不是产品承诺的体验（延迟 10×+，中文准确率掉档）。

现状问题：
1. **Tauri 端**完全不检查 GPU。用户机器没有 NVIDIA 卡 / 没装驱动，直接把
   `backend/main.py` 拉起来——backend 在 lifespan 里试图 `WhisperModel(device="cuda")`，
   CTranslate2 会抛 `RuntimeError: CUDA driver is not available`，但这个异常被
   `except Exception as exc: logger.warning("failed_to_load"...)` 吞了（见
   `backend/main.py:241`），startup 继续标记 "complete"，前端只看到一个
   "能连上但所有 ASR 请求都 500" 的 backend。
2. **Backend 端** `_asr_device = "auto"` 时 `recommend_asr_device()`（
   `observability/vram.py`）会在 `torch.cuda.is_available() == False` 时
   **回退到 CPU + int8**。这违反 Phase 3 的 NVIDIA-only 承诺，掩盖了"驱动没装"
   这种必须让用户知道的问题。
3. ASR load 失败没有结构化路径到前端——前端无法显示"缺少 NVIDIA GPU"气泡。

P3-S2 要补齐的闭环：
**启动前**（Tauri setup hook）→ 没有 NVIDIA → dialog + exit(1)，**根本不拉 backend**。
**启动后**（backend lifespan）→ ASR load 仍失败 → 结构化错误写进
`/health`，并通过 WS control 首包推给前端渲染。

---

## 2. 范围

### In Scope
- [ ] 新建 `tauri-app/src-tauri/src/gpu_check.rs`：探测 NVIDIA GPU + VRAM
- [ ] `Cargo.toml` 加 `nvml-wrapper`（主路径）和 `tauri-plugin-dialog`（弹窗）
- [ ] `lib.rs` `.setup()` hook：GPU 检查失败 → 弹 `MessageDialog` + `app.exit(1)`
- [ ] Backend `main.py` lifespan：捕获 ASR/TTS load 失败，写入
      `service_context` 的 `startup_errors` 结构
- [ ] `/health` 响应扩展 `startup_errors: [{engine, error_code, error_message}]`
- [ ] WS `/ws/control` 首包主动 push `startup_status`（复用握手后第一条消息位）
- [ ] Python 测试：mock `FasterWhisperASR.load()` 抛 `RuntimeError` → 验证
      `/health` 里有结构化错误 + WS 首包携带
- [ ] Rust 单元测试：`gpu_check` 可 mock NVML，验证无 GPU 分支返回 Err
- [ ] `docs/PACKAGING.md` 加一节"硬件前置检查"

### Out of Scope（留给后续 slice）
- 前端 React 组件渲染"缺少 NVIDIA GPU"气泡 / startup-error banner（P3-S3 会搭
  unsupported-hardware UI，这里只保证 Rust 弹窗 + backend 结构化数据就位）
- AMD/Intel GPU 适配（Phase 4+）
- 驱动版本检查（CUDA 12+ 要求）留到 P3-S3
- PyInstaller 打包本身（P3-S4）

---

## 3. 技术方案

### 3.1 Rust 侧 GPU 探测（`gpu_check.rs`）

**主路径**：`nvml-wrapper = "0.10"`（Rust 封装 NVIDIA Management Library；windows
下依赖 `nvml.dll`，NVIDIA 驱动自带，无需用户装 CUDA Toolkit）。

```rust
pub struct GpuInfo {
    pub name: String,
    pub vram_gb: f64,
    pub driver_version: String,
}

#[derive(Debug)]
pub enum GpuCheckError {
    NvmlInitFailed(String),     // nvml.dll 不存在（没装 NVIDIA 驱动）
    NoDevices,                   // NVML 能初始化但 device_count == 0
    DeviceQueryFailed(String),
}

pub fn detect_nvidia_gpu() -> Result<GpuInfo, GpuCheckError> { ... }
```

**降级路径**：nvml-wrapper 如果在目标机器初始化失败，记录具体错误码后直接
返回 `NvmlInitFailed`——不再尝试 `nvidia-smi.exe`。理由：NVML 失败意味着驱动
本身有问题，`nvidia-smi` 也跑不起来；用户看到的症状是一致的"请安装/修复
NVIDIA 驱动"。Roadmap 里列的 `nvidia-smi.exe` 回退改到 P3-S3 真有遇到再加。

### 3.2 Tauri setup hook

```rust
// lib.rs
.setup(|app| {
    // P3-S2: hardware precheck. Fail fast before spawning Python backend.
    if let Err(e) = gpu_check::detect_nvidia_gpu() {
        let msg = gpu_check::format_user_message(&e);
        // blocking dialog; we're in .setup() on main thread.
        app.dialog()
            .message(msg)
            .title("DeskPet — 硬件不支持")
            .kind(MessageDialogKind::Error)
            .blocking_show();
        app.handle().exit(1);
        return Ok(());
    }
    // existing webview permission grant stays below
    ...
})
```

`format_user_message` 按 `GpuCheckError` 分支给中文文案 + 故障排查链接
（指向 `docs/PACKAGING.md#硬件前置检查`）。

### 3.3 Backend 结构化 startup error

新建 `backend/observability/startup.py`：

```python
@dataclass
class StartupError:
    engine: str        # "asr_engine" / "tts_engine"
    error_code: str    # "CUDA_UNAVAILABLE" / "MODEL_DIR_MISSING" / "UNKNOWN"
    error_message: str # 给用户看的一句话
    raw: str           # 原始 repr(exc)，log only

class StartupErrorRegistry:
    def record(self, engine: str, exc: Exception) -> None: ...
    def snapshot(self) -> list[dict]: ...
```

**分类规则**（由 `_classify` 纯函数实现，独立测试）：
- `RuntimeError` 且 `"cuda"` 出现在 `str(exc).lower()` → `CUDA_UNAVAILABLE`
- `FileNotFoundError` 或 `"No such file or directory"` → `MODEL_DIR_MISSING`
- 其它 → `UNKNOWN`

`main.py` lifespan 改：

```python
for name in ("vad_engine", "asr_engine", "tts_engine"):
    engine = service_context.get(name)
    if engine and hasattr(engine, "load"):
        try:
            await engine.load()
            logger.info("loaded", engine=name)
        except Exception as exc:
            logger.warning("failed_to_load", engine=name, error=str(exc))
            startup_errors.record(name, exc)  # NEW
```

`/health` 返回：

```json
{
  "status": "ok" | "degraded",
  "secret_hint": "...",
  "strategy": "local",
  "cloud_configured": false,
  "startup_errors": [
    {"engine": "asr_engine", "error_code": "CUDA_UNAVAILABLE",
     "error_message": "无法初始化 CUDA，请确认 NVIDIA 驱动已安装"}
  ]
}
```

`status` 由 `"ok"` 变 `"degraded"` 当且仅当 `startup_errors` 非空。

### 3.4 WS control 首包

`/ws/control` 握手成功后的第一条 server→client 消息新增类型：

```json
{"type": "startup_status", "degraded": true, "errors": [...]}
```

前端已有的 `backend-restarted` 事件后需要重新收到这条（因为重启后
startup_errors 会刷新）—— 由 `on_connect` 始终先发即可，无需 restart hook。

---

## 4. 文件改动清单

### 新增
- `tauri-app/src-tauri/src/gpu_check.rs`（~120 行）
- `backend/observability/startup.py`（~60 行）
- `backend/tests/test_startup_errors.py`（分类 + registry + lifespan 集成）
- `tauri-app/src-tauri/src/gpu_check_tests.rs` 或 `#[cfg(test)] mod tests`

### 修改
- `tauri-app/src-tauri/Cargo.toml`（+nvml-wrapper, +tauri-plugin-dialog）
- `tauri-app/src-tauri/src/lib.rs`（+mod gpu_check, setup hook 前置检查）
- `backend/main.py`（registry 接线 + `/health` 扩展 + WS 首包）
- `backend/tests/test_metrics_endpoint.py`（健康端点 schema 新字段断言）
- `docs/PACKAGING.md`（+ "硬件前置检查" 小节）

---

## 5. 测试计划

### 5.1 单元测试（TDD 红-绿）

**Rust** (`gpu_check.rs`)：
- `format_user_message` 对每个 `GpuCheckError` 变体返回预期中文片段
- 不 mock NVML；`detect_nvidia_gpu()` 的 integration test 打 `#[ignore]`
  tag，开发机跑

**Python** (`test_startup_errors.py`)：
- `_classify(RuntimeError("CUDA driver is not available"))` → `CUDA_UNAVAILABLE`
- `_classify(FileNotFoundError("models/..."))` → `MODEL_DIR_MISSING`
- Registry `record()` 后 `snapshot()` 按 engine 去重（后来的覆盖前面的）
- 用 `FastAPI TestClient` + `service_context` fixture：注入
  "会抛 RuntimeError 的假 ASR engine"，lifespan 走一遍，打 `/health`，
  断言 `status == "degraded"` 且 `startup_errors[0].error_code == "CUDA_UNAVAILABLE"`
- WS `/ws/control` 握手后第一帧是 `startup_status`

### 5.2 E2E（auto-verify 第二层）

1. **快乐路径**（有 NVIDIA）：
   - 跑 `scripts/e2e_*.py` 冒烟 —— ASR roundtrip 仍成功，`/health` 里
     `startup_errors == []`
2. **模拟无 GPU**：临时把 `backend/providers/faster_whisper_asr.py` 的
   `load()` patch 成 `raise RuntimeError("CUDA driver is not available")`
   （不要真卸驱动！）→ 重启 backend → 打 `/health` 看到 `degraded`，
   WS 首包携带错误
3. **Rust GPU 检查**：由于开发机有 GPU，Rust precheck 快乐路径自动过；
   dialog 分支靠单元测试 + 手动把 `detect_nvidia_gpu()` 临时返回 `Err` 覆盖

### 5.3 Preview MCP UI-level

本 slice 不改前端渲染，UI 层无新行为。Preview MCP 只需复跑 P3-S1 的回归
断点（Live2D 载入 + 一次 ASR 成功），确认无倒退。

---

## 6. 风险

| 风险 | 缓解 |
|---|---|
| nvml-wrapper 在 Windows Arm64 / 部分精简 NVIDIA 驱动缺 `nvml.dll` | error 文案显式引导"重装完整版 NVIDIA 驱动"；P3-S3 再考虑 `nvidia-smi` fallback |
| `.blocking_show()` 在 setup hook 里卡主线程 | setup 本来就同步；dialog 后立即 `exit(1)`，用户只看一次 |
| `tauri-plugin-dialog` 需要配 capabilities | 按 Tauri 2 默认配置加 `dialog:default` 到 `capabilities/default.json` |
| 现有 backend `recommend_asr_device()` 会自动降级 CPU，隐藏问题 | 本 slice **不改** `auto` 降级逻辑——Rust precheck 已挡在前面；后续 P3-S3 再评估是否要让 backend 也硬失败 |

---

## 7. 验收标准

- [ ] `cargo test -p deskpet`（Rust 单测）绿
- [ ] `pytest backend/tests/` 新增用例 + 原有 283 passed 全绿
- [ ] `scripts/check_no_hardcoded_assets.py`（P3-S1 护栏）仍绿
- [ ] 本机 Tauri dev 启动：快乐路径正常拉起 backend，`/health` 无 startup_errors
- [ ] HANDOFF 文档 `docs/superpowers/handoffs/p3-s2-cuda-precheck.md` 就位
- [ ] `docs/superpowers/STATE.md` 更新 P3-S2 行

---

## 8. 提交策略

按 spec-first 节奏至少 6 个原子 commit：
1. `docs(plan): P3-S2 CUDA precheck slice plan`（本文）
2. `test(backend): add startup error registry + classify tests (red)`
3. `feat(backend): startup error registry + /health + WS startup_status`
4. `test(tauri): gpu_check format_user_message unit tests (red)`
5. `feat(tauri): gpu_check module using nvml-wrapper`
6. `feat(tauri): precheck hook in lib.rs setup + dialog + exit`
7. `docs(packaging): hardware precheck section + HANDOFF + STATE update`

Merge 用 `--no-ff` 保留 slice 拓扑（同 P3-S1）。
