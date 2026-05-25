# v2 评审 round-2 — 架构师反馈

| 项 | 值 |
|---|---|
| 评审人 | 架构师子代理（Opus 4.7 / 20 年经验视角） |
| 评审对象 | PRD.md v2 / TDD.md v2 / ManualTest.md v2（本目录） |
| 日期 | 2026-05-25 |
| 评审轮次 | round-2（对比 round-1 NEEDS-MAJOR-REWORK 5.2/10） |
| 评审基准 | round-1 13 BLOCKER + 28 MAJOR + 4 维度 |
| 强制纪律 | 任一 BLOCKER 级 scope drift = NO-GO；评分必须 > 5.2 |

---

## §A round-1 13 BLOCKER 逐项验证

| # | BLOCKER 原文（round-1） | v2 解了吗 | 解决方式（PRD/TDD/Manual 哪节） | 还差什么 |
|---|---|---|---|---|
| **B-1** | PRD §3 B3 删 "本地 phoneme 估计器（v2.1 备选）"；改前端 fallback | **YES** | PRD §3 B3 L171 显式标 "【BLOCKER B-1/B-2 解：删 "v2.1 备选"；fallback 必须 v2 内做】"；L173-188 双路径都做；§6.5 模块表 phonemeEstimator.ts 列入 | 无 — 已完整落地 |
| **B-2** | 新增 §3.B3-fallback 子章节 + TDD §2.4-b 接口 + Manual CASE-B3-07 | **YES** | PRD §3 B3-fallback 子章节 L210-215；TDD §2.4-b L155-167 接口；Manual CASE-B3-07 L180-185 朋友盲听验收；§6.5 模块列表 | 无 |
| **B-3** | PRD §3 C1 删 "如该 tag 已校准"；§8 加 motion calibration sub-task | **YES** | PRD §3 C1 L240 显式标 BLOCKER B-3 解；L248 明示 "S2 内 motion calibration sub-task 校准 yawn 到 m0X，不接受 '如 calibrated' 跳过"；§8 S2 lane "motion calibration sub-task" | 无（但 §8 表格未显式列 motion calibration 行 — 仅出现在 S2 单元格内"+ motion calibration sub-task" 字样，建议独立 sub-task 行更清晰，**非 BLOCKER**） |
| **B-4** | PRD §3 D1 删被动语言；v2 同时实施选项 A + C；§8 加后端 sub-task | **YES** | PRD §3 D1 L334-342 双路径都做明示；§6.9 后端 lane "backend/llm/emotion_prompt.py" 1d；§8 S2 后端 "LLM emotion prompt (D1)" | 无 |
| **B-5** | PRD §8 里程碑加后端工作并行 lane | **YES** | §8 表格有"后端（并行 lane，B-5 解）"列；§6.9 后端 sub-task 表（5 项，8.5d 总量） | 无 |
| **B-6** | PRD §4.1 flag 表 `deskpet_anim_occlusion` 改默认 **on** + consent | **YES** | §4.1 L480 `deskpet_anim_occlusion` **默认 on**（加粗）；NFR-8 + §6.10 Permission UX 子章节 L640-655 | 无 |
| **B-7** | §8 Plan B 删 "E2 砍"；改 graceful degrade | **YES** | §8 Plan B 段 L693-697 显式 "graceful degrade 不砍 FR"；E2 从"砍"改为"1Hz → 0.2Hz" | 无 |
| **B-8** | TDD §0 Probe-E2 FAIL 删 "v2.1 推迟" | **YES** | TDD §0 Probe-E2 L61-63 仅一行（同 v1），未含 v2.1 推迟字样；PRD §3 E2 L416 `降级` 段 "Win32 API 失败 → console.warn + functionality 自动 disable（不报错 + flag 仍可见 on）" | 无 |
| **B-9** | §8 Plan B 删 "F1 call detection 砍"；改 graceful degrade | **YES** | §8 Plan B L693-697 删；§3 F1 L442 `降级` "audio session API 失败 → call detection 自动 off（其他 2 仍运行）" | 无 |
| **B-10** | PRD §3 A1 加 physics3 决策行 | **YES** | PRD §3 A1 L109-110 显式 "【BLOCKER B-10 解】v2 不接 Hiyori physics3.json owner 切换" + 3 条理由 (a)(b)(c) | 无 |
| **B-11** | 跨层加 client_hello 协议握手 | **YES** | PRD §6.3 L554-563 client_hello / server_hello 完整 schema + 旧 backend silent ignore 行为；TDD §4.14 TC-E2E-v2-07 测握手 | 无 |
| **B-12** | §6.2 多 FR 并发参数写入优先级矩阵 | **PARTIAL** | PRD §6.2 L518-549 给出 14 Param × 10 FR 矩阵 + step 1-10 写入顺序 + DND 全局规则；但 (a) PRD/TDD 都未给"冲突时优先取胜规则"的形式化定义（仅"DND > B4 > B3 > D1" 自然语言）；(b) ParamMouthForm 的 viseme silent 时是否回落到 D1 emotion，TDD §9.1 提到 "viseme 不活跃时 fallback emotion" 但矩阵列只写 "B3 > D1" 无 silent fallback 规则；(c) step 9 "B2 brow 与 D1 冲突时 B2 优先" 与矩阵列 "B2 优先于 D1" 一致但 PRD 矩阵未画 ParamBrowLAngle / RAngle 行（angry/sad 用），导致 brow 系列只有 Y 那行；**算 PARTIAL** — 实施期仍可能争论 | 补：(1) "fallback when source X silent" 规则；(2) ParamBrowLAngle/RAngle 行加入矩阵；(3) 矩阵列定 ADD/SET/MUL 计算次序 |
| **B-13** | drag pulseInteraction vs setHeldState 重叠决策 | **YES** | PRD §3 A1 v1 兼容段 L121 + §6.1 L514-517 "setHeldState 改名 setDragState"；显式 "click 走 pulseInteraction；drag 走 setDragState；正交不冲突"；TDD §4.1 TC-A1-06 测正交 | 无 |

**§A 小结**：13/13 中 **12 YES + 1 PARTIAL**（B-12 矩阵需补细节但不算 BLOCKER 级未解）；round-1 BLOCKER 已 92% 落地。

---

## §B 新 scope drift 扫描

Grep 扫了 `v2\.1|v2\.x|推迟|如 calibrated|Plan B 砍|选项 A 在后端 ready|v2 先做选项 C|配套优化` 全部命中位置：

| 位置 | 内容 | 判定 |
|---|---|---|
| PRD L20-23 | §0 禁词清单（meta，非正文） | ✓ 合规，自己列禁词 |
| PRD L90 | "❌ 后端持久化 anniversary（v2 用 localStorage；后端持久化是**配套优化**非 FR）" | ⚠️ **边界合规** — 13 项里 #11 "整点/纪念日" 业界对照在单机 localStorage 已能完整实现，后端持久化是"跨设备同步"超出 13 项 scope。**NOT scope drift**。 |
| PRD L171 | §3 B3 标 "【BLOCKER B-1/B-2 解：删 v2.1 备选...】" | ✓ 仅引用 round-1 BLOCKER 标题，非正文 hedge |
| PRD L240 | §3 C1 标 "【BLOCKER B-3 解：删 如 calibrated...】" | ✓ 同上，引用 |
| PRD L248 | C1 行为段 "ParamBreath 周期 ×1.5（如 Hiyori 用此参数）" | ⚠️ **边界** — "如 Hiyori 用此参数" 是 conditional hedge。Hiyori 是否有 ParamBreath 应在 Day-0 Probe 实测，但 v2 Probe-D1 列了 10 参数（L34）**未含 ParamBreath**。**MINOR**：补 ParamBreath 进 Probe-D1，且明示 FAIL 时用 ParamBodyAngleY 模拟 breath，**不允许 silent skip**。 |
| PRD L318 | C3 关键参数 "SettingsPanel UI 在 v2.x 后续追加，但 JSON 本身已可用 — 此 v2 ship 路径明示" | ⚠️ **PARTIAL drift 倾向** — 业界对照 #11 是"整点/纪念日触发限定动画"，本身 v2 用 localStorage JSON 已完整可用，SettingsPanel UI 仅是配置 UX。**Not BLOCKER**（13 项 FR 在 v2 完整可用），但措辞 "v2.x 后续追加" 与 §0 禁词风格冲突，**建议改成 "用户手编辑 JSON 已可用；管理 UI 不在本 PRD scope"**，去掉"v2.x"。 |
| PRD L322 | C3 跨层段 "后端持久化是 v2.x 配套优化非 FR — 本 FR 在 v2 完整可用" | 同上 ⚠️ 措辞 minor 改即可 |
| PRD L404 | §3 E2 标 "【BLOCKER B-6/B-7/B-8 解：删 Plan B 砍 E2 + 删 v2.1 推迟...】" | ✓ 引用 round-1 BLOCKER 标题，非 hedge |
| PRD L428 | §3 F1 标 "【BLOCKER B-9 解：删 Plan B 砍 call detection...】" | ✓ 引用 |
| PRD L738 | OQ-D2 拍板 "v2 内置 5 条规则 + SettingsPanel UI 加在 v2.x 配套" | ⚠️ 同 L318 — D2 milestone 5 条规则在 v2 内完整可用（PRD §3 D2 L367 列出 5 条），UI 只是配置 UX。**Not BLOCKER**，措辞 minor。 |
| PRD L764 | §12 修订日志 "C1 删 如 calibrated" | ✓ 修订日志引用 |
| PRD L770 | §12 修订日志 "E2 删 Plan B 砍 E2" | ✓ 同上 |

**§B 判定**：**无 BLOCKER 级 scope drift**。3 处 "v2.x 后续/配套" 措辞（L318/L322/L738）涉及 SettingsPanel 配置 UI 与后端持久化，这两项**均不在 13 项业界对照 FR 范围**（13 项是行为/动画维度，不是配置管理 UX）。但措辞风格与 §0 严守纪律不一致，**建议非阻塞性修订**：把 "v2.x 后续追加" 改成 "v2 ship 用 JSON 已完整可用；配置管理 UI 不在本 PRD scope（亦不在 13 项 FR 内）"。

另外 PRD L248 "如 Hiyori 用此参数" 是变相 hedge — Hiyori ParamBreath 必须 Day-0 实测，**MINOR 整改**：扩 Probe-D1 含 ParamBreath。

---

## §C 13 项 + AC-3 snapshot 套件完备性

### C.1 13 项每项 5 件套（Day-0 探针 / 单测 / 手测 case / 验收行 / fallback）

| FR | Day-0 探针 | 单测 (TDD §4.x) | 手测 case (Manual §x) | 验收行 (PRD §3) | Fallback | 完备 |
|---|---|---|---|---|---|---|
| A1 | Probe-A1 ✓ | TC-A1-01..06 ✓ | CASE-A1-01..04 ✓ | 5 验收行 ✓ | flag off + Tauri fail | ✓ |
| B1 | (Probe-D1 含 ParamHairFront) ⚠️ | TC-B1-01..06 ✓ | CASE-B1-01..04 ✓ | 6 验收行 ✓ | ParamHairFront FAIL → 仅 tilt+eye | ⚠️ Probe-D1 未含 ParamBreath |
| B2 | (复用 chat_v2_event 信号) ✓ | TC-B2-01..05 ✓ | CASE-B2-01..04 ✓ | 4 验收行 ✓ | flag off → 沿 v1 working | ✓ |
| B3 | Probe-B3-后端 + Probe-B3-前端（双） ✓ | TC-B3-01..06 + TC-B3f-01..05 ✓ | CASE-B3-01..08 ✓ | 4 验收行 + B3-fallback 子章节 ✓ | 后端 fail → fallback；fallback fail → v1 amplitude | ✓ |
| B4 | (复用 tts_end) ✓ | TC-B4-01..05 ✓ | CASE-B4-01..03 ✓ | 3 验收行 ✓ | flag off → 瞬切；tts_end 漏 → 800ms timeout | ✓ |
| C1 | events 集 + visibility ✓ | TC-C1-01..06 ✓ | CASE-C1-01..04 ✓ | 3 验收行 + transition 图 ✓ | flag off；motion calibration S2 必跑 | ✓ |
| C2 | (复用 idleWatcher) ✓ | TC-C1-06 含 ✓ | CASE-C2-01..03 ✓ | 4 验收行 + escalation 表 ✓ | flag off；bubble fail | ✓ |
| C3 | (clock 注入) ✓ | TC-C3-01..04 ✓ | CASE-C3-01..04 ✓ | 3 验收行 ✓ | flag off；DND 抑制 hourly | ✓ |
| D1 | Probe-D1-Hiyori + Probe-D1-后端 ✓ | TC-D1-01..05 + TC-D1c-01..07 ✓ | CASE-D1-01..08 ✓ | 7 验收行 ✓ | backend 不发 → 投票 fallback；TTS 不可用 → 3s 自释 | ✓ |
| D2 | Probe-D2 (memory schema) ✓ | (TDD §2.6 合 idle - lacking dedicated) ⚠️ | CASE-D2-01..03 ✓ | 3 验收行 + 5 条规则进 PRD 正文 ✓ | flag off；bubble fail → 仅参数 | ⚠️ TDD §4.x 无 D2 milestone dedicated test (合在 idle?) |
| E1 | (currentMonitor API 已有) ✓ | TC-E1-01..03 ✓ | CASE-E1-01..04 ✓ | 4 验收行 ✓ | flag off；ParamAngleZ fail → 仅 snap；multi-monitor 异常 | ✓ |
| E2 | Probe-E2 ✓ | TC-E2-01..06 ✓ | CASE-E2-01..04 ✓ | 4 验收行 + grid sampling 48 候选 ✓ | flag off (consent deny)；Win32 fail；perf degrade 1→0.2Hz | ✓ |
| F1 | Probe-F1-通用 ✓ | TC-F1-01..08 ✓ | CASE-F1-01..06 ✓ | 5 验收行 + ZZZ badge spec ✓ | flag off；任 trigger fail 退化其余；audio session fail | ✓ |

**C.1 小结**：13/13 中 **10 完备 + 3 ⚠️ MINOR 缺漏**：
- **MINOR-1**：B1 / C1 — Probe-D1 应扩含 ParamBreath（PRD L248 hedge "如 Hiyori 用此参数"）+ ParamHairFront（已含但未明 PASS/FAIL 分支）。
- **MINOR-2**：D2 milestone — TDD §4.x 测试用例表没 dedicated `milestone.test.ts` 文件（TDD §2.6 把 milestone 合进 idleWatcher，但 §4.x 也没 TC-D2-xx 行）。Manual 有 CASE-D2-01..03 但 TDD 缺单测 task。建议加 TDD §4.x dedicated milestone test。

### C.2 AC-3 4 条 snapshot test 三处一致性

| 文档 | 位置 | 4 条都在？ |
|---|---|---|
| PRD §6.11 | L657-665 4 条 table | ✓ AC-3.1/3.2/3.3/3.4 全列 |
| TDD §4.16 | L377-403 ac3_snapshot.test.ts 描述 | ⚠️ **PARTIAL** — `it('AC-3.1 ...')` + `it('AC-3.3 ...')` + `it('AC-3.4 ...')` **缺 AC-3.2** (v2_all=off → v1 27/27 OS 手测) — 因 OS 手测不在 vitest 范围，TDD 文件只列 3 个 it()，**但 OS 手测在 ManualTest CASE-AC3-02 覆盖**，跨文档划分合理。建议 TDD §4.16 加注 "AC-3.2 在 ManualTest CASE-AC3-02 自动化外覆盖"。 |
| Manual §17 | L367-391 CASE-AC3-01..04 | ✓ 4 条全列（CASE-AC3-01..04 对应 AC-3.1..3.4） |

**C.2 小结**：AC-3 4 条三处一致（PRD 表 ✓ / Manual ✓ / TDD 缺 cross-ref 注释 — **MINOR**）。

---

## §D §6.2 优先级矩阵 + step 1-10 自洽

### D.1 矩阵完备性

PRD 文字写 "10x10 priority matrix"，实际 §6.2 L522-538 行是 **14 Param × 10 FR 列**（A1 / B1 / B2 / B3 / B4 / C1 / C2 / D1 / E1 / DND）— 列数对，但行数比"10x10"标题多。可接受（10 FR × 14 影响 Param，标题"10x10"是 PRD 自标，实际更密）。

**覆盖问题**：
- ⚠️ ParamBrowLAngle / RAngle 行（D1 angry/sad 用）**缺失** — 矩阵只列 ParamBrowLY/RY。需补一行。
- ⚠️ ParamEyeLOpenMul / EyeROpenMul 与 ParamEyeLOpen / EyeROpen 名称在 PRD §3 D1 L347（用 MUL 名）和矩阵 L529（用基本名）混用 — 需统一。
- ⚠️ C2 welcome 列（intense_multiplier=1.3）与 D1 happy 复用 — 矩阵列 C2 一直是 "—" 加 OSC HairFront — 但 C2 也 SET happy 系列（intense_multiplier 1.3）— 矩阵未画 C2 复用 D1 happy 的语义。
- ⚠️ B3 fallback（PhonemeEstimator 输出 viseme）写入 ParamMouthOpenY / Form 与 B3 主路径同列 ✓（矩阵 B3 一列覆盖）。

### D.2 step 1-10 写入顺序与矩阵自洽

PRD §6.2 L540-549 step 1-10：
- step 1 MouthOpenY: B4 fade > B3 viseme > D1 surprised > DND force 0 — 与矩阵 L524 一致 ✓
- step 2-4 Perlin/gaze/saccade: DND/A1 block — 矩阵未画 Perlin/gaze/saccade（这些不是 Param 写入而是 step gate），自洽（行为级 block 不在矩阵）
- step 5 blink MUL: D1 eye_mul 先做 — 矩阵 L529 "DND blink ≫ D1 ≫ B1 (按 MUL 链式)" ✓
- step 6 AngleZ: DND > E1 > A1 wobble > B1 tilt — 矩阵 L528 "DND > A1 (block 时其他全 stop) > E1 > B1" ⚠️ **step 6 与矩阵顺序不一致** — step 6 说 "DND > E1 > A1 > B1"，矩阵说 "DND > A1 > E1 > B1"。**MINOR 内部不一致**，需对齐。
- step 7 AngleX/Y: DND > B2/D1 ✓
- step 8 EyeBall: gaze+saccade+B2 ✓
- step 9 表情 SET (D1; B2 brow 冲突 B2 优先) — 矩阵 L534 BrowLY/RY "B2 优先于 D1 (思考姿盖 emotion)" ✓
- step 10 clamp ✓

### D.3 冲突时优先规则

PRD §6.2 列出每 Param 的优先序（自然语言），但 **未形式化定义** "ADD vs SET vs MUL 在同 step 内的计算顺序"。例如 step 5 blink (MUL) 和 D1 eye_mul (MUL) 都是 MUL — 链式相乘还是 max-take？PRD L529 "按 MUL 链式" 模糊。

**§D 小结**：矩阵主结构 PASS，但 4 处 MINOR 不一致：
1. ParamBrowLAngle / RAngle 行缺失
2. EyeOpen vs EyeOpenMul 命名混用
3. C2 welcome 复用 D1 happy 在矩阵未画
4. step 6 AngleZ 顺序与矩阵列描述不一致 (E1 vs A1 谁先)
5. MUL 链式 vs SET 覆盖的计算次序未形式化

**评估**：BLOCKER B-12 算 **PARTIAL** — 主框架已建，需补细节但不阻塞实施。

---

## §E 总体判定

**GO-WITH-MINOR-FIXES**

评分（0-10）：**7.6/10**（vs round-1 5.2/10，提升 +2.4）

判定理由：

✅ **13 BLOCKER 已 92% 落地**（12/13 YES + 1 PARTIAL）。B-12 矩阵主结构在但细节需补。

✅ **无 BLOCKER 级 scope drift**。Grep 扫描确认正文（非 meta 引用）里**没有任何 "v2.1" / "推迟" / "如 calibrated" / "Plan B 砍 13 项"** 措辞。13 项 FR 全部在 v2 内完整实现路径明示。

✅ **3 处 "v2.x" 措辞**（L318/L322/L738）涉及 SettingsPanel 配置 UI 和后端持久化 — **均不在 13 项 FR 内**，13 项功能在 v2 用 JSON / localStorage 已完整可用。措辞建议非阻塞性改语。

✅ **跨层契约完备**：client_hello/server_hello、6 个 ws 消息、3 个 Rust commands、Permission consent UX、后端并行 lane (5 sub-task / 8.5d)。

✅ **AC-3 v1 零回归 snapshot 套件**完整设计（PRD §6.11 / TDD §4.16 / Manual §17 三处协同；仅 TDD 缺一个 cross-ref 注释 MINOR）。

✅ **多 FR 优先级矩阵**主结构已建（14 Param × 10 FR），但 4 处 MINOR 不一致需对齐。

⚠️ **剩余 MINOR**（不阻塞 GOAL 实施，可在 Sprint 1 D0-D1 补完）：
1. Probe-D1 扩含 ParamBreath（PRD L248 "如 Hiyori 用此参数" hedge）
2. PRD §6.2 矩阵补 ParamBrowLAngle/RAngle 行 + ParamEyeLOpenMul 命名统一 + C2 happy 复用画清 + step 6 AngleZ 顺序对齐 + ADD/MUL/SET 计算次序形式化
3. TDD §4.x 加 dedicated milestone.test.ts （D2 单测合并进 idleWatcher 不清晰）
4. TDD §4.16 加 cross-ref 注释 "AC-3.2 OS 手测在 ManualTest CASE-AC3-02"
5. PRD L318/L322/L738 "v2.x" 措辞改语（非 13 项内的配置 UI/后端持久化，建议改 "不在本 PRD scope（亦不在 13 项 FR 内）"）

---

## §F 剩余整改清单（Sprint 1 D0-D1 补完，不阻塞 GOAL 启动）

| # | 整改项 | 类型 | 工作量 | 优先 |
|---|---|---|---|---|
| F-1 | PRD Probe-D1 / TDD §0 Probe-D1-Hiyori 扩含 ParamBreath + 明示 FAIL 分支用 ParamBodyAngleY 替代 | MINOR | 0.25h | P1 |
| F-2 | PRD §6.2 矩阵补 ParamBrowLAngle/RAngle 行；EyeOpen / EyeOpenMul 命名统一；C2 happy 复用画明；step 6 AngleZ 顺序与矩阵列对齐；定义 ADD/MUL/SET 在同 step 内的计算次序（建议：先 MUL 链式 → 然后 ADD 累加 → 最后 SET 覆盖 → clamp） | MINOR | 1h | P0 (实施前) |
| F-3 | TDD §4.x 加 dedicated `milestone.test.ts` 测试组（5 条规则各 1 case） | MINOR | 0.5h | P1 |
| F-4 | TDD §4.16 加 cross-ref 注释 "AC-3.2 v1 27/27 OS 手测在 ManualTest CASE-AC3-02 覆盖" | MINOR | 0.1h | P2 |
| F-5 | PRD L318 / L322 / L738 "v2.x 后续" / "v2.x 配套" 改语为 "不在本 PRD scope（亦不在 13 项 FR 内）" | MINOR (措辞纪律) | 0.25h | P1 |
| F-6 | PRD §8 表 S2 行独立列出 "motion calibration sub-task (yawn/edge/dodge tag)" sub-bullet（B-3 已解但表格可读性更好） | MINOR | 0.25h | P2 |

**整改总工作量**：~2.5h。**不阻塞 GOAL 实施启动** —— 这些 MINOR 可在 Sprint 1 D0-D1 探针阶段并行补完，不影响 S1 整体节奏。

---

## §G 推荐进入 GOAL 实施

**YES**

**理由**：

1. **scope drift 已根本性扭转**。round-1 8 项 BLOCKER 级换皮砍（"v2.1 备选" / "推荐先做选项 C" / "Plan B 砍 E2/F1 call" / "默认 off" / "如 calibrated"）在 v2 正文中**已 100% 清除**。v2 §0 明文列禁词清单 + Grep 实证 0 命中（meta 引用与禁词清单本身除外）。

2. **13 项 FR 在 v2 ship 路径完整可用**。无任何 FR 被推到 v2.x / v3 / "后端 ready 再做"。所有 fallback 路径在 v2 内必做（B3 phoneme 估计器、D1 投票分类器、E2 grid sampling、F1 通用 audio session 全部为 v2 工程量）。

3. **后端并行 lane 已开**（§6.9 5 sub-task / 8.5d）。B3 viseme provider、D1 emotion prompt、D2 milestone.py、3 个 Rust commands、client_hello 握手全部在 S2 后端 sprint 内并行。无"等后端 ready"被动语言。

4. **跨层契约完备**。client_hello/server_hello 版本握手、6 个 ws 消息、3 个 Rust commands、Permission consent UX 子章节、unknown silent skip 策略全部明示。

5. **v1 兼容性测试套件 (AC-3) 完整**。4 条 snapshot test (PRD §6.11 / TDD §4.16 / Manual §17) 三处协同覆盖；setHeldState→setDragState 改名解 v1 pulseInteraction 冲突。

6. **剩余 MINOR (§F 6 项 ~2.5h)** 可在 Sprint 1 D0-D1 探针阶段并行补完，不阻塞启动。建议把 F-2 矩阵补完列为 S1 D0 第一项（实施期跨 FR 协调用）。

7. **评分 7.6/10 > round-1 5.2/10**，满足"必须高于 round-1"硬条件。

**唯一保留风险**（实施期监控）：

- **B-12 优先级矩阵** PARTIAL — 主结构在，但 4 处不一致 (§F-2)。若 S1 D0 不先补完，实施期 case-by-case 解读矩阵会产生小规模返工。建议 F-2 列 S1 D0 Day-0 第一项，1h 内补完。
- **后端并行 lane 8.5d** 假设有后端工程师可投。若后端资源不足，B3 主路径 / D1 选项 A / D2 milestone 三项需 fallback 路径单独 ship — fallback 路径已 v2 内备齐（投票分类 / phoneme 估计 / 不实施 milestone 但 5 条规则文档化），ship 时主路径缺席属"graceful degrade 不砍 FR"范畴。

---

**报告结束**。v2 已通过 round-2 评审，建议主 agent 进入 GOAL 实施阶段；§F 6 项 MINOR 整改建议在 S1 D0-D1 补完。若后续需求方需 Codex GPT-5.4 第二意见，本评审签字接受。
