//! 诊断用探针：注册 `ctrl+alt+shift+f9` 并在触发时打印 `TRIGGERED`。
//!
//! 为什么要它（2026-10-02）：用应用自己的电脑操作工具发**合成**的全局组合键时，
//! 守护进程日志里一条「触发」都没有。要区分两种可能 ——
//! ①合成事件根本到不了 Carbon 热键；②我们的守护进程事件处理有问题 ——
//! 就需要一个**同构但极简**的对照：同一个 crate、同一个组合键、同样在主线程跑 run loop。
//! 探针收到而守护进程收不到 ⇒ 是我们的问题；两者都收不到 ⇒ 大概率是合成事件被过滤。
//!
//! 用法（先停掉守护进程，避免两个进程抢同一个键）：
//! ```text
//! launchctl bootout gui/$(id -u)/cn.mingos.mingtea.hotkey
//! cargo run --release --example hotkey_probe     # 前台跑，看到 TRIGGERED 即证明
//! launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/cn.mingos.mingtea.hotkey.plist
//! ```

use std::io::Write;
use std::time::Duration;

use global_hotkey::hotkey::{Code, HotKey, Modifiers};
use global_hotkey::{GlobalHotKeyEvent, GlobalHotKeyManager, HotKeyState};

#[link(name = "CoreFoundation", kind = "framework")]
extern "C" {
    fn CFRunLoopRun();
}

fn main() {
    let manager = match GlobalHotKeyManager::new() {
        Ok(manager) => manager,
        Err(error) => {
            println!("PROBE-ERROR 创建管理器失败: {error}");
            return;
        }
    };
    let hotkey = HotKey::new(
        Some(Modifiers::CONTROL | Modifiers::ALT | Modifiers::SHIFT),
        Code::F9,
    );
    match manager.register(hotkey) {
        Ok(()) => println!("PROBE-REGISTERED ctrl+alt+shift+f9"),
        Err(error) => {
            println!("PROBE-ERROR 注册失败: {error}");
            return;
        }
    }
    let _ = std::io::stdout().flush();

    let receiver = GlobalHotKeyEvent::receiver();
    std::thread::spawn(move || loop {
        match receiver.recv_timeout(Duration::from_millis(200)) {
            Ok(event) if event.state() == HotKeyState::Pressed => {
                println!("TRIGGERED id={}", event.id());
                let _ = std::io::stdout().flush();
            }
            Ok(_) => {}
            Err(_) => {}
        }
    });

    println!("PROBE-WAITING 进入 run loop");
    let _ = std::io::stdout().flush();
    // SAFETY: 与守护进程完全相同的派发前提
    unsafe { CFRunLoopRun() };
}
