# MSI 安装包 — 已知问题清单

**日期**: 2026-05-07
**触发场景**: 走 `scripts/build-msi.ps1` 出来的 5.4 GB MSI，装到 Windows 后启动

---

## 1. 设置面板"保存"按钮在 release 模式下报 `TypeError: Failed to fetch`

### 表现
- 用户打开 Settings → 改 LLM `base_url` / `model` / `api_key` → 点"保存"
- UI 弹红：「已保存到本地，但后端热更新失败: TypeError: Failed to fetch」
- 本地配置文件确实写入了（`%AppData%/deskpet/llm_runtime.json`），但运行中的 backend 没收到 reload
- dev 模式（`npm run tauri dev`）下同一段代码不会报这个错

### 根因
Tauri 2.x release 模式下 webview 的 origin 是 **`https://tauri.localhost`**，而 backend 监听 **`http://127.0.0.1:8100`**。浏览器的 mixed-content 策略会**阻止**从 `https` origin 直接 fetch `http` URL。

dev 模式没事，因为 vite 把 webview 拉到 `http://localhost:1420`，protocol 跟 backend 一致。

错误现场：

```ts
// tauri-app/src/bindings/config.ts:28-39
export async function updateCloudConfig(secret, update) {
  const resp = await fetch("http://127.0.0.1:8100/config/cloud", {
    method: "POST",
    headers: { "X-Shared-Secret": secret, ... },
    body: JSON.stringify(update),
  });
  // ... ↑ 在 release 模式下 fetch() 直接抛 TypeError，
  //         浏览器都没让请求出门
}
```

### 现有 Workaround
不通过 UI 保存，直接改文件：

```powershell
notepad $env:APPDATA\deskpet\llm_runtime.json
# 编辑 base_url / model / api_key，保存
# 重启 deskpet —— backend 启动时读这个文件，配置即生效
```

文件 schema：

```json
{
  "base_url": "https://your-llm-relay.example.com/v1",
  "model": "gpt-5.5",
  "temperature": 0.7,
  "api_key": "sk-..."
}
```

### 永久修复方案（待做）
把前端 fetch 改成 Tauri IPC 走 Rust 转发：

1. **Rust 端** (`tauri-app/src-tauri/src/main.rs` 或新文件)：

   ```rust
   #[tauri::command]
   async fn update_cloud_config(
       state: tauri::State<'_, BackendInfo>,
       update: serde_json::Value,
   ) -> Result<serde_json::Value, String> {
       let url = format!("http://127.0.0.1:{}/config/cloud", state.port);
       let resp = reqwest::Client::new()
           .post(&url)
           .header("X-Shared-Secret", &state.shared_secret)
           .json(&update)
           .send()
           .await.map_err(|e| e.to_string())?;
       resp.json().await.map_err(|e| e.to_string())
   }
   ```

   `Builder::default().invoke_handler(tauri::generate_handler![..., update_cloud_config])`

2. **前端** (`tauri-app/src/bindings/config.ts`)：

   ```ts
   import { invoke } from "@tauri-apps/api/core";

   export async function updateCloudConfig(_secret, update) {
     return invoke<CloudConfigResult>("update_cloud_config", { update });
   }
   ```

   secret 参数可以删掉 — Rust 端从已知 state 拿。

3. 重 build MSI（`scripts/build-msi.ps1`）。

### 影响范围
- ❌ Settings 面板的"保存 LLM 配置"按钮（`/config/cloud`）
- ⚠️ 任何前端直接 fetch backend HTTP 端点的代码都有同样毛病
  - 检查：`grep -rn "fetch.*8100\|fetch.*127.0.0.1" tauri-app/src/`
  - 截至 2026-05-07，似乎只有 `bindings/config.ts:32` 一处

WebSocket 连接走 `ws://`，不受 mixed-content 限制（fetch 限制不覆盖 WebSocket）。所以聊天/语音管线在 release 下正常工作。

### 类似 issue 的发现路径
**只有 release/MSI 的产物会触发**，dev 模式永远复现不出来。任何"在 dev 下能用，装包后不能用"的 fetch 问题都先去看 mixed-content。

---

## 2. MSI 安装时 C 盘需要 ~7 GB 临时空间

### 表现
点 Install 中弹"磁盘空间不足"或装到一半弹"Out of Disk Space"，C 盘 < 5 GB 可用时几乎必然撞上。

### 根因
Windows Installer 的固定行为：装 MSI 时 cab 数据会被 cache 到 `C:\Windows\Installer\` 一份（用于将来卸载/修复），即使 MSI 选了装到非 C 盘。我们 MSI 5.4 GB → cache 也要 5.4 GB → 加上其他临时文件，C 盘要 7 GB+ 可用。

### Workaround
跑 `scripts/cleanup-c.ps1`（admin） + `scripts/uninstall-bloat.ps1`（admin），把 C 盘腾出 ≥ 7 GB。脚本里说明了能清的目录和能卸的应用清单。

### 长期方案（待做）
把 MSI 改成 **external cab** 模式（`<MediaTemplate EmbedCab="no" ...>`），让 cab 文件跟 .msi 并排放、不嵌进 .msi 主体。这样 Windows Installer 只 cache 小的 .msi 主文件，cab 流式读取不 cache。代价：分发时不是单文件 .msi，而是 .msi + N 个 .cab 一起发（用户得保留全部）。

---

## 3. SmartScreen "未识别的应用程序" 警告

### 表现
双击 MSI 时 Windows Defender SmartScreen 弹"Microsoft Defender SmartScreen 阻止了未识别的应用程序"。

### 根因
MSI 没用证书签名。

### Workaround
点 "更多信息" → "仍要运行"。

### 长期方案
- 弄一张 Authenticode 代码签名证书（OV 证书 ¥2000+/年，EV 证书 ¥6000+/年但建立 reputation 更快）
- 在 `tauri.conf.json` 的 `bundle.windows.certificateThumbprint` 配置签名指纹
- 重 build 自动签名

---

## 4. Tauri NSIS 32-bit makensis 累积 mmap 上限 ~3.5 GB（已绕过）

### 现状
已经放弃 NSIS，切到 MSI。NSIS target 在 `tauri.conf.json` 的 `bundle.targets` 已删。详见 commit history 里 P4-S20 这一段的诊断记录。

如果未来某天有人想恢复 NSIS：要换 amd64-Unicode 版本的 makensis（社区 fork，不是官方），或者把 dist 拆得 < 3 GB。

---

## 5. WiX `<Media>` 单 cab 2 GiB 上限（已 patch）

### 现状
Tauri bundler 默认生成 `<Media Id="1" Cabinet="app.cab" EmbedCab="yes" />` 单一 cab。我们 dist 7.7 GB 触发 `light.exe` 在 CreateCabFinish 抛 `E_UNEXPECTED`。

`scripts/build-msi.ps1` 已经把这一步自动化（hot-patch wxs 改 `<MediaTemplate ... MaximumUncompressedMediaSize="1900">`，然后重 candle + light），跑这个脚本就行。

如果未来 Tauri bundler 自己支持 multi-cab 配置（看 issue tracker），可以撤掉 patch。

---

## 6. Windows Search (WSearch) 锁住 `target/release/wix/x64/`（构建侧）

### 现状
WSearch 服务在 indexing 旧的 wix 构建产物时持有 file handle，导致下次 `tauri build` `rm` 失败。

`build-msi.ps1` 已经绕开（用 `CARGO_TARGET_DIR=G:\temp\cargo-target` 切到全新输出位置）。如果坚持用默认路径，需要 admin 重启 WSearch 服务。

---

## 7. **没有退出按钮，正常运行时无法干净退出**

### 表现
DeskPet 启动成功后（Live2D 显示出来），用户找不到退出方式：
- Toolbar 上没有退出/关闭按钮
- 没有系统托盘图标（Rust 端没注册 `TrayIconBuilder`）
- 窗口是 `decorations: false` + `transparent: true`，没有标题栏 × 按钮
- Alt+F4 在透明无边框窗口下行为不稳定

唯一在代码里写了的"退出"路径是 `App.tsx:161 handleBootExit`，但它只挂在 `<StartupOverlay onExit={...}>` —— 那个 overlay 仅在 backend 启动失败时显示，正常启动后 overlay 自己消失，退出按钮也跟着隐藏了。

### 现有 Workaround
```powershell
taskkill /F /IM deskpet.exe; taskkill /F /IM deskpet-backend.exe
```

或任务管理器（Ctrl+Shift+Esc）→ 结束 deskpet.exe。

### 永久修复方案（待做）
两种都加上更好：

**方案 A：Toolbar 加退出按钮**（最简单，10 分钟）

```tsx
// tauri-app/src/components/Toolbar.tsx
<button onClick={onExit} aria-label="退出 DeskPet">⏻</button>
```

把 `App.tsx` 里现有的 `handleBootExit` 函数提到上面，传给 `<Toolbar onExit={handleBootExit} />`。

**方案 B：注册系统托盘图标 + 右键菜单退出**（推荐，30 分钟）

```rust
// tauri-app/src-tauri/src/lib.rs
use tauri::{
    menu::{Menu, MenuItem},
    tray::{TrayIconBuilder, MouseButton, MouseButtonState, TrayIconEvent},
};

let quit_i = MenuItem::with_id(app, "quit", "退出 DeskPet", true, None::<&str>)?;
let menu = Menu::with_items(app, &[&quit_i])?;
let _ = TrayIconBuilder::new()
    .icon(app.default_window_icon().unwrap().clone())
    .menu(&menu)
    .on_menu_event(|app, event| match event.id.as_ref() {
        "quit" => { app.exit(0); }
        _ => {}
    })
    .build(app)?;
```

托盘 + Toolbar 双保险。

重 build MSI 后生效。

### 影响范围
所有装了 MSI 的用户。第一次启动后必踩 — 用户找退出按钮找不到，只能任务管理器或 cmd kill。

---

## 8. **启动时弹一个黑色 cmd 窗口跟着 deskpet 一起开**

### 表现
双击 deskpet.exe（或 MSI 装好后启动），除了 Live2D 桌宠窗口外，还会同时**弹出一个黑色控制台窗口**（标题是 `deskpet-backend.exe` 之类），里面滚动 Python log。关掉这个 cmd 窗口会**连带杀死 backend 进程**，导致桌宠功能直接掉线。

### 根因
两层叠加：

1. **PyInstaller spec 把 backend 编成 console subsystem** (`backend/deskpet-backend.spec:172`)：
   ```python
   exe = EXE(
       ...
       console=True,                                  # SHARED_SECRET on stdout
       ...
   )
   ```
   注释说"SHARED_SECRET on stdout"，意思是 P3-S5 那时设计成 console=True 是为了让 stdout 能正常打印。但其实 stdout 走 `Stdio::piped()` 给 Rust supervisor 读，跟 console 模式无关 —— `console=False`（windowed）也能 print 到 piped stdout。

2. **Rust spawn 没加 `CREATE_NO_WINDOW` flag** (`process_manager.rs:128-143`)：
   ```rust
   let mut cmd = Command::new(exe);
   ...
   cmd.stdout(Stdio::piped()).stderr(Stdio::inherit())
       .env("PYTHONIOENCODING", "utf-8")
       ...;
   // 缺这个：
   // #[cfg(windows)]
   // cmd.creation_flags(CREATE_NO_WINDOW);
   ```
   就算 PyInstaller 把 backend 编成 console subsystem，spawn 时加 `CREATE_NO_WINDOW`（值 `0x08000000`）也会让 Windows **不分配新的 console window**。

### 现有 Workaround
没有干净的运行时方案 —— 一旦 cmd 窗口弹出来就只能让它在那。最低成本：**最小化它**（点窗口左上角图标 → 最小化）但不要关（关了 backend 死）。

### 永久修复方案（待做）
**首选：改 Rust spawn flags**（5 分钟，不用重 build PyInstaller）

`tauri-app/src-tauri/src/process_manager.rs` 顶部加：
```rust
#[cfg(windows)]
use std::os::windows::process::CommandExt;
```

`spawn_once` 里 `cmd.stdout(Stdio::piped())` 那一段后面加：
```rust
#[cfg(windows)]
{
    // CREATE_NO_WINDOW = 0x08000000. Suppresses the inherited console
    // window even when the child exe was linked as console subsystem.
    // Without this, the PyInstaller backend (console=True for SHARED_SECRET
    // logging) flashes a cmd window that the user can't tell apart from
    // a runaway terminal — and closing it kills the backend.
    cmd.creation_flags(0x08000000);
}
```

只重 build Tauri exe（30 秒）+ 重 build MSI 即可，PyInstaller dist 不动。

**也可以同时做**（更彻底）：spec 改 `console=False`，再重 build PyInstaller + MSI。这样即使外部直接双击 `deskpet-backend.exe` 也不会弹窗。但工作量大很多（重打 PyInstaller ~10min + MSI ~5min）。

### 影响范围
所有装了 MSI 的 Windows 用户。第一印象很差（看起来像后台残留的 cmd 在跑没关）。

---

## 10. 双击空白区域整屏蓝色（"蓝屏"）

### 表现
桌宠窗口大部分区域是透明的，鼠标双击空白处 → 视觉上整片变蓝色覆盖。再点任意位置取消选中后蓝色消失。

### 根因
1. `index.css:30` 定义了 `::selection { background: rgba(59, 130, 246, 0.3); }`（半透明蓝）
2. 没设 `user-select: none`，所以双击会**选中**点击点附近的所有 absolutely-positioned 透明子元素
3. 结果：整片透明区域被染成 30% 透明蓝 → 看起来像 BSOD

### 状态
✅ **已修**（commit pending）：
- `index.css` 给 `html, body, #root` 加 `user-select: none`，阻断默认选中
- 同时加规则：input / textarea / [contenteditable] / pre / code / `.bp-selectable` / 几个面板类**保留** `user-select: text` —— 不影响聊天历史复制、设置框输入

下次 build 生效。

---

## 11. 不能拖动桌宠位置

### 表现
桌宠固定在初始坐标 (2100, 640)。用户想把它挪到屏幕另一边，找不到方法。任务栏右键也没"移动窗口"（窗口 `decorations: false` 没标题栏）。

### 根因
- 窗口是 `decorations: false` + `transparent: true`，没有 OS 自带的拖动条
- App.tsx 的 root container 没挂 Tauri 2 的 `data-tauri-drag-region` attribute

### 状态
✅ **已修**（commit pending）：
- App.tsx 的 root `<div>` 加 `data-tauri-drag-region`
- Tauri 自动判断：mousedown 落在带这个 attribute 的元素**且不在交互子元素上**才拖动 → 点 Toolbar 按钮 / Live2D 角色 / 任意打开的 panel 不会触发拖动，只有抓**真正的透明空白**才拖

下次 build 生效。

实测要看：抓桌宠透明区域 → 拖 → 整窗跟着鼠标移动。

---

## 9. 技能商店里 docx 这种长 description 撑爆卡片，无法滚动

### 表现
进 SkillStore → marketplace tab → 找到 docx 技能（HuggingFace 官方 skills-registry 里的 "Document creation: Microsoft Word"），它的 description 有 1500+ 字（包含 "Triggers include any mention of 'Word doc', 'word document', '.docx', or requests to produce..."），整个 description 段被铺成几十行，**单张卡片就撑满了滚动视口**，其它 skill 看不到，滚动也滚不动（因为只有一张卡）。

### 根因
`tauri-app/src/components/SkillStorePanel.tsx` 的 `MarketplaceList` 渲染 description 时**没截断**。`InstalledList` 那边在 line ~510 用了 `WebkitLineClamp: 2` 截断到 2 行，但 Marketplace 这边的 line 596-605 漏了。

### 状态
✅ **已修**（commit pending）—— 给 marketplace description 加 4 行截断 + `title` attribute 让 hover 能看完整。详见 SkillStorePanel.tsx:596 附近改动。

下次重 build MSI 时随其他 UI fix 一起生效。

---

## 12. 旧 config.toml schema 残留 → backend 启动请求 sealos URL → 401

### 表现
干净启动 backend 后日志立刻出现：
```
INFO:httpx:HTTP Request: GET https://vcrppsmofoyv.cloud.sealos.io/v1/models "HTTP/1.1 401 Unauthorized"
```
用户从未配过这个 URL。

### 根因
1. P4-S20-LLM-Unified 把 LLM 配置 schema 从 `[llm.local]` + `[llm.cloud]` 双段改成单 `[llm]` 段
2. 仓库 `G:/projects/deskpet/config.toml` 已经升级到新 schema
3. **MSI/PyInstaller spec 没有把 config.toml 打进 bundle**（`spec` 只列了 `memory/migrations`，其他 datas）
4. 所以 `_bundle_default_config_path()` 在 frozen 模式下找不到 bundle config，`seed_user_config_if_missing()` 直接 return
5. 用户 `%AppData%\deskpet\config.toml` 是更早版本残留的旧 schema，包含写死的 sealos URL：
   ```toml
   [llm.cloud]
   model = "qwen3.6-plus"
   base_url = "https://vcrppsmofoyv.cloud.sealos.io/v1"
   ```
6. backend 读到 `[llm.cloud]` → init cloud OpenAI provider → provider 启动时 health-check `/v1/models` → 401（没 api_key）

### 已做的临时修复（用户单机）
```powershell
Copy-Item $env:APPDATA\deskpet\config.toml $env:APPDATA\deskpet\config.toml.legacy-bak
Copy-Item G:\projects\deskpet\config.toml $env:APPDATA\deskpet\config.toml
```
新 config 没 `[llm.cloud]` → 不再 init cloud provider → sealos 请求消失。

### 永久修复方案（待做）
**修 PyInstaller spec 把 config.toml 打进 bundle**：

`backend/deskpet-backend.spec` line ~73:
```python
datas += [
    ("memory/migrations", "memory/migrations"),
    ("../config.toml", "."),    # ← 加这一行：打到 _MEIPASS 根
]
```

然后 `backend/config.py:_bundle_default_config_path` 加一个查 `_MEIPASS/config.toml` 的分支：
```python
if getattr(sys, "frozen", False):
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        c = Path(meipass) / "config.toml"
        if c.is_file(): return c
    # ... 现有 exe_dir 多层 fallback
```

并且 **MSI 装包后 seeding 逻辑应该检测旧 schema 自动迁移**：在 `seed_user_config_if_missing` 里加：

```python
def seed_user_config_if_missing() -> Path | None:
    user_target = ...
    if user_target.is_file():
        # 检查是否为旧 schema（含 [llm.local] 或 [llm.cloud]），是则备份+覆盖
        try:
            with open(user_target, "rb") as f:
                raw = tomli.load(f)
            llm = raw.get("llm", {})
            if "local" in llm or "cloud" in llm:
                logger.warning("legacy llm schema detected, migrating")
                shutil.copy(user_target, user_target.with_suffix(".legacy-bak"))
                source = _bundle_default_config_path()
                if source: shutil.copyfile(source, user_target)
        except Exception:
            pass
        return user_target
    # ... 现有 first-run seed
```

### 同时关联：用户希望"初始空 + 设置里保存的值是唯一真相"
当前 `LLMEndpointConfig` defaults 写死 ollama localhost。用户要的语义：

```python
@dataclass
class LLMEndpointConfig:
    model: str = ""
    base_url: str = ""
    api_key: str = ""
    temperature: float = 0.7
    max_tokens: int = 2048
```

加上 backend 启动检测 `if config.llm.local.base_url == ""` 时不 init provider，chat handler 在收到请求时返回友好的"请先去设置配置 LLM"。

这是产品决策（要不要让 deskpet 第一次启动就能跟 Ollama 聊？），用户单机修法已经够用，永久层面留给下次 sprint。

---

## 13. 语音输入永远走不到工具调用链路

### 表现
- 用户**说话**："帮我在桌面生成一个笑话"
- ASR 识别正确（log 里 `event='user_said' text='帮我在桌面生成一个笑话'` ✅）
- 14-18s 后 LLM 回了一段文字（"我没法直接生成文件" 之类），**没有任何工具调用**
- 同一句话**用文字输入**则会触发 `desktop_create_file` 工具（如果 `chat_v2 toggle` 开着，但其实现在永远 on，见下条）

### 根因
两条聊天链路在 P4-S20-LLM-Unified 后**没统一**：

| 入口 | 实际处理 | 工具调用？ |
|------|---------|-----------|
| WebSocket `chat` / `chat_v2` （文字）| `main.py:1450` → AgentLoop tool_use loop | ✅ |
| `voice_pipeline.process_audio()` （语音）| `pipeline/voice_pipeline.py:212` → `self.agent.chat_stream(...)` | ❌ |

`VoicePipeline` 构造时拿的是 `agent: AgentProvider`（普通 LLM agent，没 tool registry / permission gate），所以即使后面要加 tool_use 也得给它注入 ToolRegistryV2 + PermissionGate。

### 用户诉求（明确）
> 语音和文字输入我都希望走到能够使用工具的那个链路

### 永久修复方案（待做）
两步走：

**Step 1 — `VoicePipeline` 接受 ToolRegistryV2 + PermissionGate**

```python
# backend/pipeline/voice_pipeline.py
class VoicePipeline:
    def __init__(
        self,
        ...,
        agent: "AgentProvider",
        tool_registry_v2: ToolRegistry | None = None,  # NEW
        permission_gate: PermissionGate | None = None, # NEW
        ...,
    ):
        ...
        self.tool_registry_v2 = tool_registry_v2
        self.permission_gate = permission_gate
```

**Step 2 — `_handle_user_said` 改成跑 AgentLoop（仿 main.py:1450 的 `_run_chat`）**

`voice_pipeline.py:200-220` 的 agent 段替换为：

```python
if self.tool_registry_v2 is not None:
    from agent.loop import AgentLoop  # P4-S20
    loop = AgentLoop(
        llm=self.agent.llm,                      # provider 拿 base llm
        tool_registry=self.tool_registry_v2,
        permission_gate=self.permission_gate,
        session_id=self.session_id,
    )
    async for event in loop.run(messages=[{"role": "user", "content": text}]):
        # 把 AssistantMessageEvent / ToolCallEvent 转成 user-facing
        # 通过 ws.send_json 发给前端（跟 main.py:1450 那段一致）
        ...
else:
    # legacy fallback — 旧路径，给没有装 ToolRegistry 的环境
    async for token in self.agent.chat_stream(...): ...
```

**Step 3 — `main.py:1757` VoicePipeline 构造调用** 把 `deskpet_tool_registry_v2` + `permission_gate_v2` 传进去

注意还得让前端把语音 user_said 也作为"会话"（写到 SessionDB），跟文字 chat 一样的事务。AgentLoop 内部应该已经处理了。

### 工作量
中等（1-2 小时）—— 主要是 voice_pipeline 跟 AgentLoop event 流的 ws 协议转换 + 测试。

---

## 14. Toolbar 上的 `chat_v2` toggle 已经冗余，建议隐藏或删除

### 表现
Toolbar 上有一个 `useToolUseLoop` toggle 按钮，状态会通过 `chat` 或 `chat_v2` 的 msg_type 区分发到 backend。但实际上：

```python
# backend/main.py:1450
elif msg_type in ("chat", "chat_v2"):
    # 永远走 AgentLoop tool_use loop ...
    # `chat_v2` 类型保留作为别名，前端无感升级。
```

backend 已经把两种 msg_type 当**同一回事**处理。toggle 关了也走 tool_use，开了也走 tool_use。

### 修复方案
**简单做法**：直接删掉 Toolbar 那个 toggle 按钮 + 前端发 `chat` 消息时 backend 处理逻辑不变。

**或者保留作"开关 tool_use"语义**：backend 在 `chat` 时 fallback 到非工具路径（用户能临时关掉所有工具，比如想纯聊天不要 LLM 写文件）。但需要明确产品意图。

---

## 15. 缺一个"Code 模式"——给复杂任务用，类似 Claude Code 的能力

### 用户诉求（直接引用）
> 我希望设计一个 code 模式，专门为做一些复杂事情准备，就是类似于你的这种。

### 当前桌宠 vs Claude Code 能力差距

| 能力 | 当前 DeskPet | Claude Code | 差距 |
|------|------------|-------------|------|
| 工具调用 | AgentLoop tool_use（有，但用得保守） | 多轮无限循环 | 默认轮数上限要放开 |
| 工具集 | 7 个 OS 工具：read_file / write_file / edit_file / list_directory / run_shell / web_fetch / desktop_create_file | 数十个：Read / Edit / Write / Glob / Grep / Bash / WebFetch / WebSearch / TodoWrite / NotebookEdit / Agent (delegating subagents) … | 至少要补 Glob / Grep / WebSearch / TodoWrite |
| 任务分解 | 无 | TodoWrite + 状态机 | 缺 |
| 长任务上下文 | 短上下文（最近 N 条 + memory recall） | 200k token + chunked 文件读取 | 长文件读不进、跨文件 reasoning 弱 |
| 子任务 / 并行 | 无 | Agent (subagent spawning) | 缺 |
| 长期记忆 | 有（BGE-M3 + sqlite-vec L1+L2+L3） ✅ | 简单 | DeskPet 这块反而更强 |

### 设计提案

**入口（产品层面）**：
Toolbar 加一个明显的"Code 模式"切换按钮（或 `/code` 命令）。开启后：
- 系统 prompt 切到"作为编码 + 复杂任务助手"模板
- AgentLoop 的 `max_iterations` 从默认 ~5 放宽到 50+
- TodoWrite 工具自动可用
- 工具集解锁完整列表
- 默认权限策略变宽松（read 类工具 yes-always、shell/write 类仍弹 PermissionPopup）

**最小工具补丁**（按优先级）：

1. **Glob**（按文件名 pattern 找） — 简单 `pathlib.glob`
2. **Grep**（按内容找） — wrap `ripgrep`，frozen bundle 里 ship 一个 rg.exe
3. **TodoWrite** — 在 SessionDB 里加 todos 表（也可走前端）
4. **WebSearch** — wrap Bing/Google API 或 DuckDuckGo
5. **Agent (subagent)** — 让一个 LLM 调用启动另一个 LLM 实例做子任务，结果汇总。复杂但价值高。

**LLM 适配**：
- 复杂任务建议优先用云端（gpt-5.5 / Claude Sonnet / Qwen3.6-plus），本地小模型大概率搞不动多轮 tool_use loop。Settings 里允许"Code 模式专用 LLM"独立配置（可以跟普通陪伴的 LLM 不同）。

**UI 反馈**：
- Code 模式跑长任务时桌宠左/右上角加一个滚动的"thinking dots" / TodoWrite 进度
- 跑长任务期间桌宠 Live2D 表情切到"思考"动作

### 工作量
大（**1-2 个 sprint**，不是单 PR）：

- Phase 1 (1 周)：补 Glob/Grep/TodoWrite 三个工具 + 入口 toggle + max_iterations 放宽
- Phase 2 (1 周)：WebSearch + 系统 prompt 模板 + UI feedback
- Phase 3 (1-2 周)：Agent (subagent) 这个最复杂，要解决 LLM 实例共享 + cost / token 控制

### 跟其他 issue 的关系
- 跟 #13（语音也能调工具）正交，互不依赖
- 配合 #15 一起做最香：用户说"帮我把 D:\projects\deskpet 这个项目跑通测试" → 语音激活 + 进 Code 模式 + 跑长链路 → 全自动

---

## 16. 上下文丢失：LLM 看不到刚说过的话

### 表现
真实场景（用户 2026-05-07 截图）：

```
用户: 我希望你帮我做一个VPN的项目，在G:\projects\VPN下面
桌宠: 可以呀～我看了下 G:\projects\VPN 目前是空目录。
       你想做哪种 VPN 项目？...
用户: 我想要做一个企业版本的重点是稳定和好用
桌宠: 懂啦～企业版就别堆花哨功能...
用户: 太复杂了，重点是稳定的速度和操作简单，给C端用户用的
桌宠: 明白～那就别做太"专业工具化"了...
用户: 好的呀，请开始帮我做把
桌宠: 好呀好呀～我准备好啦 🐾 不过你还没告诉我要做什么呢。  ← ❌ 失忆
       把需求发我一下，比如：写文案、改代码、整理文件...
```

整段 VPN 上下文 LLM 完全无视。

### SessionDB 数据完整 ✅
查 `state.db` 的 messages 表，#5513-5518 完整记录了 VPN 5 轮对话。问题不在持久化，在**装载到 LLM**。

### 根因（设计层面）
当前 `ContextAssembler.assemble()` 的产出 `ContextBundle` 只有这些字段：

```python
# backend/deskpet/agent/assembler/bundle.py:238
@dataclass
class ContextBundle:
    frozen_system: str = ""
    skill_prelude: str = ""
    memory_block: str = ""           # ← L2 近期对话 + L3 RRF 召回，文本形式
    tool_schemas: list[dict] = ...
    # ❌ 没有 history: list[dict] 字段
```

L2 近期对话被 `_render_l2_l3()` 渲染成纯文字，每条还被 truncate 到 200 字符：

```python
# backend/deskpet/agent/assembler/components/memory.py:130-143
parts.append("## 近期对话 (L2, recent)")
for row in l2_rows:
    content = row["content"][:200] + "…" if len(row["content"]) > 200 else row["content"]
    lines.append(f"- [{role}] {content}")
```

最后塞成一个 **system message**：

```
system: ## 近期对话 (L2, recent)
        - [user] 我希望你帮我做一个VPN的项目，在G:\projects\VPN下面
        - [assistant] 可以呀～我看了下 G:\projects\VPN 目前是空目录...（截到 200 字）
        - [user] 我想要做一个企业版本的重点是稳定和好用
        - ...
user: 好的呀，请开始帮我做把
```

LLM 把 `system` 角色看作指令而不是真实对话上下文。中文 LLM（gpt-5.5 / Qwen 等）对"文本化的对话历史摘要"特别不敏感，倾向于忽略 → 表现为失忆。

而且 main.py:1529 调用：

```python
_msgs = _bundle.build_messages(user_message=_text)   # ← history 参数没传
```

`build_messages` 的签名其实**支持** `history: list[dict]` 参数（bundle.py:251），但 ContextBundle 没暴露 raw history、main.py 没去 SessionDB 拉、所以传不进去。

### 永久修复方案

**方案 A（推荐 — 工作量小，效果立竿见影）**：

让 `ContextBundle` 多一个 `history: list[dict]` 字段，由 `MemoryComponent` 填，`main.py` 直接转发到 `build_messages`。

```python
# bundle.py — 加字段
@dataclass
class ContextBundle:
    frozen_system: str = ""
    ...
    memory_block: str = ""        # 保留：L3 召回继续走文本（远期记忆）
    history: list[dict] = field(default_factory=list)  # ← 新：L2 近期对话原文
    ...
```

```python
# memory.py — gather 里把 L2 raw rows 转成 OpenAI message format
bundle.history = [
    {"role": row["role"], "content": row["content"]}
    for row in l2_rows
    if row.get("content")
]
# memory_block 只保留 L3（RRF 召回），不再塞 L2 重复
```

```python
# main.py:1529 — build_messages 传 history
_msgs = _bundle.build_messages(
    user_message=_text,
    history=_bundle.history,
)
```

效果：LLM 看到的是真正的 OpenAI message history（user/assistant 多轮交替），跟 ChatGPT 网页版的 conversation 体验等价。VPN 那段对话直接以 raw 形式给 LLM，不再被截断。

**方案 B（保留现有架构 + LLM hint）**：
不改架构，只在 `_render_l2_l3` 里把 system 标签换成 user prefix：

```
"以下是我们最近的对话记录，请记住这些上下文：\n[user] xxx\n[assistant] yyy\n..."
```

LLM 容易理解。但仍然 truncate 到 200 字符 + 仍然在 system 角色，效果不如方案 A。

### 工作量
小（**1-2 小时**，包括测试）。

### 跟 issue 13 / 15 的关系
- 跟 #13（语音也走工具链路）正交，可以独立修
- 是 #15（Code 模式）的**前置条件** —— Code 模式跑长任务必须有正常的 message history，不然 LLM 第二轮就忘了第一轮做了啥

---

## 优先级建议

| 序号 | 问题 | 影响 | 修复工作量 | 状态 |
|------|------|-----|-----------|------|
| 1 | Settings 保存按钮 fetch 失败 | 用户必踩 | 小（1-2 小时） | ✅ 代码完成（P4-S21）— 待 Tauri rebuild ship |
| 7 | 没有退出按钮 | 用户必踩 | 小（10-30 分钟） | ✅ 代码完成（P4-S21）— Toolbar ⏻ + 系统托盘 + Quit 全做了；待 rebuild |
| 8 | 启动弹 cmd 黑窗 | 用户必踩 + 第一印象差 | 极小（5 分钟） | ✅ 代码完成（P4-S21）— CREATE_NO_WINDOW；待 rebuild |
| 9 | 技能商店长 description 撑爆 | 装了 docx 之类大 skill 才踩 | 极小 | ✅ 代码完成；待 rebuild |
| 10 | 双击空白蓝屏 | 用户必踩 | 极小 | ✅ 代码完成；待 rebuild |
| 11 | 不能拖动桌宠位置 | 用户必踩 | 极小 | ✅ 代码完成；待 rebuild |
| 12 | 旧 config schema 残留 → sealos 401 + "初始空"诉求 | 升级用户必踩 | 小（spec 加 datas + seed 迁移） | ✅ 完成（P4-S21 + 已部署）— spec 打 config.toml + seed 自动迁移 |
| 13 | 语音输入走不到工具调用链路 | 用户必踩 | 中（voice_pipeline 接入 AgentLoop） | ✅ 完成（P4-S21 + 已部署）— + auto_mode toggle + voice TTS prompt |
| 14 | Toolbar `chat_v2` toggle 已冗余 | 视觉混淆 | 极小（删 UI 元素） | ✅ 代码完成；待 rebuild |
| 15 | "Code 模式"（类 Claude Code 能力） | feature | 大（1-2 sprint） | ✅ 已完成（P4-S22 + 已部署 + 集成测试通过） |
| 16 | LLM 看不到对话历史，多轮失忆（VPN bug） | 用户必踩 | 小（1-2h） | ✅ 完成（P4-S21 + 已部署）— ContextBundle.history 真实 messages[] |
| 2 | C 盘临时空间 7 GB | 装 MSI 必踩 | 中（external cab 重 build） | 待修 |
| 3 | SmartScreen 警告 | 心理障碍 | 大（要买证书） | 待修 |
| 4-6 | 已绕过 | 无 | 不用动 | — |

下一次 sprint 优先攻克 1、7、8、9（都很小，可以一并重 build MSI），然后 2。
