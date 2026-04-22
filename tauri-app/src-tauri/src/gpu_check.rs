//! P3-S2 — NVIDIA GPU precheck.
//!
//! Runs in `lib.rs` `.setup()` before the Python backend is spawned.
//! DeskPet's Phase-3 contract is **NVIDIA-only**: faster-whisper
//! large-v3-turbo needs CUDA + float16 to hit latency targets, and
//! falling back to CPU silently would mislead users into thinking
//! the app is working when it really isn't.
//!
//! Happy path: NVML initialises, at least one device reports VRAM →
//! `Ok(GpuInfo)`, setup hook continues as before.
//!
//! Sad path: NVML fails (no driver, driver too old, `nvml.dll`
//! missing) → `Err(GpuCheckError)` → `lib.rs` pops a blocking
//! `MessageDialog` with the user-facing message from
//! `format_user_message` and calls `app.exit(1)`. The backend is
//! never spawned.
//!
//! nvidia-smi.exe fallback is deliberately out of scope — NVML shares
//! the same DLL, so if it fails nvidia-smi would fail the same way.
//! If P3-S3 encounters a machine where that's not true, add the
//! subprocess fallback there.

#[derive(Debug)]
#[cfg_attr(test, derive(PartialEq, Eq))]
pub enum GpuCheckError {
    /// `nvml.dll` couldn't be loaded / NVML_Init returned failure.
    /// Usually means NVIDIA driver isn't installed or is broken.
    NvmlInitFailed(String),
    /// NVML initialised but reports zero devices (unusual but valid
    /// — e.g. laptop with only Intel iGPU and nvml stub).
    NoDevices,
    /// NVML initialised and reports devices, but querying the first
    /// one failed. Rare; treated as "driver present but degraded".
    DeviceQueryFailed(String),
}

#[derive(Debug, Clone)]
#[allow(dead_code)] // fields read by P3-S3 frontend status banner
pub struct GpuInfo {
    pub name: String,
    pub vram_gb: f64,
    pub driver_version: String,
}

/// User-facing Chinese message for the dialog box. Pure function so
/// we can unit-test every branch without touching NVML.
pub fn format_user_message(err: &GpuCheckError) -> String {
    match err {
        GpuCheckError::NvmlInitFailed(detail) => format!(
            "DeskPet 需要 NVIDIA GPU 才能运行。\n\n\
             无法初始化 NVIDIA 驱动 (NVML)，请确认：\n\
             • 机器上有 NVIDIA 显卡\n\
             • 已安装最新版 NVIDIA 驱动并重启\n\n\
             详细故障排查见 docs/PACKAGING.md#硬件前置检查\n\n\
             [技术细节] {detail}"
        ),
        GpuCheckError::NoDevices => {
            "DeskPet 需要 NVIDIA GPU 才能运行。\n\n\
             NVIDIA 驱动已装，但没有检测到任何 NVIDIA 显卡。\n\
             请确认显卡已正确连接、未被外接坞禁用。\n\n\
             详细故障排查见 docs/PACKAGING.md#硬件前置检查"
                .to_string()
        }
        GpuCheckError::DeviceQueryFailed(detail) => format!(
            "DeskPet 需要 NVIDIA GPU 才能运行。\n\n\
             检测到 NVIDIA 显卡但查询失败，可能驱动已损坏。\n\
             请尝试重装最新版 NVIDIA 驱动。\n\n\
             [技术细节] {detail}"
        ),
    }
}

/// Detect the first NVIDIA GPU via nvml-wrapper.
///
/// Returns the first device's info on success, or the most specific
/// error on failure. Caller is expected to render `format_user_message`
/// to the user in a dialog before exit(1).
#[cfg(not(test))]
pub fn detect_nvidia_gpu() -> Result<GpuInfo, GpuCheckError> {
    use nvml_wrapper::Nvml;

    let nvml = Nvml::init()
        .map_err(|e| GpuCheckError::NvmlInitFailed(e.to_string()))?;

    let count = nvml
        .device_count()
        .map_err(|e| GpuCheckError::DeviceQueryFailed(e.to_string()))?;
    if count == 0 {
        return Err(GpuCheckError::NoDevices);
    }

    let device = nvml
        .device_by_index(0)
        .map_err(|e| GpuCheckError::DeviceQueryFailed(e.to_string()))?;

    let name = device
        .name()
        .map_err(|e| GpuCheckError::DeviceQueryFailed(e.to_string()))?;
    let mem = device
        .memory_info()
        .map_err(|e| GpuCheckError::DeviceQueryFailed(e.to_string()))?;
    let driver_version = nvml
        .sys_driver_version()
        .unwrap_or_else(|_| "unknown".to_string());

    Ok(GpuInfo {
        name,
        vram_gb: mem.total as f64 / (1024.0 * 1024.0 * 1024.0),
        driver_version,
    })
}

/// Test-only stub — real NVML isn't available in `cargo test`
/// (would need the dev machine's driver to be exactly right and
/// would make CI flaky). The stub is never called; callers under
/// `#[cfg(test)]` exercise `format_user_message` directly.
#[cfg(test)]
pub fn detect_nvidia_gpu() -> Result<GpuInfo, GpuCheckError> {
    Err(GpuCheckError::NvmlInitFailed("test stub".into()))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn format_nvml_init_failed_mentions_driver() {
        let err = GpuCheckError::NvmlInitFailed("NVML_ERROR_LIBRARY_NOT_FOUND".into());
        let msg = format_user_message(&err);
        assert!(msg.contains("NVIDIA"));
        assert!(msg.contains("NVML_ERROR_LIBRARY_NOT_FOUND"));
        assert!(msg.contains("驱动"));
    }

    #[test]
    fn format_no_devices_mentions_no_gpu() {
        let msg = format_user_message(&GpuCheckError::NoDevices);
        assert!(msg.contains("NVIDIA"));
        // must NOT contain a generic "driver missing" since here the
        // driver is actually present
        assert!(!msg.contains("NVML_ERROR"));
        assert!(msg.contains("没有检测到"));
    }

    #[test]
    fn format_device_query_failed_points_at_reinstall() {
        let err = GpuCheckError::DeviceQueryFailed("ERROR_UNKNOWN".into());
        let msg = format_user_message(&err);
        assert!(msg.contains("重装") || msg.contains("重新安装"));
        assert!(msg.contains("ERROR_UNKNOWN"));
    }

    #[test]
    fn format_messages_are_nonempty_for_all_variants() {
        let variants = [
            GpuCheckError::NvmlInitFailed("x".into()),
            GpuCheckError::NoDevices,
            GpuCheckError::DeviceQueryFailed("y".into()),
        ];
        for v in &variants {
            assert!(!format_user_message(v).is_empty());
        }
    }

    #[test]
    fn detect_stub_returns_err_in_test_build() {
        // Sanity: the #[cfg(test)] stub is what runs, not the real
        // NVML call — keeps CI deterministic.
        assert!(detect_nvidia_gpu().is_err());
    }
}
