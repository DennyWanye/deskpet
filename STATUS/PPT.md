# PPT 生成 — 模块专项状态

> **最后更新**: 2026-06-22
> **用途**: 一页看清 DeskPet「生成 PPT」全链路怎么工作、由哪些文件承担、能力边界与已知短板。要动 PPT 功能前先读这里。
> **同级**: [status.md](./status.md)(全局) · [AgentLoop.md](./AgentLoop.md)(执行引擎)

---

## 1. 一句话

桌宠把「一个主题 / 一段大纲 / 一份研究报告」变成可下载、可二次编辑的 `.pptx`。
**大纲由 LLM(+ppt-generate skill)产出 → `ppt_create` 工具渲染成 pptx**。两层职责分明:
**skill/LLM 层管「写什么」(内容/大纲),工具层管「画成什么样」(版式/渲染)**。

---

## 2. 端到端链路(一次「做个 PPT」发生了什么)

```
用户说「做个 X 的 PPT」
  │
  ├─[skill 层] ppt-generate/SKILL.md 指导 LLM:
  │     ① 判视觉模式(AI整页生图 / 模板填充 / 朴素主题)
  │     ② 把主题→结构化 JSON outline(list[SlideOutline])  ← 大纲质量在这里决定
  │
  ├─[工具层] ppt_create(outline, theme, template, ...)  (ppt_tools.py)
  │     ① parse_outline → SlideOutline.normalize()(只清洗字段,不改内容)
  │     ② 视觉风格仲裁:wants_fullbleed? → chosen_template
  │     ③ 配图:image_full/image + image_prompt → _autofill_image_prompts
  │     │        → image_tools.generate_images() → gpt-image-2 出图 → 插图
  │     ④ 三条渲染路径分派(见 §3)
  │     ⑤ 视觉评审闭环(渲染→多模态看→修→重渲,≤2轮)
  │     ⑥ 落盘 OutPut/PPT/ + 预览图进聊天 + (带图)自动打开
  │
  └─[回复] 报路径 + 页数 + 主题
```

**同步 vs 异步**:纯文本/模板 deck 同步出;带 `image_prompt` 的 deck 走后台
(`_handle_ppt_create` → `_bg_job`):秒回「制作中」,出完图 notifier 推回 + 自动打开。

---

## 3. 三条渲染路径(ppt_tools.py)

| 路径 | 函数 | 何时走 | 产物特点 |
|---|---|---|---|
| **① 模板设计页填充** | `_render_with_design_pages` (:2950) | 传了 `template`(大类名/路径)且解析出设计 deck | 最专业;复用现成设计页,内容填进文字槽,AI 图换图位(`_swap_design_picture`) |
| **② 模板版式填充** | `_render_with_template` (:3042) | ① 失败回退;模板只有 bare layout(占位符) | 干净但朴素;`_pick_template_layout` 中英布局名映射 |
| **③ from-scratch** | `_render_fromscratch` (:3302) | 无模板 / 模板解析失败 / AI 整页生图 | 代码摆 EMU 坐标;3 主题(minimal/dark/playful)× 10 版式 |

**版式**(`VALID_LAYOUTS`): title / section / bullet / two_column / image / image_full / quote / toc / chart / (+conclusion 自动判定)。每个 `_render_*_v2` 一个函数,经 `_RENDERERS` 分派。
**图表**:`chart` 走 python-pptx 原生 `add_chart`(✅ 可编辑,亮点)。

**分派核心逻辑**(`ppt_create` @:3152;`wants_fullbleed` 分派 @:3232 起):
```python
wants_fullbleed = any(image_full + image_prompt)            # AI 整页 → 不让默认模板劫持
chosen_template = template or (None if wants_fullbleed else _default_template())
if chosen_template:
    resolved = _resolve_template_for_render(chosen_template, topic=...)  # 大类名→预览图视觉选
    → _render_with_design_pages → (失败) _render_with_template
else:
    → _render_fromscratch + _visual_review_loop
```

---

## 4. 模板库 + 预览图视觉选模板(2026-06-20 重构)

- **源**:外部大库 `resources/PPT_Template/`(250 套·2.8GB·**gitignored**),两大类 `01 高级色`(221)/`02 高级简约`(29),每套是 15-19 页设计 deck + 同 stem 预览图。
- **选择**(`ppt_template_picker.py`):模板数以百计无法塞进 LLM schema 按名选 → **LLM 只挑大类**(高级色/高级简约/通用商务)→ 引擎把该类预览图 PIL 拼 contact sheet → **一次多模态调用**(`ppt_visual_review.vision_chat`)按主题选具体 .pptx → 映射回路径。容量超 90 随机采样并打日志。**全程优雅降级**:无库/无预览/vision 挂 → 随机回退;类空 → None 回落 from-scratch。
- **兜底**:`backend/deskpet/tools/ppt_templates/通用商务/`(3 套·~24MB·**git 跟踪**:现代商务汇报/水彩工作计划/极简PitchDeck,含预览图)。`template_library_root()` = 外部大库优先,缺失回退 bundled → 打包 app/新机器/无大库时模板功能不失效。
- **解析**:`_resolve_template_for_render`(大类名→视觉选)/ `_resolve_template_path`(直传 .pptx 路径或库内 stem)。env `DESKPET_PPT_TEMPLATE_ROOT` 覆盖库根,`DESKPET_PPT_DEFAULT_TEMPLATE` 设默认大类。

---

## 5. AI 配图(doubao-seedream-4.0)

- **2026-06-24 换模型**:relay 下线 gpt-image-2 → 默认切 `doubao-seedream-4.0`(真链路实测可用,返回 b64,接受 1024x1024/1536x1024/1792x1024,落 300s 读超时内)。同库 seedream-4.5/5.0-lite 也活着但**拒绝 1024、强制 ≥2K**,会打断侧栏 1024 出图路径故不选默认。换模型只改 `config.toml [image].model` 一行(代码默认 `image_tools._DEFAULT_MODEL` 同步改了)。
- `image_tools.generate_images(prompts)` 同步批量原语 → relay `images/generations`(模型走 `[image].model`)。`_MAX_ATTEMPTS=2`(**刻意**:省钱权衡 — 读超时/504 不重试防双倍扣费;只连接级失败重试,SSL 已归类瞬时)。
- `_autofill_image_prompts` 在渲染前把 `image_prompt` 批量出图回填 `image_path`;`image_full` 全幅铺图 / `image` 插图。失败优雅降级占位。
- 版式变体(`image_variant`):cover/split_left/split_right/top/card/quote,`_assign_image_layouts` 自动轮换;`_place_cover` object-fit cover 比例裁切零变形;`_set_fill_alpha` 真半透明遮罩。

---

## 6. 视觉评审闭环(ppt_visual_review.py + ppt_render.py)

「桌宠亲眼看每页」: `ppt_render.render_pptx_to_pngs_safe`(WPS COM `Kwpp.Application` 渲 PNG)→ `vision_chat` 把页图 768px base64 发多模态(gpt-5.5)→ 结构化 JSON 质检(溢出/截断/压主体/对比度/版式匹配)→ 应用可自动修动作:
- from-scratch 版(`_visual_review_loop`):`change_variant` / `shrink_text` → `_render_fromscratch` 重渲。
- 模板版(`_visual_review_loop_template`):`shrink_text` / `change_page`(ban 该设计页换页重填)→ 重渲。
最多 2 轮;vision 不可用静默降级零影响;`[ppt].visual_review` 可关;pytest 内跳过。

---

## 7. 关键文件

| 文件 | 职责 |
|---|---|
| `backend/deskpet/skills/builtin/ppt-generate/SKILL.md` | **大纲生成指导**(LLM 怎么把主题变 outline + 视觉模式判定 + 内容质量要求) |
| `backend/deskpet/tools/ppt_tools.py` (~3850 行) | 渲染引擎:三路径 + 分派 + 配图接线 + 视觉闭环编排 + schema |
| `backend/deskpet/tools/ppt_template_picker.py` | 模板库结构原语 + 预览图视觉选模板 + 兜底 |
| `backend/deskpet/tools/ppt_visual_review.py` | 多模态看图评审 + 共享 `vision_chat` 原语 |
| `backend/deskpet/tools/ppt_render.py` | WPS COM 渲 pptx→PNG(给视觉闭环 + 预览) |
| `backend/deskpet/tools/image_tools.py` | gpt-image-2 出图(`generate_images` / `_generate_png`) |

输出:`<user_data>/OutPut/PPT/deskpet-ppt-<ns>.pptx`;每页预览 PNG 作 `kind=image` artifact 进聊天。

---

## 8. 真机验证状态(2026-06-24 更新)

- **`ppt_pro` 惊艳生图路径 × doubao-seedream-4.0** ✅ **PASS(2026-06-24)** —— 补齐之前被 gpt-image-2 403 卡住、从未验证过的 happy path:主题「在AI时代,程序员的核心竞争力是什么?」→ 路由 ppt_pro(image_mode=true)→ 真 deepresearch(3 源)→ 大纲卡(带引用 `[^1][^2][^3]`)+ SendInput 真点确认 → **8× `images/generations 200`(seedream-4.0)** → `render path=fromscratch(惊艳)`(**未降级模板**)→ 2.88MB/8 页 pptx 落盘 → WPS 自动打开(8 页全 AI 整页图)。耗时 ~13min/8 图(超时预算内)。真测中发现 **C: 盘满**致持久化 disk I/O error,但 orchestration best-effort 降级未崩、清盘后自恢复。证据 [plans/manual-results-2026-06-24-ppt-seedream/](../plans/manual-results-2026-06-24-ppt-seedream/RESULTS.md)。
- **`ppt_pro` F1-F4 端到端逻辑链路** ✅ PASS(2026-06-22):F1 deepresearch 真调研(搜狗百科直连)→ F2 拟纲 6K+ 字流 → F3 大纲卡真渲染 + SendInput 真点击确认 → F4 首图实测判定(gpt-image-2 真 403→切模板)→ deck 5 页落盘自动打开(WPS)。TC-4 模板回退 PASS;TC-9 preempt 不杀确认链路 PASS。真测中**揪出并修复 2 真 bug**(路由缺口 / 渲染 executor 线程 hang 双根因)。证据 [plans/manual-results-2026-06-22-ppt-pro/](../plans/manual-results-2026-06-22-ppt-pro/)。
- **模板设计页 + 预览图视觉选** ✅ PASS(2026-06-20):LLM 选大类「高级色」→ 真 vision `vision chose id=77 → (177).pptx` → design-pages 填充 + 模板视觉闭环 2 轮 → 5 页产物。
- **AI 整页配图(gpt-image-2)** ✅ PASS(2026-06-20):「深海探秘」3 页 → gpt-image-2 出图 200×3 → 视觉评审 `issues=0` → 落盘 + 自动打开(WPS)。深海潜航器/幽光水母/海沟全屏电影感大图。
- 报告:[plans/manual-results-2026-06-20/REPORT-ppt-template-vision-pick.md](../plans/manual-results-2026-06-20/REPORT-ppt-template-vision-pick.md)。
- **环境受限**:relay gpt-image-2 403 配额墙阻断 ppt_pro 剩余约 13 用例(模型不可用回退/内容不误伤/调研降级等),待配额恢复补验。

---

> 📌 **`ppt_pro` 已实施完成 + 真机验收 ✅(2026-06-22)**：[plans/2026-06-21-ppt-deepresearch-pro/](../plans/2026-06-21-ppt-deepresearch-pro/00-PLAN.md)(v1.3 LOCKED, 6 轮 codex 对抗收敛) —
> 新工具 `ppt_pro`：主题 → deepresearch 充分调研 → 拟大纲(双模式防回退) → 用户确认(可改) → 首图实测判定(gpt-image-2 可达→惊艳整页生图 / 不可达→模板兜底)。
> 旧 `ppt_create` 仍在岗作直传路径(已给 outline / 已知模板时)。见 §4 全局里程碑(status.md 2026-06-22)。

## 9. 已知短板 / 缺口

1. **大纲质量(skill 层)** — outline 由 LLM 按 `ppt-generate/SKILL.md` 产;现实里内容常空泛/结构平庸/bullet 像讲稿。**根因在 skill/LLM 层,不在工具层**(工具只渲染)。当前 gpt-5.5 窗口仅 8000 也压制了大纲质量。→ **`ppt_pro` 已用 deepresearch 调研喂大纲对治**(见上 📌);旧 `ppt_create` 直传路径仍受此短板影响。
2. **渲染 hang 需埋点根因定位(残留)** — ppt_pro 渲染 executor 线程 hang 已修双根因(bundled 模板直传 + asdict 转 dict),但 RESULTS 建议后续加 log 锚点确认无第三处阻塞路径。
3. ~~**SKILL.md 已 stale**~~ ✅ 已修(commit `2e83c1fe`):删旧 3 模板名,改「按大类名选」+ 路由优先 `ppt_pro`。
4. **「6 短板」**(2026-06 评估,主动 defer):①信息密度失控 ②图标简陋 ③图片裁切生硬 ④图表配色不随主题 ⑤内容数≠模板槽数(已部分被鲁棒选页缓解)⑥细节装饰基础。
5. **打包/分发**:外部大库 gitignored 不进安装包;打包 app 仅有 3 套 bundled 兜底。
6. **成本/时延**:每次模板生成 +1 vision 选图调用;AI 配图每张 1-3 分钟(外部 relay 速度)。
