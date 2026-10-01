//! 系统权限的**真实**探测与引导。
//!
//! 背景：`packages/agent/src/platform-status.ts` 里的权限状态是**硬编码**的
//! （darwin 恒为 `available:false, permissionRequired:true`），那只能当提示语用。
//! 这个模块做真实探测，供面板与设置页显示「到底缺哪个」，并给一键跳系统设置。
//!
//! 三条权限的获取方式各不相同（macOS 无法程序化授予）：
//! - 辅助功能（Accessibility）：`AXIsProcessTrusted()`，控制鼠标键盘必需；
//! - 屏幕录制（Screen Recording）：`CGPreflightScreenCaptureAccess()`，截图必需；
//! - 麦克风（Microphone）：`AVCaptureDevice.authorizationStatusForMediaType`，语音输入必需。

#[derive(Clone, serde::Serialize)]
pub struct Capability {
    pub id: String,
    pub label: String,
    pub available: bool,
    pub permission_required: bool,
    pub error: Option<String>,
}

#[cfg(target_os = "macos")]
mod platform {
    #[link(name = "ApplicationServices", kind = "framework")]
    extern "C" {
        /// 辅助功能是否已授权。返回 bool 是 CoreFoundation 的 Boolean（u8 语义），
        /// 该函数在 macOS 上返回 Boolean，用 bool 接收与实测行为一致。
        fn AXIsProcessTrusted() -> bool;
    }

    #[link(name = "CoreGraphics", kind = "framework")]
    extern "C" {
        /// 屏幕录制是否已授权（不会弹窗，只查询）。
        fn CGPreflightScreenCaptureAccess() -> bool;
    }

    pub fn accessibility_trusted() -> bool {
        unsafe { AXIsProcessTrusted() }
    }

    pub fn screen_capture_allowed() -> bool {
        unsafe { CGPreflightScreenCaptureAccess() }
    }

    pub fn microphone_status() -> MicStatus {
        use objc2_av_foundation::{AVAuthorizationStatus, AVCaptureDevice, AVMediaTypeAudio};
        // extern static 取值本身就需要 unsafe（AVFoundation 的媒体类型常量）
        let Some(media_type) = (unsafe { AVMediaTypeAudio }) else {
            return MicStatus::Unknown;
        };
        // SAFETY: 只读查询授权状态；media_type 是 AVFoundation 的静态常量，进程内有效。
        let status = unsafe { AVCaptureDevice::authorizationStatusForMediaType(media_type) };
        if status == AVAuthorizationStatus::Authorized {
            MicStatus::Granted
        } else if status == AVAuthorizationStatus::NotDetermined {
            MicStatus::NotDetermined
        } else {
            MicStatus::Denied
        }
    }

    pub enum MicStatus {
        Granted,
        NotDetermined,
        Denied,
        Unknown,
    }
}

#[cfg(not(target_os = "macos"))]
mod platform {
    pub fn accessibility_trusted() -> bool {
        true
    }
    pub fn screen_capture_allowed() -> bool {
        true
    }
    pub fn microphone_status() -> MicStatus {
        MicStatus::Unknown
    }
    pub enum MicStatus {
        Granted,
        NotDetermined,
        Denied,
        Unknown,
    }
}

pub fn probe() -> Vec<Capability> {
    let mut list = Vec::new();

    #[cfg(target_os = "macos")]
    {
        let ax = platform::accessibility_trusted();
        list.push(Capability {
            id: "accessibility".into(),
            label: "辅助功能".into(),
            available: ax,
            permission_required: !ax,
            error: (!ax).then(|| "控制鼠标与键盘需要它：系统设置 › 隐私与安全性 › 辅助功能".into()),
        });

        let screen = platform::screen_capture_allowed();
        list.push(Capability {
            id: "screen-recording".into(),
            label: "屏幕录制".into(),
            available: screen,
            permission_required: !screen,
            error: (!screen).then(|| "「看屏幕」需要它：系统设置 › 隐私与安全性 › 屏幕录制".into()),
        });

        let mic = match platform::microphone_status() {
            platform::MicStatus::Granted => Capability {
                id: "microphone".into(),
                label: "麦克风".into(),
                available: true,
                permission_required: false,
                error: None,
            },
            platform::MicStatus::NotDetermined => Capability {
                id: "microphone".into(),
                label: "麦克风".into(),
                available: false,
                permission_required: true,
                error: Some("还没授权：第一次说话时会弹窗，或到 系统设置 › 隐私与安全性 › 麦克风 勾选".into()),
            },
            _ => Capability {
                id: "microphone".into(),
                label: "麦克风".into(),
                available: false,
                permission_required: true,
                error: Some("语音输入需要它：系统设置 › 隐私与安全性 › 麦克风".into()),
            },
        };
        list.push(mic);
    }

    #[cfg(not(target_os = "macos"))]
    {
        list.push(Capability {
            id: "accessibility".into(),
            label: "系统控制".into(),
            available: true,
            permission_required: false,
            error: None,
        });
    }

    // 与权限无关、但面板要显示的能力
    list.push(Capability {
        id: "terminal".into(),
        label: "终端".into(),
        available: true,
        permission_required: false,
        error: None,
    });
    list.push(Capability {
        id: "diagnostics".into(),
        label: "诊断".into(),
        available: true,
        permission_required: false,
        error: None,
    });

    list
}

/// 打开对应的系统设置页。macOS 用 `x-apple.systempreferences:` 深链。
pub fn open_settings(id: &str) -> Result<(), String> {
    #[cfg(target_os = "macos")]
    {
        let url = match id {
            "accessibility" => {
                "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
            }
            "screen-recording" => {
                "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"
            }
            "microphone" => {
                "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone"
            }
            other => return Err(format!("未知的权限项：{other}")),
        };
        std::process::Command::new("open")
            .arg(url)
            .status()
            .map_err(|e| e.to_string())?;
        Ok(())
    }
    #[cfg(not(target_os = "macos"))]
    {
        let _ = id;
        Err("只有 macOS 需要跳系统设置".into())
    }
}
