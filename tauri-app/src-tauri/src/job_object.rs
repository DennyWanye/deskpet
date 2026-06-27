// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

//! Windows Job Object 守卫 —— 杜绝 python backend 孤儿进程。
//!
//! ## 为什么需要它
//!
//! 之前的退出路径（toolbar ⏻ `app_exit` / 托盘 Quit / 主窗 `Destroyed`）
//! 都靠 `BackendProcess::kill_child()` 去 `child.kill()`。但 supervisor 线程
//! 在调用 `child.wait()` 前已经把 `Child` 从 Mutex 里 `take()` 走持有在自己
//! 栈上，所以 99.99% 时间 `state.child` 是 `None` —— `kill_child()` 形同空操作，
//! python 后端根本没被杀。再加上 **Windows 不会随父进程退出自动回收子进程**，
//! deskpet.exe 退出后 python 残留，继续占用 8100 端口，重开即报"启动失败"。
//!
//! ## 机制
//!
//! 进程启动后创建一个设置了 `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` 的匿名
//! Job Object，并把每个 spawn 出来的 backend 子进程 assign 进去。只要持有
//! 该 job 的最后一个句柄被关闭 —— 无论是 app 正常退出、panic，还是被
//! `taskkill` 杀掉 deskpet.exe，OS 都会在进程终止时关闭其所有句柄，从而
//! 触发 KILL_ON_JOB_CLOSE，**连带终止 job 内的全部子进程**。这是 Windows
//! 上"子进程绑定父进程生命周期"的规范做法，比依赖优雅关闭代码更可靠
//! （同时覆盖 CLAUDE.md 反复踩的孤儿进程坑 #1 / #7）。
//!
//! 非 Windows 平台为 no-op（Unix 下 backend 生命周期另行处理，不在本次范围）。

#[cfg(windows)]
mod imp {
    use std::ffi::c_void;
    use std::os::windows::io::AsRawHandle;
    use std::process::Child;
    use std::sync::OnceLock;

    use windows::core::PCWSTR;
    use windows::Win32::Foundation::{CloseHandle, HANDLE};
    use windows::Win32::System::JobObjects::{
        AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
        SetInformationJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    };

    /// 拥有一个 Job 句柄；Drop 时 `CloseHandle`。对设置了 KILL_ON_JOB_CLOSE
    /// 的 job 而言，关掉最后一个句柄即触发杀掉 job 内全部进程 —— 单测正是
    /// 靠这一点验证机制（见 tests::kill_on_close_terminates_child）。
    pub struct Job(HANDLE);

    // HANDLE 是裸指针包装；我们只在创建线程 / 单测里访问，手动断言可安全
    // 在线程间持有（实际全局单例只读共享）。
    unsafe impl Send for Job {}
    unsafe impl Sync for Job {}

    impl Drop for Job {
        fn drop(&mut self) {
            unsafe {
                let _ = CloseHandle(self.0);
            }
        }
    }

    impl Job {
        /// 创建一个匿名的、KILL_ON_JOB_CLOSE 的 Job。任何一步失败返回 `None`，
        /// 调用方据此降级（仅打日志，不影响 backend 启动）。
        pub fn create_kill_on_close() -> Option<Job> {
            unsafe {
                let handle = CreateJobObjectW(None, PCWSTR::null()).ok()?;
                let mut info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
                info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
                if SetInformationJobObject(
                    handle,
                    JobObjectExtendedLimitInformation,
                    &info as *const _ as *const c_void,
                    std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
                )
                .is_err()
                {
                    let _ = CloseHandle(handle);
                    return None;
                }
                Some(Job(handle))
            }
        }

        /// 把一个子进程加入本 job。返回是否成功。
        pub fn assign(&self, child: &Child) -> bool {
            unsafe {
                let h = HANDLE(child.as_raw_handle());
                AssignProcessToJobObject(self.0, h).is_ok()
            }
        }
    }

    /// 进程级单例 —— 整个 app 生命周期持有，**绝不主动 Drop**。进程退出时由
    /// OS 关闭其句柄触发 KILL_ON_JOB_CLOSE。
    static GLOBAL_JOB: OnceLock<Option<Job>> = OnceLock::new();

    /// 把 backend 子进程绑定到全局 job。`spawn` 成功后调用一次即可。
    /// 失败只打日志、不报错 —— 即使保护没生效，也不该阻断 backend 启动。
    pub fn assign_to_global(child: &Child) {
        match GLOBAL_JOB.get_or_init(Job::create_kill_on_close) {
            Some(job) => {
                if !job.assign(child) {
                    eprintln!(
                        "[job_object] AssignProcessToJobObject 失败 —— backend 可能在 \
                         deskpet.exe 异常退出后残留占用端口"
                    );
                }
            }
            None => {
                eprintln!(
                    "[job_object] 创建 Job Object 失败 —— backend 不受 kill-on-close 保护"
                );
            }
        }
    }

    #[cfg(test)]
    mod tests {
        use super::*;
        use std::os::windows::process::CommandExt;
        use std::process::Command;
        use std::time::{Duration, Instant};

        /// 核心机制验证：把一个长命子进程加入 job，关掉 job 的唯一句柄后，
        /// 子进程应被 OS 自动终止。
        #[test]
        fn kill_on_close_terminates_child() {
            let job = Job::create_kill_on_close().expect("应能创建 job object");

            // ping -n 30 localhost ≈ 存活 30s，远长于本测试。
            // CREATE_NO_WINDOW(0x08000000) 避免弹控制台窗口。
            let mut child = Command::new("ping")
                .args(["-n", "30", "127.0.0.1"])
                .creation_flags(0x08000000)
                .spawn()
                .expect("应能 spawn ping 子进程");

            assert!(job.assign(&child), "应能把子进程 assign 进 job");
            assert!(
                child.try_wait().expect("try_wait").is_none(),
                "assign 后子进程应仍在运行"
            );

            // 关掉 job 唯一句柄 → 触发 KILL_ON_JOB_CLOSE。
            drop(job);

            // 轮询等待子进程被杀（给 10s 上限）。
            let start = Instant::now();
            loop {
                if child.try_wait().expect("try_wait").is_some() {
                    return; // 已被 job 关闭终止 —— 通过
                }
                if start.elapsed() > Duration::from_secs(10) {
                    let _ = child.kill();
                    panic!("子进程未在 job 关闭后 10s 内被终止");
                }
                std::thread::sleep(Duration::from_millis(100));
            }
        }
    }
}

#[cfg(not(windows))]
mod imp {
    use std::process::Child;

    /// 非 Windows 平台 no-op。
    pub fn assign_to_global(_child: &Child) {}
}

pub use imp::assign_to_global;
