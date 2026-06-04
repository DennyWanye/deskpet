// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

//! 桌宠主窗口几何（尺寸 + 位置）持久化。
//!
//! 把 main 窗口的 (width, height) **和** (x, y) 写到
//! `<user_data>/window_geometry.json`。启动时 Rust 读一次 → 恢复尺寸 + 位置；
//! 运行时 `WindowEvent::Resized` / `WindowEvent::Moved` 防抖写回（800ms 静止
//! 后落盘，避免拖动过程中刷盘抖动）。
//!
//! 2026-06-02 多屏修复：旧实现只持久化尺寸、不持久化位置，且 `apply_saved_size`
//! 启动时把窗口 clamp 到 `current_monitor`（首启=主屏）→ **多屏用户没法把桌宠
//! 放到副屏**（拖过去重启又回主屏）。现在也持久化位置 + 恢复，clamp 只保证
//! "在恢复后所在的显示器内可见"，不再强制主屏。
//!
//! 向后兼容：旧 `{width,height}` 文件无 x/y → serde default `None` → 不恢复
//! 位置（回退 Tauri 默认 = 旧行为），尺寸照常恢复。
//!
//! 失败策略：读失败 → 用 tauri.conf.json 默认；写失败 → 静默忽略。
//! 这个功能丢一次状态无所谓，不能因为 IO 错误炸应用。

use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};
use tauri::{LogicalSize, PhysicalPosition, PhysicalSize, WebviewWindow, Window};

use crate::paths;

#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
pub struct WindowGeometry {
    pub width: u32,
    pub height: u32,
    /// 2026-06-02: physical outer position。旧文件无此字段 → serde default
    /// `None` → 不恢复位置（保旧行为）。x/y 要么都有要么都无。
    #[serde(default)]
    pub x: Option<i32>,
    #[serde(default)]
    pub y: Option<i32>,
}

const MIN_W: u32 = 240;
const MIN_H: u32 = 360;
const MAX_W: u32 = 4000;
const MAX_H: u32 = 4000;
const DEBOUNCE_MS: u64 = 800;

fn geometry_file() -> Option<PathBuf> {
    let dir = paths::user_data_dir()?;
    let _ = std::fs::create_dir_all(&dir);
    Some(dir.join("window_geometry.json"))
}

pub fn load() -> Option<WindowGeometry> {
    let path = geometry_file()?;
    let s = std::fs::read_to_string(&path).ok()?;
    let g: WindowGeometry = serde_json::from_str(&s).ok()?;
    if g.width < MIN_W || g.height < MIN_H || g.width > MAX_W || g.height > MAX_H {
        return None;
    }
    Some(g)
}

pub fn save(g: WindowGeometry) -> std::io::Result<()> {
    let Some(path) = geometry_file() else {
        return Err(std::io::Error::new(
            std::io::ErrorKind::Other,
            "user_data_dir unavailable",
        ));
    };
    let s = serde_json::to_string(&g)
        .map_err(|e| std::io::Error::new(std::io::ErrorKind::Other, e))?;
    std::fs::write(&path, s)
}

/// 把窗口位置 clamp 到**当前所在 monitor** 工作区内，保证完整可见。
///
/// 触发场景（2026-05-30 bug fix）：tauri.conf.json 默认 `x` 是按 360 宽算的，
/// 当用户上次拉大窗口（如 1022×828）后保存到 window_geometry.json，
/// 重启时只恢复 size → 大窗口仍从默认 x 起，右边界严重超出屏幕。
///
/// 2026-06-02：现在恢复位置后再 clamp，`current_monitor()` 已是「恢复后所在的
/// 显示器」（即上次用户放的那块），所以 clamp 把它夹在**那块**屏内，不再强制
/// 回主屏 —— 这是多屏副屏放置能生效的关键。
pub fn clamp_position_to_screen(win: &WebviewWindow) {
    let monitor = match win.current_monitor() {
        Ok(Some(m)) => m,
        _ => {
            eprintln!("[window_geometry] clamp_position: no current_monitor, skip");
            return;
        }
    };
    let mon_size = monitor.size();
    let mon_pos = monitor.position();
    let win_size = match win.outer_size() {
        Ok(s) => s,
        Err(e) => {
            eprintln!("[window_geometry] clamp_position: outer_size failed: {e:?}");
            return;
        }
    };
    let win_pos = match win.outer_position() {
        Ok(p) => p,
        Err(e) => {
            eprintln!("[window_geometry] clamp_position: outer_position failed: {e:?}");
            return;
        }
    };

    // 计算允许的 max 位置（窗口右/下边界不超出屏幕右/下边界）
    let max_x = mon_pos.x + (mon_size.width as i32) - (win_size.width as i32);
    let max_y = mon_pos.y + (mon_size.height as i32) - (win_size.height as i32);
    // clamp，同时也保证不低于 monitor 左/上边界
    let new_x = win_pos.x.min(max_x).max(mon_pos.x);
    let new_y = win_pos.y.min(max_y).max(mon_pos.y);

    if new_x != win_pos.x || new_y != win_pos.y {
        eprintln!(
            "[window_geometry] clamp_position: moving from ({},{}) to ({},{}) (mon={}x{} at ({},{}), win={}x{})",
            win_pos.x, win_pos.y, new_x, new_y,
            mon_size.width, mon_size.height, mon_pos.x, mon_pos.y,
            win_size.width, win_size.height,
        );
        if let Err(e) = win.set_position(PhysicalPosition::new(new_x, new_y)) {
            eprintln!("[window_geometry] clamp_position: set_position failed: {e:?}");
        }
    }
}

/// 从窗口读取当前几何（尺寸 logical + 位置 physical）。
/// `size_override`：resize 事件携带的新 size（physical）；None → 现读 inner_size。
fn build_geometry(win: &Window, size_override: Option<PhysicalSize<u32>>) -> Option<WindowGeometry> {
    let scale = win.scale_factor().unwrap_or(1.0);
    let sz = match size_override {
        Some(s) => s,
        None => win.inner_size().ok()?,
    };
    let w = (sz.width as f64 / scale).round() as u32;
    let h = (sz.height as f64 / scale).round() as u32;
    if w < MIN_W || h < MIN_H {
        eprintln!("[window_geometry] build_geometry rejected: {w}x{h} below MIN");
        return None;
    }
    let pos = match win.outer_position() {
        Ok(p) => p,
        Err(e) => {
            eprintln!("[window_geometry] build_geometry: outer_position failed: {e:?}");
            return None;
        }
    };
    Some(WindowGeometry {
        width: w,
        height: h,
        x: Some(pos.x),
        y: Some(pos.y),
    })
}

/// 启动时应用持久化的尺寸 **+ 位置** 到 main 窗口（如果有的话）。
pub fn apply_saved_geometry(win: &WebviewWindow) {
    match load() {
        None => eprintln!("[window_geometry] apply_saved_geometry: load() returned None (no file or out of range)"),
        Some(g) => {
            eprintln!("[window_geometry] apply_saved_geometry: loaded {}x{} logical pos={:?},{:?}", g.width, g.height, g.x, g.y);
            let size = LogicalSize::new(g.width as f64, g.height as f64);
            match win.set_size(size) {
                Ok(()) => {
                    if let Ok(actual) = win.inner_size() {
                        eprintln!("[window_geometry] set_size OK, inner_size now {}x{} physical", actual.width, actual.height);
                    }
                }
                Err(e) => eprintln!("[window_geometry] set_size failed: {e:?}"),
            }
            // 2026-06-02: 恢复上次所在显示器的位置（旧文件无 x/y → 跳过）。
            if let (Some(x), Some(y)) = (g.x, g.y) {
                eprintln!("[window_geometry] restoring position ({x},{y})");
                if let Err(e) = win.set_position(PhysicalPosition::new(x, y)) {
                    eprintln!("[window_geometry] set_position failed: {e:?}");
                }
            }
        }
    }
    // 不论是否恢复了 size/pos，都 clamp 一次 —— 现在 clamp 到「恢复后所在的
    // 显示器」（上次那块），不强制主屏；也修 conf.json 默认 x 落屏外的情况。
    clamp_position_to_screen(win);
}

#[derive(Default)]
struct DebouncerState {
    last_event_at: Option<Instant>,
    pending: Option<WindowGeometry>,
    timer_armed: bool,
    /// 2026-06-03 跨 DPI 尺寸漂移修复：权威逻辑尺寸（pin）。首个 resize/move
    /// 事件时从已保存(boot 恢复)的尺寸初始化；此后 resize/move 一律沿用此值，
    /// 不再用 physical/scale 反算的尺寸覆盖它。详见 `pin_size`。
    committed_size: Option<(u32, u32)>,
}

/// 防抖落盘。挂在 Tauri app state 上，单例。clone() 廉价（Arc）。
/// 同时服务 resize（尺寸变）和 move（位置变）—— 名字保留 `ResizeDebouncer`
/// 以免改动 lib.rs 的 manage()/try_state() 类型签名。
#[derive(Clone, Default)]
pub struct ResizeDebouncer {
    state: Arc<Mutex<DebouncerState>>,
}

impl ResizeDebouncer {
    pub fn new() -> Self {
        Self::default()
    }

    /// resize 事件：尺寸来自事件 payload（physical），位置现读。
    ///
    /// 2026-06-03 多屏 DPI 修复：移除了原先在此调 `clamp_position_to_screen` 的
    /// 逻辑。原意「拖大窗口超屏就拽回」，但在**跨不同 DPI 显示器拖动**时灾难性：
    /// 跨 DPI 边界 → WM_DPICHANGED → Resized → clamp → set_position → 又跨边界 →
    /// Resized → … 形成振荡（桌宠剧烈抖动 + 被 current_monitor 误判拽回主屏，
    /// 无法从主屏拖到副屏）。clamp 只在启动时 apply_saved_geometry 跑一次即可，
    /// 不在运行期 resize/drag 干预位置。
    pub fn on_resize(&self, win: &Window, physical: PhysicalSize<u32>) {
        eprintln!("[window_geometry] on_resize physical={}x{}", physical.width, physical.height);
        if let Some(g) = build_geometry(win, Some(physical)) {
            // 跨 DPI 尺寸漂移修复：用 pin 住的权威逻辑尺寸，不让 DPI 变化改尺寸。
            self.schedule_save(self.pin_size(g));
        }
    }

    /// 2026-06-02: move 事件 → 持久化位置（桌宠记住用户放的显示器）。
    /// 尺寸现读（拖动不改尺寸，但一并存以保持文件完整）。
    pub fn on_move(&self, win: &Window) {
        if let Some(g) = build_geometry(win, None) {
            // 跨 DPI 尺寸漂移修复：move 路径同样 pin 尺寸（拖动只改位置不改尺寸）。
            let g = self.pin_size(g);
            eprintln!("[window_geometry] on_move pos=({:?},{:?}) size={}x{}", g.x, g.y, g.width, g.height);
            self.schedule_save(g);
        }
    }

    /// 2026-06-03 跨 DPI 尺寸漂移修复（"拖几次只剩一半"根因）。
    ///
    /// `build_geometry` 用 `physical_size / scale_factor` 反算逻辑尺寸。跨不同 DPI
    /// 显示器拖动时，Resized 携带的 physical（尤其窗口横跨两屏的瞬间）与
    /// `scale_factor()` 不总是同拍 → 反算的逻辑尺寸有误差，经 resize+move 两条持久化
    /// 路径反复写回、多次跨屏累积 → 逻辑尺寸单调漂移（实测 375×610 → 360×657）→
    /// 窗口窄于角色 → 桌宠只显示一半。
    ///
    /// 本桌宠窗口**无用户改尺寸入口**（无边框拖拽 / 无缩放滑块；前端 resize 持久化
    /// 已于 2026-05-31 删除）——逻辑尺寸理应 boot 后恒定。故 **pin 住**：首次事件以
    /// 已保存(boot apply_saved_geometry 恢复)的尺寸为权威值，此后所有 resize/move 一律
    /// 沿用，DPI 变化只让窗口物理尺寸自适应、绝不改持久化的逻辑尺寸。若将来加入真·
    /// 缩放功能，应由该功能直接更新 `committed_size`（或走独立命令路径）。
    fn pin_size(&self, mut g: WindowGeometry) -> WindowGeometry {
        let mut st = self.state.lock().unwrap();
        let (w, h) = match st.committed_size {
            Some(s) => s,
            None => {
                let s = load()
                    .map(|saved| (saved.width, saved.height))
                    .unwrap_or((g.width, g.height));
                st.committed_size = Some(s);
                s
            }
        };
        g.width = w;
        g.height = h;
        g
    }

    fn schedule_save(&self, g: WindowGeometry) {
        let arm_timer = {
            let mut st = self.state.lock().unwrap();
            st.last_event_at = Some(Instant::now());
            st.pending = Some(g);
            if st.timer_armed {
                false
            } else {
                st.timer_armed = true;
                true
            }
        };

        if !arm_timer {
            return;
        }

        let state_clone = Arc::clone(&self.state);
        std::thread::spawn(move || loop {
            std::thread::sleep(Duration::from_millis(DEBOUNCE_MS));
            let to_save = {
                let mut st = state_clone.lock().unwrap();
                match st.last_event_at {
                    Some(t) if t.elapsed() >= Duration::from_millis(DEBOUNCE_MS) => {
                        let g = st.pending.take();
                        st.timer_armed = false;
                        st.last_event_at = None;
                        g
                    }
                    _ => continue,
                }
            };
            if let Some(g) = to_save {
                eprintln!("[window_geometry] flushing {}x{} pos={:?},{:?} to disk", g.width, g.height, g.x, g.y);
                match save(g) {
                    Ok(()) => eprintln!("[window_geometry] saved OK"),
                    Err(e) => eprintln!("[window_geometry] save failed: {e:?}"),
                }
            }
            break;
        });
    }
}

#[tauri::command]
pub fn get_saved_window_geometry() -> Option<WindowGeometry> {
    load()
}

/// 2026-05-31 restore — Defense in depth：本命令以前同时 `win.set_size(...)`
/// 和 `save(...)`。commit 14a58f5 已删了前端调用方，但 set_size 自身仍是潜在
/// 反馈源。修复：去掉 `win.set_size`，命令变成纯写盘。运行时尺寸/位置调整由
/// 用户拖拽 + `WindowEvent::Resized/Moved → ResizeDebouncer` 这一条权威路径负责。
#[tauri::command]
pub fn set_window_geometry(
    _app: tauri::AppHandle,
    width: u32,
    height: u32,
) -> Result<(), String> {
    if width < MIN_W || height < MIN_H || width > MAX_W || height > MAX_H {
        return Err(format!("out of range: {width}x{height}"));
    }
    // 仅显式设尺寸时，位置维持已存值（读旧文件取 x/y；无则 None）。
    let (x, y) = load().map(|g| (g.x, g.y)).unwrap_or((None, None));
    save(WindowGeometry { width, height, x, y }).map_err(|e| e.to_string())?;
    Ok(())
}
