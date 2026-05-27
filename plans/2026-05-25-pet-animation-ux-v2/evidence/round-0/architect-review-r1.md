# v2 评审 round 1 — 架构师反馈

| 项 | 值 |
|---|---|
| 评审人 | 架构师子代理（Opus 4.7 / 20 年经验视角） |
| 评审对象 | PRD.md v1 / TDD.md v1 / ManualTest.md v1（本目录） |
| 日期 | 2026-05-25 |
| 评审轮次 | round-1（之后用户会要求 round-2 / v2-v3 优化） |
| 评审标准 | 4 维度（13 项对照 / 可执行性 / v1 兼容 / 跨层契约） |

> 重要前置：本评审严守用户立场——v1 的 §2.2 自作主张砍 13 项是事故，v2 严禁重演。**任何形如"v2.1 再做" / "推迟" / "推荐先做选项 C" / "Plan B 砍 X" 的语言，只要落在 13 项里就标 BLOCKER**，不接受 advisory 等级。

---

## §A 13 项逐项检查（**核心 — 防 scope drift 再次发生**）

| # | 项 | 覆盖位置 | 深度分 | 缺什么 / 变相 out-of-scope |
|---|---|---|---|---|
| 1 | **A1** 拖拽 → being_held + physics3 | PRD §3 Layer A `A1` / TDD §2.1 heldStateMachine / Manual §3 CASE-A1-01..04 | **3.5/5** | (a) **PRD §3 A1 没有说 "physics3 接管"** — 业界对照原文是 physics3.json 接管 wobble，而 v2 改成"前端纯参数 sin 计算 + 衰减包络"。这是**实现路径选择**，需要明示 "physics3 接管不做 v2 用纯参数模拟" 是否算 scope 偏移。**建议 BLOCKER**：明示在 PRD 里 "physics3 是否纳入 / 仅参数模拟" 决策点，否则等于变相砍 physics3。(b) wobble 公式 `sin × exp(-t/4000)` 4000ms 衰减常数无依据，需 Day-0 视觉调参证据。(c) drag 期间是否 block Perlin/saccade/gaze 在 PRD 写了 "block"，但 applyTo step 2/3/4 注释只说 "block if held/DND" — TDD §2.1 没有 held → block 的契约 test。(d) PRD §3 A1 跨层"无后端依赖"——但 v1 FIX-R3 OS 测试已经验证 startDragging Promise pending 问题，Day-0 Probe-A1 只是再跑一次，**没说如果 Probe-A1 FAIL 分支 b (用 invoke('start_dragging')) 需要后端 Rust 加 command** — 这是隐性后端依赖。 |
| 2 | **B1** 用户输入中 → 微歪头 + 听音 | PRD §3 Layer B `B1` / TDD §2.2 userInputObserver / Manual §4 CASE-B1-01..03 | **4/5** | (a) "听音" 业界对照里指的是 **耳朵微动**（如猫耳朵动） — PRD 写了 "ParamEar / ParamHairFront 微动（**如 Hiyori 有**）"。这是 **变相 hedge** — 若 Hiyori 没有这俩参数，"听音"维度就缺失了。**BLOCKER**：必须在 Day-0 Probe-D1 加 ParamEar / ParamHairFront 实测，并在 PRD 写明 "若 Hiyori 缺这俩参数，B1 用 XX 参数（如 ParamBustY 或微 ParamAngleY 摆动）替代"，不允许 silent skip。(b) eye_boost_mul=1.05 太弱，肉眼不可辨；建议 1.15-1.2，并在 ManualTest CASE-B1 加 "脸部细节录屏" 验证。(c) tilt_deg=+2° 也偏小，需 A/B 调参证据。(d) input selector 默认 `'input,textarea'`，但 chat-input 实际 selector 没 cross-check（App.tsx 里 data-testid="chat-input" 是否就在 textarea/input 上，待核）。 |
| 3 | **B2** LLM 思考中 → 看天/思考表情 | PRD §3 Layer B `B2` / TDD §2.3 thinkingObserver / Manual §5 CASE-B2-01..03 | **4/5** | (a) PRD 已明确挂 sendChatV2 / chat_v2_final 信号，但 **没考虑流式响应** — chat_v2 走 streaming first-token，"思考姿"应该在 first token 到达就结束 vs final 到达才结束？业界对照里"思考姿"是 LLM 完全 silent 期，first token 一来就应切到 "speaking" / emotion 姿。PRD 写的是 chat_v2_final 才退出 — **MAJOR**：与流式 UX 不一致，建议改 first chunk 到达即退出 thinking。(b) max_thinking_duration_ms=30000 — the relay 偶尔会卡 60s+，30s 太短，建议 90s。(c) blink_hz=0.15 与思考姿 + saccade=off 容易让眼睛"僵" — 应当 saccade 仍跑（只是降频）。(d) ParamBrowLY/RY = +0.3 "微挤眉" 与 emotion=neutral 时的眉位是否冲突？没说优先级。 |
| 4 | **B3** TTS viseme lipsync | PRD §3 Layer B `B3` / TDD §2.4 visemeLipsync / Manual §6 CASE-B3-01..06 | **2.5/5** | **★ 重灾区 ★** (a) 业界对照原文 "TTS 播放中 → viseme 时间戳 lipsync"，**v2 在 PRD §3 B3 里写 "本地 phoneme 估计器（v2.1 备选）"** — 这是**典型变相 out-of-scope** — **BLOCKER B-1**：必须从 PRD §3 B3 里把 "v2.1 备选" 这句删掉，要么 v2 内做要么明示在 §2.2 Non-Goals（但用户已禁止）。同样 TDD §9-6 写 "viseme 后端不支持 → 走 fallback：前端基于 transcript + 估计时长均分粗 phoneme（用 jieba-like 中文分词 + 元音映射）" — 这个 fallback 必须在 v2 实施，不能"备选"。(b) Day-0 Probe-B3 是好的，但若 FAIL，TDD §0 Probe-B3 直接说 "B3 走 fallback：前端基于 transcript 时间均分 phoneme 估计" — 这个 fallback 路径**没有独立的设计章节、没有 test case、没有 ManualTest CASE**。CASE-B3-04 仅说 "flagOff('viseme') 退回 v1 amplitude" — **这等于偷偷砍了 viseme 项**。**BLOCKER B-2**：必须新增 §3 B3-fallback 章节 + TDD §2.4-b 前端 phoneme 估计器接口 + Manual CASE-B3-07 fallback 准确度验收。(c) viseme blend_ms=60 默认无依据；OQ-B3-2 说 "需 ManualTest A/B" 但没在 ManualTest §6 出现 A/B case。(d) 6 viseme 映射不含**双元音 / 鼻音**（ang / eng / in / un）— 中文实际需要更细。PRD 写 "本 PRD 取最常用 6 个"，但没说 "ai/ei/ao/ou/ang/eng 各自映射到哪个"。**MAJOR**：补全中文 phoneme 完整集到 6 viseme 的映射表，否则实施期会争吵。(e) Hiyori ParamMouthOpenY 范围实际多少？1.0 是猜测 — Day-0 Probe-D1 没测嘴部上下限。(f) TTS chunk 与 viseme 异步流（先到 audio 后到 viseme，或反过来）— PRD/TDD 都没说排队/同步策略。 |
| 5 | **B4** 说完静默 → 150-300ms fade | PRD §3 Layer B `B4` / TDD §2.5 mouthFader / Manual §7 CASE-B4-01..02 | **4.5/5** | (a) PRD/TDD/Manual 覆盖完整，公式正确；fade_ms=200 在 150-300 区间是合理中位。(b) **MAJOR**：未处理 "B4 fade 进行中 emotion=happy → ParamMouthForm 应该是 happy 的 0.8 还是 fade 中" — fade 仅作用于 mouthOpenY，但 emotion 写 mouthForm，两者独立没问题；但需在 §6.2 applyTo step 7-8 注释里明示。(c) tts_end 事件源是否可靠？后端有无可能漏发？建议 "TTS chunk 流停止 800ms 也触发 fade"（保险机制） — 这是缺失的兜底路径。 |
| 6 | **C1** >5min idle → low-energy | PRD §3 Layer C `C1` / TDD §2.6 idleWatcher / Manual §8 CASE-C1-01..03 | **3.5/5** | (a) PRD §3 C1 写 "每 30-60s 触发一次 yawn motion（**如该 tag 已校准**）" — **变相 out-of-scope** — 若 Hiyori m01-m10 没人标 "yawn" tag，等于不触发。**BLOCKER B-3**：必须有一个 sub-task 在 v2 内做 motion tag 校准（HiyoriMotionTuner 在 v1 已建）— 或者明示用哪个 m0X 当 yawn（如 m08 是 "low-energy idle"）。否则验收行 CASE-C1-02 "需 motion calibration 已标" 是 condition-deferred，等于砍 yawn。(b) ParamBreath 周期 1.5x —— Hiyori 是否真用了 ParamBreath？需 Day-0 Probe 加这个参数检测。(c) idle 判定 events 默认 `['keydown','mousemove','wheel','pointermove']` — 缺 **window focus 变化**（用户切到其他应用时算"active" 还是"idle"？业界对照里"用户离开" 指的是切走窗口）。**MAJOR**：补 visibility change / window blur 进 events。(d) low_energy 与 v1 已有 idle state 关系：v1 PetStateMachine.idle 现在如何被 low_energy 替代？是 idle 的子状态还是并列状态？PRD §6.8 写 "加 `low_energy` 状态（与 idle 并列）"，但 v1 idle/working/worried/alert/intervening 是 PetStateMachine 一套，low_energy 加进去是否破坏 v1 状态转换图？需要新画 transition 图。 |
| 7 | **C2** 用户回归 → 想你了 | PRD §3 Layer C `C2` / TDD §2.6 idleWatcher (合) / Manual §9 CASE-C2-01..02 | **4/5** | (a) "想你了"动画 = TapBody + happy 表情 + 兴奋 blink — 业界对照里更具体是"扑过来 / 摇尾巴 / 撒娇"。Hiyori 是 2D 模型不能"扑过来"，PRD 用 TapBody 替代是合理的，但应在 PRD 里**明示"由于 Hiyori 是 2D 静态模型，'想你了'用 TapBody motion + happy 替代"** — 现在没说，等于变相 downgrade 业界对照。**MINOR-1**：补此说明。(b) welcome_duration_ms=1500 OK；cooldown_ms=60000（OQ-17）合理。(c) **MAJOR**：未定义"如果 low_energy 持续 30min+ 后用户回归，需不需要更隆重欢迎？"这是 hci 细节但用户提到 16 项时 mentioned。可加 **escalation 表**：low_energy 5-15min → 普通 welcome；>15min → 强化 welcome（更长 + bubble "好久不见~"）。 |
| 8 | **C3** 整点/纪念日 → 限定动画 | PRD §3 Layer C `C3` / TDD §2.7 timeCelebration / Manual §10 CASE-C3-01..03 | **3.5/5** | (a) PRD §3 C3 跨层写 "后端可选：将 anniversary 配置同步到后端 memory（**v2.1**）；**v2 先纯前端 localStorage**" — **变相 out-of-scope** — anniversary 后端同步推到 v2.1。但用户 13 项里的"整点/纪念日"是单机 OK 的，纯前端 localStorage 是**合理实现路径** — **这条勉强不算 BLOCKER**，但 PRD 必须删掉"v2.1"字样，改成 "v2 内：纯前端 localStorage 持久化（后端持久化不在 v2 scope，但不影响 13 项功能完整性）"。(b) PRD 写 "用户在 SettingsPanel 可加，v2 提供配置文件" — 但**没指定 SettingsPanel UI 设计或配置文件路径**。**MAJOR**：补 anniversary UI mockup or skip UI 用 JSON file 自填，明示其一。(c) "整点" 太频繁干扰 — DND 抑制 hourly 在 PRD 风险表#14 提到 "DND 模式抑制 C3"，但 §3 C3 行为段没明示。**MINOR-2**：补 "整点触发前 check dnd_active，true 则 silent skip"。(d) 纪念日"用户生日 / 应用安装日"OQ-C3 仍 open — 需在 PRD 内先拍：v2 内置 1 个默认 anniversary（应用安装日），其余用户自填。 |
| 9 | **D1** 回复情感分类 → 表情 TTS 锁定 | PRD §3 Layer D `D1` / TDD §2.8 emotionMapper + §2.9 emotionClassifier / Manual §11 CASE-D1-01..06 | **3/5** | (a) **★ 重灾区 ★** PRD §3 D1 跨层写 "选项 A (推荐) — 后端 LLM 调用时让模型在 chat_v2_final 附加 emotion 字段；选项 B — 前端本地 emotion classifier（轻量模型）；**选项 C — 关键词正则（兜底）**" + "v2 实施策略：先做选项 C 关键词正则（无后端依赖，10 秒落地）兜底；选项 A 在后端 ready 时启用"。**这是典型"推荐先做选项 C" 变相砍 scope** — 关键词正则永远做不到"happy/sad/angry/surprised/neutral 5 类准确分类"。**BLOCKER B-4**：必须在 v2 同时做 **选项 A（后端协议扩 + LLM system prompt 教 LLM 输出 emotion）+ 选项 C（前端正则兜底）**。选项 A 不能"等后端 ready" — 后端协议变更属于本 v2 工程量，PRD §6.9 已列出，TDD 必须有 backend task 子项。删掉"选项 A 在后端 ready 时启用"这种被动语言。(b) 5 类 emotion 不含 **disgust / fear** — OQ-D1 推 "5 类先做，留扩展接口" — **MAJOR**：业界 6-7 类是默认；至少在 EmotionCode union 类型预留扩展，且 emotionMapper 表里 disgust/fear 写 // TODO 占位（非 silent skip）。(c) emotion 锁定到 TTS_END 但若用户**强行打断 TTS**（比如下个 chat），锁该怎么释放？PRD 没说。(d) emotionClassifier 关键词正则太弱：HAPPY_RE 只匹配开头，"我觉得这是好的" 会归 neutral。**MAJOR**：改为 "出现一次即记一票，多类时取分高者"。 |
| 10 | **D2** memory milestone → 庆祝 | PRD §3 Layer D `D2` / TDD §2.6 (合 milestone-related) / Manual §12 CASE-D2-01..02 | **3/5** | (a) PRD §3 D2 跨层依赖后端 `backend/memory/milestone.py` 全新模块 — **这是真后端工程量**，但**没在 §8 里程碑表分配后端 sprint** — S1/S2/S3 全是前端任务清单。**BLOCKER B-5**：必须在 §8 加 "后端 D2 milestone 检测器开发"（与前端并行，否则 v2 ship 时 milestone 永远没数据触发）。(b) milestone 规则 OQ-D2 推 "v2 内置 3 条（streak_7d / msgs_1000 / first_custom_prompt）" — **MAJOR**：规则需写进 PRD §3 D2 正文（不能留 OQ），并定义"连续聊天定义"（每天至少 1 条 chat？跨 0 点算 1 天？）。(c) bubble 文案 "🎉 你已经和我聊了 7 天啦~" 是 hardcoded — i18n 不考虑（v2 Non-Goals 已含），OK。(d) D2 与 C3 anniversary 同时触发怎么排队？没说。 |
| 11 | **E1** 拖到屏幕边缘 → 攀爬/挂边 | PRD §3 Layer E `E1` / TDD §2.10 edgeWatcher / Manual §13 CASE-E1-01..03 | **3.5/5** | (a) 业界对照原文是"攀爬 / 挂边" — 多帧动画。Hiyori 2D 静态模型不能"攀爬"。PRD §3 E1 写 "贴右边 → ParamAngleZ=+90°（侧躺）；贴底 → ParamAngleZ=+180°（倒挂）" — 用旋转替代攀爬是合理 fallback，但和 #7 C2 一样**必须明示"Hiyori 2D 静态无攀爬，用旋转替代"** — 否则等于变相砍。**MINOR-3**：补此说明。(b) "切到 edge motion subset（如 calibrated）" — 又是"如已校准" hedge — **MAJOR**：v2 内必须做 edge motion 校准 sub-task（或明示无 edge motion 仅靠旋转，删掉"如 calibrated"）。(c) snap_offset_px=10 让"一部分出屏" — 多屏 setup 下出屏位置可能跨屏，未考虑。(d) PRD §6.4 新 Rust command `get_primary_monitor_size` 但 §3 E1 说"前端用 currentMonitor() API 已有，可选" — 选哪个？**MINOR-4**：明示。(e) E1 触发条件 "拖窗结束后" — 但若用户拖动**正在进行中**（手没松），是否预览 snap？业界 hci 一般会给 snap preview。**MAJOR**：补 snap preview 行为（hover-near-edge 半透明 ghost）or 明示 v2 不做 preview，仅松手 snap。 |
| 12 | **E2** 被窗口遮挡 → 主动挪开 | PRD §3 Layer E `E2` / TDD §2.11 occlusionWatcher / Manual §14 CASE-E2-01..03 | **2.5/5** | **★ 重灾区 ★** (a) PRD §4.1 flag 表 `deskpet_anim_occlusion` **默认 off** — **这是变相 out-of-scope** — 默认 off 等于绝大多数用户体验不到。理由写"用户隐私顾虑" — **BLOCKER B-6**：要么改默认 on + 首次启用弹 consent（NFR-8 已要求弹 consent），要么砍 E2 进 Non-Goals（用户已禁），二选一。建议默认 on + consent。(b) PRD §8 里程碑表 Plan B 砍优先序写 "**E2 砍（用户隐私 + Rust 工作量）**" 是首砍项 — **BLOCKER B-7**：删 §8 Plan B 砍 E2 的措辞，改成"E2 性能不达标时砍轮询频率（5Hz→0.5Hz）而非砍整项"。(c) Day-0 Probe-E2 FAIL 路径 TDD §0 写 "E2 降级或 **v2.1 推迟**" — **BLOCKER B-8**：删"v2.1 推迟"。(d) findSafeSpot 只检查 4 角 + 4 边中点 — 8 候选位置在 1920x1080 屏 + 多窗口环境下大概率全部 occluded → 返回 null → pet 不动。**MAJOR**：扩大候选集（grid sampling 16x9 = 144 点） or 用滑动窗口算法找最大 unoccluded 矩形。(e) 1Hz 轮询性能 — Win32 EnumWindows 在窗口多时可能 30ms+，1Hz 还 OK 但要测；NFR-1 性能预算未明示分配给 E2。(f) "dodge motion" — 又是 motion tag — v2 内必须校准 dodge motion 或明示用哪个 m0X。 |
| 13 | **F1** 全屏/打字密度/通话 → DND | PRD §3 Layer F `F1` / TDD §2.12 dndDetector / Manual §15 CASE-F1-01..04 | **3.5/5** | (a) PRD §8 Plan B 砍优先序写 "**F1 call detection 砍**" 是次砍项 — **BLOCKER B-9**：同 E2，删此砍项措辞，改成"call detection API 失败时 fallback 仅 fullscreen + typing"（这是 graceful degrade，不是 scope 砍）。(b) heavy_typing_kpm_threshold=50 太低 —— 普通用户敲字 200+ KPM 是常态，50 KPM 误触发。**MAJOR**：调到 250+ KPM，并加 "持续 3min" 持续判定（avoid 短时爆敲触发）。(c) call_app_processes 固定 `['Teams.exe','zoom.exe','discord.exe','skype.exe']` — 漏 Slack call / Wechat 视频 / Lark 等。**MAJOR**：用 audio session API 探测（"任意进程有 active audio capture session" = 通话中），不要 hardcode 进程名。(d) DND 角标 "ZZZ" 图标 — Hiyori 桌宠本身可能小，ZZZ 角标位置/尺寸未定义。**MINOR-5**：补 UI spec。(e) red supervisor alert 不被抑制 = AC-10-03 / CASE-F1-04 / CASE-AC10-03 三处覆盖，重复无害，OK。(f) Audio Session API（CoreAudio）— Day-0 Probe-F1 测的是 Teams.exe 单一进程，没测"任意进程有 audio capture" 通用版。**MAJOR**：Probe-F1 改为通用 audio session 枚举。 |

**§A 小结**：13 项里 **8 项**带 BLOCKER（变相 out-of-scope 或工程量 hedge），3 项 MAJOR-only，2 项 MINOR-only。这与用户"严禁 scope drift"诉求直接冲突，**v2 必须 round-2 整改后才可开工**。

---

## §B BLOCKER（必须修；不修不准 ship）

- **B-1**：PRD §3 B3 删 "本地 phoneme 估计器（v2.1 备选）"；改为 "若后端不输出 viseme，v2 内做前端 phoneme 估计器 fallback（基于 transcript 时间均分 + 中文分词元音映射）"。
- **B-2**：PRD §3 新增 §3.B3-fallback 子章节 + TDD §2.4-b 前端 phoneme 估计器接口 `createPhonemeEstimator(transcript, total_duration_ms): VisemeFrame[]` + ManualTest CASE-B3-07 fallback 准确度验收（朋友盲听判定）。
- **B-3**：PRD §3 C1 删 "如该 tag 已校准" hedge；在 §8 里程碑 S2 增加 "motion tag 校准 sub-task"（HiyoriMotionTuner 跑 m01-m10 → 给 yawn/dodge/edge 三 tag）；TDD §4.6 加 motion tag fallback test。
- **B-4**：PRD §3 D1 删 "选项 A 在后端 ready 时启用" 被动语言；改为 "v2 同时实施选项 A（后端 LLM emotion 字段）+ 选项 C（前端关键词正则兜底）"；§8 里程碑 S2 加 "后端 chat_v2_final 加 emotion 字段 + LLM system prompt 教学" 后端 sub-task。
- **B-5**：PRD §8 里程碑表加后端工作并行 lane：S2 "后端 D2 milestone.py + chat_v2_final emotion 字段 + (B3 viseme if probe PASS)" 三条后端 task；后端不并行做 v2 无法 ship。
- **B-6**：PRD §4.1 flag 表 `deskpet_anim_occlusion` 改默认 **on**（首次启用弹 consent，复用 NFR-8）；E2 不再"默认 off 等于砍 scope"。
- **B-7**：PRD §8 里程碑 Plan B 砍优先序删 "E2 砍" 措辞；改 "E2 性能/Win32 fail 时降级：轮询 1Hz→0.2Hz 或 ParamAngleZ 写入失败时仅 snap 位置不旋转"。
- **B-8**：TDD §0 Probe-E2 FAIL 分支删 "v2.1 推迟"；改 "Probe-E2 FAIL → E2 用 polling enumeration 失败时维持原位 + console.warn 一次，functionality 自动 disable 但 flag 仍 on"。
- **B-9**：PRD §8 Plan B 砍优先序删 "F1 call detection 砍"；改 "F1 call detection API 失败时 fallback 仅 fullscreen + typing，flag 子项 deskpet_anim_dnd_call 自动 off"。
- **B-10**：PRD §3 A1 加 "physics3 是否启用" 决策行：v2 用纯参数 sin wobble + exp 衰减，**不**接 physics3.json（这是工程取舍，需明示，避免后期质疑"v1 砍 physics3 等于 v2 砍 physics3"）。
- **B-11**：跨层契约缺协议版本号。PRD §6.3 ws 消息扩展 5 条，但未定义 "client→backend 握手时声明 client_version"。**强烈建议** 加 `client_hello { version: 'v2.0', supports: ['viseme','emotion','milestone'] }` 让旧 backend 知道 client 能力，新 backend 可按 capability 推消息。否则跨 client/backend 版本组合矩阵会失控。
- **B-12**：PRD §6.2 applyTo step 7-8 注释只说"emotion + viseme 冲突 viseme 优先"，但没说**多 FR 并发参数写入完整优先级表**（A1 wobble × B1 tilt × D1 emotion × DND force-off × E1 edge pose 同时活时 ParamAngleZ 写入冲突）。**必须**在 PRD §6.2 加 10x10 priority matrix（哪个 FR 写哪个 param + 优先级 + ADD/SET/MULTIPLY），不能让实施期 case-by-case。
- **B-13**：v1 已 ship 的 `pulseInteraction` 接口在 v2 §6.1 Live2DHandle 仍保留，但 v2 加 `setHeldState` 又重叠（drag 既触发 pulseInteraction('drag') 又 setHeldState('being_held')）—— **v1 兼容性破坏点**。必须明示：drag pulseInteraction 是否被 setHeldState 取代？

---

## §C MAJOR（强烈建议修，不修会拖慢实施）

- **M-1**：B2 thinking 应在 first chunk 到达即退出（流式 UX 一致性），非 chat_v2_final。
- **M-2**：B3 中文 phoneme→6 viseme 完整映射表必须写出（含 ai/ei/ao/ou/ang/eng/in/un 等）。
- **M-3**：B3 fallback phoneme 估计的准确度验收需要 ManualTest 用句 + 朋友盲听判定（不能 silent skip）。
- **M-4**：B4 缺 "TTS chunk 停止 800ms 也触发 fade" 兜底（防 tts_end 漏发）。
- **M-5**：C1 idle events 缺 window blur / visibility change。
- **M-6**：C1 low_energy state 与 v1 PetStateMachine 5 state 关系图未画 — 转换图必须重画。
- **M-7**：C2 escalation：long-absence (>15min) 需更隆重 welcome。
- **M-8**：C3 SettingsPanel anniversary UI 或 JSON 配置路径必须二选一明示。
- **M-9**：C3 整点触发前 check dnd_active 抑制。
- **M-10**：D1 选项 A 后端协议+LLM system prompt 教学是 v2 工程量（已并入 B-4）。
- **M-11**：D1 emotion 锁释放需处理"用户打断 TTS"边缘。
- **M-12**：D1 emotionClassifier 用投票法替代单一 regex 顺序。
- **M-13**：D1 EmotionCode 预留扩展（disgust/fear），TODO 占位。
- **M-14**：D2 milestone 规则文本写进 PRD §3 D2 正文（streak 定义、时区、跨日规则）。
- **M-15**：E1 snap preview（hover-near-edge ghost）或明示不做。
- **M-16**：E1 edge motion calibration sub-task（合并 B-3）。
- **M-17**：E2 findSafeSpot 候选集扩到 grid sampling（8 点太少）。
- **M-18**：E2 NFR-1 性能预算给 occlusion 1Hz 轮询多少 CPU? 明示。
- **M-19**：F1 KPM 阈值 50→250+；窗口 30s→3min。
- **M-20**：F1 通话检测 audio session 通用化（不 hardcode 进程名）。
- **M-21**：F1 Day-0 Probe-F1 改为枚举所有 audio capture session，非 Teams 单进程。
- **M-22**：v1 兼容 - drag pulseInteraction vs setHeldState 重叠决策（合并 B-13）。
- **M-23**：跨层 - 协议版本号握手（合并 B-11）。
- **M-24**：性能 - NFR-1 v2 总预算 0.7ms/call 内部如何分配给 13 FR？至少给 emotion/viseme/occlusion 三大头预算上限。
- **M-25**：v2 新加 14 setters，AnimationOverlay 单测覆盖 P0 case 数量预估 ≤ 14 但 PRD §6.2 step 7-8 注释复杂，建议每个 step 单独 unit test。
- **M-26**：B1 eye_boost_mul=1.05 / tilt_deg=+2° 太弱，建议加 A/B 调参 ManualTest CASE。
- **M-27**：B3 Hiyori ParamMouthOpenY 实际范围必须 Day-0 Probe（不是猜 1.0）。
- **M-28**：A1 wobble 衰减常数 4000ms 无依据，需 Day-0 视觉调参。

---

## §D MINOR（命名 / 注释 / 修订日志）

- **m-1**：C2 PRD §3 补 "Hiyori 2D 静态无扑过来动画，用 TapBody + happy 替代"。
- **m-2**：C3 PRD §3 加 "整点触发前 check dnd_active 抑制" 一句（也算 M-9 简版）。
- **m-3**：E1 PRD §3 补 "Hiyori 2D 静态无攀爬动画，用 ParamAngleZ 旋转替代"。
- **m-4**：E1 PRD §6.4 currentMonitor API vs 新 Rust command 二选一明示。
- **m-5**：F1 PRD §3 补 DND badge UI spec（位置/尺寸/颜色）。
- **m-6**：PRD §12 修订日志写得太薄（仅 1 行 v1）；round-1 评审后应增 v1.1 整改条目，单独列出 B-1 ~ B-13 + M-1 ~ M-28 全部 ack。
- **m-7**：TDD §1 架构图最右下角 13 模块缩写 [held][inObs][thObs][vis][mFd][idle][wake][time][emo][mile][edge][occl][dnd] —— wake 与 idle 共用 idleWatcher，但图里画成两格，建议合并标 `[idle/wake]` 12 格。
- **m-8**：ManualTest §0.2 helper 名 `flagOn / flagOff / allDefault` 在 v1 已有，v2 加新 key（v2_all / held / userInput / ...）— 函数没做 cross-check（如 flagOff('v2_all') 是 v2 总开关 off）。可加 `window.testV2Smoke()` 一键跑 13 FR mini case。

---

## §E v1 兼容性 + 回归风险

| 风险点 | 等级 | 详情 |
|---|---|---|
| **drag pulseInteraction vs setHeldState 重叠** | BLOCKER | v1 FIX-R3 + v1 FR-6 pointerReaction 已有 `pulseInteraction('drag')`（待核），v2 A1 又加 `setHeldState`。若两者同时改 ParamAngleZ 会冲突。需在 v2 设计**取消 pulseInteraction('drag') 改由 setHeldState 全权接管**，或明示 pulseInteraction('drag') 已废弃。**这是 v1 → v2 行为破坏点**。 |
| **applyTo step 6 ParamAngleZ 写入扩到 5 源** | MAJOR | v1 applyTo 仅 base + transient + tilt；v2 加 held_wobble + edge_pose 同写 step 6。AC-3 v1 零回归测必须 explicit：v1 模式下 wobble=0 / edge=null → 写入结果 == v1 baseline。 |
| **PetStateMachine 加 low_energy state** | MAJOR | v1 5 state（idle/working/worried/alert/intervening）+ STATE_CONFIG 已固化测试。v2 加 low_energy 等价于改状态机契约，v1 transition 测必须 explicit AC-3 测 "low_energy flag off 时状态机行为 == v1"。 |
| **AnimationOverlay 14 个新 setter** | MAJOR | v1 9 个原 setter；v2 加 14 个共 23 个。NFR-7 "v1 386 单测不回归" 需要：(a) v2 所有新 setter 默认未调用时 applyTo 输出 == v1；(b) v1 baseline snapshot test（参数 vector dump 比对）。建议加 TC-AC3-snapshot test。 |
| **HiyoriMotionTuner motion tag pool 加 'low-energy'/'yawn'/'edge'/'dodge'** | MAJOR | v1 已有 fast/medium/slow/special 4 tag。加 4 新 tag 改变 setMotionTagPool 行为，需测 "v1 mode（v2_all=off）下 setMotionTagPool(['fast','medium']) 等价于 v1"。 |
| **B3 viseme 写 ParamMouthOpenY** | MAJOR | v1 FR-？（音量包络？v1 实际 ship 哪个 ParamMouthOpenY 写入需核对）；若 v1 也写 mouthOpenY 则两者竞争 step 1。AC-3 测 viseme flag off → mouthOpenY 行为 == v1。 |
| **Live2DCanvas 挂 5+ observer** | MINOR | userInput/thinking/idle/occlusion/dnd observer 挂载顺序 + dispose 顺序必须 mirror v1（防内存泄漏）。Live2DCanvas useEffect cleanup 需新增 5 个 stop()。需 ManualTest cold reload + HMR 测（CASE-HMR-01 / COLD-01 已 v1 含）。 |
| **App.tsx 改 chat-input** | MINOR | App.tsx 在 chat-input 加 onFocus/onBlur/onChange — 若用户的 IME 输入法（中文拼音）合成中 keydown 触发节奏不同，B1 可能误报 active。需测 IME 兼容。 |

**v1 零回归测试套件建议（AC-3 必跑）**：
1. v2_all=off → 全 386 单测 PASS（不增不减）
2. v2_all=off → v1 ManualTest 全 P0 PASS（不少于 v1 ship 时的 27/27）
3. v2_all=on + 所有 13 FR flag off → AnimationOverlay applyTo 输出 snapshot 与 v1 baseline diff = 0
4. v2_all=on + 个别 FR flag on → 仅相关 param 写入变化，其他 == v1

---

## §F 跨层契约审查

| 子项 | 评分 | 详情 |
|---|---|---|
| 协议字段命名一致 | 3/5 | `tts_viseme { v, t_ms }` 字段命名简短 OK；`chat_v2_final { emotion }` optional OK；`pet_milestone { kind, message, achieved_at }` OK。但 **缺命名空间** — 所有 v2 新消息没有 v2 前缀（如 `tts_viseme` vs `pet_milestone`），旧 backend 升级时区分 v1/v2 困难。**建议**：用 `pet_*` / `tts_*` 现有命名空间，并加协议版本号（B-11）。 |
| 旧 client 兼容（新 backend） | 4/5 | 新 backend 推 `tts_viseme` / `pet_milestone` 旧 client 应 silent skip（NFR-9）— 客户端 ws message dispatcher 默认 unknown → silent，OK。但需写**测试**验证。 |
| 旧 backend 兼容（新 client） | 3/5 | 新 client 收 chat_v2_final 时 emotion 字段 optional，若缺 → 走 emotionClassifier 关键词 — OK。但 viseme 缺失就跑 fallback phoneme 估计 — 这条**必须 v2 内做**（见 B-1/B-2）。否则"旧 backend + 新 client + B3" 退化为"v1 音量包络" = 变相砍 viseme。 |
| Day-0 探针覆盖后端假设 | 3/5 | 5 探针覆盖：A1 startDragging / B3 viseme / D1 Hiyori 参数 / E2 EnumWindows / F1 audio session。**缺**：(a) D1 后端 LLM 是否真能输出 emotion 字段（Probe-D1-B 新增 — 让 backend dry-run chat 看 chat_v2_final 含 emotion 否）；(b) D2 后端 memory 表是否准备好 milestone 字段（Probe-D2 新增）；(c) F1 audio session 通用枚举非 Teams 单进程（M-21）。 |
| 协议版本号 | 0/5 | **完全缺失**（B-11）。 |
| Rust commands 协议 | 4/5 | `enumerate_top_windows / is_foreground_fullscreen / is_call_app_active / get_primary_monitor_size` 4 个 command 命名清晰；返回类型在 §6.4 给了草稿（`Vec<TopWindowInfo>`）但 `TopWindowInfo` 结构未在 PRD/TDD 写全 — TDD §2.11 写了 `{ hwnd, title, rect, is_visible }` OK，但 PRD §6.4 应同步。 |
| Permission consent UX | 2/5 | NFR-8 "首次启用时弹用户确认" 但**没有 UI mockup / 确认流程**。如何弹？Tauri dialog API？SettingsPanel 集中？拒绝后怎样？consent 持久化在哪？**MAJOR**：补 §6.10 Permission flow 子章节。 |

---

## §G 总体判定

**NEEDS-MAJOR-REWORK**

评分（0-10）：**5.2/10**

判定理由：
- 13 项纸面全部列入 PRD，**但 8 项有 BLOCKER 级变相 out-of-scope**（"v2.1 备选" / "推荐先做选项 C" / "Plan B 砍 E2/F1 call" / "默认 off" / "如 calibrated"）—— 这与 v1 §2.2 错误是同种 pattern 的换皮，不是修复。
- 13 BLOCKER + 28 MAJOR + 8 MINOR —— 工程量大但**不**需要重写 PRD 框架，只需要 round-2 整改后即可推进。
- v2 基础设施复用（v1 9 模块 + AnimationOverlay + 386 单测）+ Day-0 5 探针 + 13 模块 + 14 setters + ws 协议扩展 + 4 Rust commands 总体方向是**正确**的，工程量在 3 sprint 紧但可控（前提是后端并行 lane 打开 — B-5）。
- 跨层契约最薄弱：协议版本号 / 后端 sprint 缺失 / permission UX 全缺 —— 这些是 v2 ship 必须的 due-diligence。
- v1 兼容性测试套件（AC-3 snapshot test）缺失，"386 单测不回归"现在只是口号没有具体方案。

---

## §H 给 v2 round-2 优化清单（排序，按优先级 + 工作量）

| # | 整改项 | 类型 | 工作量 | 优先 |
|---|---|---|---|---|
| 1 | 删 PRD/TDD 所有 "v2.1 备选" / "v2.1 推迟" / "如 calibrated" / "v2 先做选项 C" 措辞 | scope 纪律 | 0.5h | P0 |
| 2 | B-1/B-2 viseme fallback：PRD §3 B3-fallback 子章节 + TDD §2.4-b 接口 + Manual CASE-B3-07 | 新增章节 | 2h | P0 |
| 3 | B-4 D1 emotion：删被动语言 + §8 加后端 sub-task（LLM system prompt 教学 + chat_v2_final emotion 字段） | scope + 后端 | 1h | P0 |
| 4 | B-5 §8 里程碑加后端并行 lane（D1 emotion / D2 milestone / B3 viseme provider） | 后端 | 1h | P0 |
| 5 | B-3/B-16 motion tag 校准 sub-task 进 S2（yawn/dodge/edge 三 tag） | calibration | 2h（含 v1 HiyoriMotionTuner 跑） | P0 |
| 6 | B-6/B-7/B-8 E2 默认 off 改 on + 删 Plan B 砍 E2 + Probe-E2 FAIL 处理 | scope | 1h | P0 |
| 7 | B-9 F1 Plan B 删 call 砍措辞改 graceful degrade | scope | 0.5h | P0 |
| 8 | B-10 A1 physics3 决策行（明示用纯参数 sin wobble） | 说明 | 0.5h | P0 |
| 9 | B-11 协议版本号握手 `client_hello` | 协议 | 2h（前后端） | P0 |
| 10 | B-12 多 FR 并发参数写入优先级矩阵（PRD §6.2 加 10x10 表） | 文档 + 设计 | 3h | P0 |
| 11 | B-13 drag pulseInteraction vs setHeldState 重叠决策 | v1 兼容 | 1h | P0 |
| 12 | M-1 B2 thinking 改 first-chunk 退出（流式 UX） | 设计 | 0.5h | P1 |
| 13 | M-2 B3 中文 phoneme→viseme 完整映射表 | 文档 | 2h（含拼音学习） | P1 |
| 14 | M-4 B4 tts_end 漏发 800ms timeout 兜底 | 设计 | 0.5h | P1 |
| 15 | M-5/M-6 C1 idle events 加 visibility/blur + PetStateMachine transition 图重画 | 设计 + 图 | 2h | P1 |
| 16 | M-7 C2 long-absence escalation | 设计 | 0.5h | P1 |
| 17 | M-8 C3 SettingsPanel UI 或 JSON 路径明示 | 设计 | 1h | P1 |
| 18 | M-9 C3 整点 dnd_active 抑制 | 设计 | 0.25h | P1 |
| 19 | M-11/M-12 D1 锁释放 + 投票分类器 | 设计 | 1h | P1 |
| 20 | M-13 EmotionCode 扩展 disgust/fear TODO | 类型 | 0.25h | P1 |
| 21 | M-14 D2 milestone 规则文本进 PRD | 文档 | 1h | P1 |
| 22 | M-15 E1 snap preview 决策（做 or 不做明示） | 设计 | 0.5h | P1 |
| 23 | M-17/M-18 E2 candidates 扩 + 性能预算 | 设计 + 算法 | 2h | P1 |
| 24 | M-19/M-20/M-21 F1 KPM 阈值 + audio session 通用化 | 设计 | 1.5h | P1 |
| 25 | M-24 NFR-1 v2 性能预算 0.7ms/call 内部分配 | 文档 | 0.5h | P1 |
| 26 | M-26/M-27/M-28 B1/B3/A1 关键参数 Day-0 调参 case 加 ManualTest | 测试 | 1.5h | P1 |
| 27 | AC-3 v1 零回归 snapshot test 套件设计 | 测试 | 2h | P0 |
| 28 | Permission consent UX (§6.10 新章节) | 设计 + UI | 2h | P1 |
| 29 | m-7 TDD §1 架构图合并 [idle/wake] 12 格 | 图 | 0.25h | P2 |
| 30 | m-6 PRD §12 修订日志补 round-1 整改 ack | 文档 | 0.5h | P2 |

**round-2 预估总工作量**：P0 整改 ~17h（2 工作日）；P1 整改 ~14h（1.5 工作日）；P2 ~1h。**建议 round-2 仅修 P0 + 关键 P1（M-1/M-2/M-19/M-20）共 ~25h（3 工作日）即可重审**。

---

## §I 终审纪律提醒（给用户 + round-2 子代理）

1. **scope drift 是 v1 → v2 唯一原始诉求**。round-2 整改后若 PRD 仍含任何 "v2.1" / "推迟" / "如 calibrated" / "Plan B 砍 13 项之一" 字样，**直接 NO-GO**。
2. v2 文档"13 项纳入 scope"是底线，**不是上限**；后端协议变更 / motion calibration / Permission UX 这些是 13 项 "完整实施" 必带的 — 不能因为"工程量大"再换皮砍。
3. 多 FR 并发参数写入优先级矩阵（B-12）是 v2 最大架构债，必须 round-2 内补完，否则实施期会 case-by-case 失控。
4. v1 兼容性 AC-3 snapshot test 套件（§E 末尾 4 条）是 v2 ship 准入硬条件，与 v1 27/27 OS 手测同级。

---

**报告结束**。round-2 整改完成后请通知本评审人复审；建议同时请 Codex GPT-5.4 给第二意见。
