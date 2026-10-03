# 铭荼桌面应用 —— 系统构建接入与多平台打包（2026-10-03）

本文描述铭荼从「开发树里的 Tauri 应用」到「随 ming-os 系统构建分发 + 三平台打包」的接入方式，
以及每一步的**已验证/未验证**边界。

## 一、ming-os（Debian 13）系统构建接入

### 构建流程里发生了什么

```
build_onion_os.sh（构建宿主机）
  └─ run_modules() 依次在 chroot 里跑 modules/*.sh
       ├─ 03_desktop.sh     铭荼 GTK 原生壳（入口改名「铭荼（经典）」）
       └─ 04_ming_tea_desktop.sh   ★ 新增：铭荼 DSH 桌面（本批接入）
```

**模块 04 做的事**（`modules/04_ming_tea_desktop.sh`，fail-closed —— 任一步失败整个 ISO 构建失败）：

1. `receipt.json` sha256 逐文件校验（`assets/vendor/ming-tea-desktop/` 下的每个产物）；
2. `apt` 安装运行依赖：`libwebkit2gtk-4.1-0 libgtk-3-0 libayatana-appindicator3-1 librsvg2-2 libxdo3 libssl3`；
3. `dpkg` 安装 `ming-tea-desktop_amd64.deb`（先校验容器与数据档，照 xiahai/papyrus 的口径）；
4. DSH runtime 解到 `/opt/ming-tea/runtime`（含 `dsh` CLI、全部社区插件），锁文件一起落地；
5. 热键守护进程 `/opt/ming-tea/bin/ming-tea-hotkey` + `/etc/xdg/autostart/ming-tea-hotkey.desktop`
   （登录后常驻 —— 应用完全退出后热键仍能呼出面板）；
6. 启动器 `/usr/local/bin/ming-tea-dsh` + 桌面入口 `ming-tea-dsh.desktop`。

### 构建产物从哪来（关键：不在 chroot 里编译）

Tauri/WebKitGTK 在 chroot 里构建需要 ~2GB 工具链、20+ 分钟，且会让 ISO 构建的输入哈希
随 rustc/pnpm 版本漂移。所以产物由 **构建宿主机** 提前产出：

```bash
scripts/prepare_ming_tea_desktop.sh
```

产出 `assets/vendor/ming-tea-desktop/`：

| 文件 | 说明 |
| --- | --- |
| `ming-tea-desktop_amd64.deb` | Tauri Linux 包（只能在 Linux 上产出） |
| `ming-tea-hotkey` | 全局热键守护进程（Linux x86_64） |
| `ming-tea-runtime.tar.zst`（无 zstd 时 `.tar.gz`） | DSH runtime（node_modules + dsh CLI） |
| `ming-tea-dsh-lock.json` | 插件锁副本 |
| `receipt.json` | 每个文件的 sha256（模块 04 校验） |

**`.gitignore` 已排除该目录**（179 MB 级），产物由 CI 或 Debian 13 宿主产出。

### 已验证 / 未验证（如实）

- ✅ 模块脚本 `bash -n` 语法、`build_onion_os.sh` 模块列表接入、runtime 打包全流程
  （本机 macOS 实跑，产出 179 MB tar.gz + receipt）；
- ❌ **完整 ISO 构建未在本机跑过**（需要 Debian 13 宿主 + debootstrap），模块 04 的
  chroot 内行为（dpkg 安装、WebKitGTK 依赖解析、autostart）在真实构建里首次执行；
- ❌ `.deb` 与 Linux 守护进程二进制未产出（本机是 macOS），CI 的
  `ubuntu-22.04 / deb,appimage` 矩阵会在推送后产出。

## 二、三平台打包通道

`platform/ming-tea/apps/desktop/src-tauri/tauri.conf.json`：`targets: "all"`，
新增 `linux`（deb depends：webkit2gtk-4.1/gtk3/appindicator/rsvg；appimage）与
`windows`（NSIS `currentUser` 安装、WebView2 embedBootstrapper）配置。

CI（`.github/workflows/ming-tea-platform.yml`）：

| runner | target | bundles | 说明 |
| --- | --- | --- | --- |
| macos-latest | aarch64-apple-darwin | `app,dmg` | Apple Silicon |
| macos-latest | x86_64-apple-darwin | `app,dmg` | Intel |
| ubuntu-22.04 | x86_64-unknown-linux-gnu | `deb,appimage` | 装 WebKitGTK 开发头 |
| windows-latest | x86_64-pc-windows-msvc | `nsis` | NSIS currentUser |

契约测试矩阵也加了 ubuntu（铭荼要进 ming-os，插件/代理半区不能只在 mac/win 成立）。

### 代码侧为跨平台做的改动

- `launch_agent.rs` 平台拆分：macOS 原实现（LaunchAgent）移入 `launch_agent_macos.rs`；
  非 macOS 的 `status()` **如实**报告随登录启动由哪一层负责（Linux：`/etc/xdg/autostart`，
  ming-os 构建安装；Windows：HKCU Run，安装器写），`install/uninstall` 拒绝运行期修改
  而不是假装成功；
- `logging.rs` 平台分支：macOS `~/Library/Logs/铭荼/app.log`、Linux
  `${XDG_STATE_HOME:-~/.local/state}/ming-tea/app.log`、Windows 暂不重定向（如实注释）；
- 图标集 `icons/`（ico/icns/png，从自有资产 ming-os-logo 生成）—— Windows 的
  tauri-build 强制要求 `icon.ico`。

### 已验证 / 未验证

- ✅ macOS 打包实测（`tauri build --bundles app` 全绿）；
- ✅ 热键守护进程对 `x86_64-pc-windows-msvc` **cargo check 通过**（global-hotkey 0.8
  三平台支持：Carbon / x11rb / windows-sys）；
- ⚠️ 桌面壳对 Windows 的检查在 `tauri-winres` 阶段中止：macOS 没有 `llvm-rc`
  （Windows 资源编译器）—— 这是环境边界，不是代码问题，windows-latest runner 上有全套工具链；
- ❌ Linux/Windows 的真实产物未构建（CI 推送后由矩阵产出）；
- ❌ macOS dmg 未出（本地 `bundle_dmg.sh` 失败的问题自 2026-09-26 就存在；CI 的 dmg
  走另一条路径，未实测）；
- ❌ Windows NSIS / Linux deb 的安装行为未实测。

## 三、下一次系统构建带什么

构建机（Debian 13）上：

```bash
# 1. 先在任意 Linux 机（或 CI）产出桌面产物，放进仓库：
scripts/prepare_ming_tea_desktop.sh          # 产出 assets/vendor/ming-tea-desktop/
# 2. 正常跑 ming-os 构建（模块列表已含 04）：
./build_onion_os.sh
```

若构建时不放 `assets/vendor/ming-tea-desktop/`，模块 04 会在 `verify_receipt` 一步
**明确失败**（不会静默跳过，也不会装出半套东西）—— 这是刻意的：要么带上、要么不带，
不允许「带了一半」。
