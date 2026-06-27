# P4-S18: BGE-M3 + faster_whisper 共存 segfault

**发现日期**: 2026-05-03
**严重等级**: 🔴 P0（rc3 真链路 smoke 暴露）
**状态**: 临时缓解 ✅（mock fallback），永久修复 ⏳

---

## 现象

启动 backend → 任何 `memory_search` IPC 请求触发 backend 整进程 **Segmentation fault** (exit code 139, Windows access violation)。

100% reproducible，两次独立验证：
- 多 IPC 顺序调（最后是 memory_search）→ crash
- 单一 connection 直接发 memory_search → crash

---

## 诊断过程

### 1. faulthandler 拿 native stack

```
Windows fatal exception: access violation
File ".../torch/nn/modules/module.py", line 1329 in convert
File ".../torch/nn/modules/module.py", line 930 in _apply
File ".../torch/nn/modules/module.py", line 903 in _apply  (recursion)
File ".../torch/nn/modules/module.py", line 1343 in to
File ".../FlagEmbedding/.../m3.py", line 345 in encode_single_device
File ".../FlagEmbedding/.../AbsEmbedder.py", line 279 in encode
File ".../FlagEmbedding/.../m3.py", line 300 in encode
File ".../deskpet/memory/embedder.py" in _sync_encode (worker thread)
```

### 2. Standalone 隔离

- `python -c "embedder.warmup(); embedder.encode(['test'])"` 单独跑 **不崩** ✅
- 模拟 backend 跨 task 调用模式 — 也不崩 ✅
- **加一行 `from providers.faster_whisper_asr import FasterWhisperASR`**（甚至不 load model）→ 100% 复现 segfault ❌

### 3. 根因

`faster_whisper` 间接 import `ctranslate2`，后者在 main thread 初始化某些 CUDA / native runtime hook（具体 hook 待深入研究）。BGE-M3 通过 ThreadPoolExecutor worker thread 加载并调用 `model.to(device)` 时，PyTorch 的 `Module._apply` → `convert` 内部在 worker thread 走 native CUDA / CPU memcpy，**与 ctranslate2 的 thread-local state 撞车** → access violation。

device 与 segfault 关系：
- `device='cuda'` → 崩 ❌
- `device='cpu'` → 仍崩 ❌（与 device 无关，是 thread + native lib 共存问题）
- 不 import faster_whisper → 任何 device 都不崩 ✅

---

## 临时缓解（已 ship）

`backend/deskpet/memory/embedder.py::warmup()`：

`device='auto'`（默认）+ `use_mock_when_missing=True`（默认）→ 跳过真模型加载，走 mock embedder（md5 哈希假向量）。

显式 `device='cuda'` 或 `device='cpu'` 仍走真模型（用户自担风险，会崩）。

### 影响

| 功能 | mock fallback 后 |
|---|---|
| chat 主链路 | ✅ 不受影响 |
| MemoryPanel 对话/L1/技能 tab | ✅ 不受影响 |
| ContextTracePanel | ✅ 不受影响 |
| **MemoryPanel 向量搜索 tab** | ⚠️ degraded — md5 哈希向量，跨语言/同义词召回失败；FTS5 + recency + salience 三路 RRF 仍工作 |
| **L3 vec route in Retriever** | ⚠️ 同上 — vec 路返回低质量结果，但其他路弥补 |

### 性能数据

mock 模式下：
- `embedder.encode([句子]) p50` ≈ 1ms（md5 哈希）
- `memory_search` 端到端 ≈ 17-27ms
- 全套 679 unit test 通过（零回归）

---

## 永久修复方案（按优先级）

### 方案 A: BGE-M3 子进程隔离（推荐）

把 BGE-M3 移到**独立 Python 子进程**（`subprocess.Popen`），通过简单 IPC（stdin/stdout JSON 或 socket）传递 encode 请求。子进程不 import faster_whisper / ctranslate2。

**优点**：
- 完全隔离 PyTorch + ctranslate2 共存 bug
- BGE-M3 可走 GPU（在子进程内 CUDA 没人抢）
- IPC 开销 ~5ms RPC，仍远低于 SLO

**成本**：
- 新增 1 个 Python 进程（额外 200MB RSS）
- 写一个简单 RPC 协议（~200 行）

### 方案 B: 替换 BGE-M3 → ONNX Runtime

把 BGE-M3 的 PyTorch 推理换成 ONNX Runtime（`onnxruntime` / `onnxruntime-gpu`）。HuggingFace 仓库已有 `onnx/model.onnx`。

**优点**：
- 不依赖 PyTorch，避开 PyTorch+ctranslate2 共存问题
- ONNX Runtime 启动更快、内存更小

**成本**：
- 重写 `embedder.py::_load_real_model` + `_sync_encode`（~100 行）
- ONNX 输出 dense vec 需手动后处理（pooling）

### 方案 C: 等 PyTorch + ctranslate2 上游修复

观察上游 issue 是否有 fix。  
**风险**：等不到，rc 一直无法启用真 BGE-M3。

### 方案 D: 把 ASR 也改成不依赖 ctranslate2

替换 faster-whisper → whisper-cpp / whisperx / 等。

**风险**：影响主语音管线，重大改动。

---

## 决策

**rc3 → rc4 之间**：维持当前 mock fallback，确保 backend 稳定。

**rc4**：评估方案 A 或 B 实施。优先 **方案 A**（子进程），因为不需要重写 embedder 接口。

---

## 验证 checklist

```bash
# 1. backend 启动不崩
DESKPET_SHARED_SECRET=devtest python main.py

# 2. memory_search 多次调用不崩
# (见 docs/P4-S18-segfault-smoke.py 同目录脚本，待补)

# 3. 全套 unit test 通过
python -m pytest tests/ --ignore=tests/test_asr_short_audio.py \
   --ignore=tests/test_cosyvoice_provider.py -q
# 期望：679 passed
```

---

## 相关 commit

- `<this commit>`: P4-S18 临时缓解（mock fallback）
- `<future>`: P4-S19 永久修复（待实施）
