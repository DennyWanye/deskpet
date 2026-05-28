// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

//! 桌宠主窗口尺寸持久化。
//!
//! 把 main 窗口的 (width, height) 写到 `<user_data>/window_geometry.json`。
//! 启动时 Rust 读一次 → 应用到 main 窗。运行时 WindowEvent::Resized 防抖
//! 写回（800ms 静止后落盘，避免拖动过程中刷盘抖动）。
//!
//! 只持久化尺寸，不持久化位置 —— 用户原话："拉动的宽高" + 重启恢复。
//! 位置交给 Tauri 默认。
//!
//! 失败策略：读失败 → 用 tauri.conf.json 默认；写失败 → 静默忽略。
//! 这个功能丢一次状态无所谓，不能因为 IO 错误炸应用。

use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};
use tauri::{LogicalSize, Manager, PhysicalSize, WebviewWindow, Window};

use crate::paths;

#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
pub struct WindowGeometry {
    pub width: u32,
    pub height: u32,
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

/// 启动时应用持久化的尺寸到 main 窗口（如果有的话）。
pub fn apply_saved_size(win: &WebviewWindow) {
    match load() {
        None => eprintln!("[window_geometry] apply_saved_size: load() returned None (no file or out of range)"),
        Some(g) => {
            eprintln!("[window_geometry] apply_saved_size: loaded {}x{} logical", g.width, g.height);
            let size = LogicalSize::new(g.width as f64, g.height as f64);
            match win.set_size(size) {
                Ok(()) => {
                    eprintln!("[window_geometry] set_size OK");
                    // 验证实际生效尺寸
                    if let Ok(actual) = win.inner_size() {
                        eprintln!("[window_geometry] verify inner_size after set: {}x{} physical", actual.width, actual.height);
                    }
                }
                Err(e) => eprintln!("[window_geometry] set_size failed: {e:?}"),
            }
        }
    }
}

#[derive(Default)]
struct DebouncerState {
    last_resize_at: Option<Instant>,
    pending: Option<WindowGeometry>,
    timer_armed: bool,
}

/// 防抖落盘。挂在 Tauri app state 上，单例。clone() 廉价（Arc）。
#[derive(Clone, Default)]
pub struct ResizeDebouncer {
    state: Arc<Mutex<DebouncerState>>,
}

impl ResizeDebouncer {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn on_resize(&self, win: &Window, physical: PhysicalSize<u32>) {
        let scale = win.scale_factor().unwrap_or(1.0);
        let w = (physical.width as f64 / scale).round() as u32;
        let h = (physical.height as f64 / scale).round() as u32;
        eprintln!("[window_geometry] on_resize physical={}x{} scale={} logical={}x{}", physical.width, physical.height, scale, w, h);
        if w < MIN_W || h < MIN_H {
            eprintln!("[window_geometry] rejected: below MIN_W={} MIN_H={}", MIN_W, MIN_H);
            return;
        }
        let g = WindowGeometry { width: w, height: h };

        let arm_timer = {
            let mut st = self.state.lock().unwrap();
            st.last_resize_at = Some(Instant::now());
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
                match st.last_resize_at {
                    Some(t) if t.elapsed() >= Duration::from_millis(DEBOUNCE_MS) => {
                        let g = st.pending.take();
                        st.timer_armed = false;
                        st.last_resize_at = None;
                        g
                    }
                    _ => continue,
                }
            };
            if let Some(g) = to_save {
                eprintln!("[window_geometry] flushing {}x{} to disk", g.width, g.height);
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

#[tauri::command]
pub fn set_window_geometry(
    app: tauri::AppHandle,
    width: u32,
    height: u32,
) -> Result<(), String> {
    if width < MIN_W || height < MIN_H || width > MAX_W || height > MAX_H {
        return Err(format!("out of range: {width}x{height}"));
    }
    let Some(win) = app.get_webview_window("main") else {
        return Err("main window missing".into());
    };
    win.set_size(LogicalSize::new(width as f64, height as f64))
        .map_err(|e| e.to_string())?;
    save(WindowGeometry { width, height }).map_err(|e| e.to_string())?;
    Ok(())
}
