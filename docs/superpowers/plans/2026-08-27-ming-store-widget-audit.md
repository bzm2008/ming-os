# Ming Store and Desktop Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将已确认的 V2 预览落实为可验证的 Ming OS 桌面与应用商店改动。

**Architecture:** 保留现有 GTK/Xfce 运行时边界。小组件和抽屉只改现有 `ming-phone-desktop.py`、`ming-app-drawer.py` 与 `modules/03_desktop.sh` 的安装契约；商店新增 AppStream 目录适配和国内 APT 源选择，不让 UI 直接执行命令。OTA 只做审计加固，不改变签名协议。

**Tech Stack:** Python 3, GTK3/GTK4, GLib/GIO, POSIX shell, AppStream XML, APT/dpkg, unittest。

---

### Task 1: 小组件与抽屉固定布局

**Files:** `tests/test_status_widget_layout_v2.py`, `assets/ming-phone-desktop.py`, `assets/ming-app-drawer.py`, `modules/03_desktop.sh`

- [ ] 写测试：确认展开面板使用独立浮层、胶囊固定尺寸、应用抽屉底部锚定、右键包含添加到桌面/新建空白文件。
- [ ] 运行测试确认当前实现缺少契约。
- [ ] 实现最小布局和安装链改动。
- [ ] 定向测试、编译并提交。

### Task 2: AppStream 与国内软件源

**Files:** `tests/test_ming_store_appstream.py`, `assets/ming-store-core.py`, `assets/ming-store.py`, `modules/01_base.sh`, `assets/ming-store-catalog/debian-apt.json`

- [ ] 写测试：解析 AppStream XML、过滤 amd64/desktop ID、目录超过 1000、重复包去重、阿里/清华/中科大/官方源候选和失败回退。
- [ ] 运行测试确认当前静态 13 条目录失败。
- [ ] 实现 AppStream provider、缓存和受控源选择；不伪造厂商 SHA256。
- [ ] 定向测试、编译并提交。

### Task 3: Xfce 可见入口与模板

**Files:** `tests/test_ming_desktop_surface.py`, `modules/03_desktop.sh`, `assets/ming-files.py`

- [ ] 写测试：Ming 设置覆盖的功能入口有对应页面；可见抽屉不含被隐藏的 Xfce 原版入口；`/etc/skel/Templates` 含空白文件模板；右键和文件管理器都能新建文件/文件夹。
- [ ] 运行测试确认当前模板和入口清单不完整。
- [ ] 实现模板初始化、桌面右键菜单项和必要 Xfce `NoDisplay` 迁移。
- [ ] 定向测试、Shell 语法并提交。

### Task 4: OTA 对抗性审计与门禁

**Files:** `tests/test_ota_adversarial_audit.py`, `modules/06_ota_update.sh`

- [ ] 写测试：旧域名不作为自动可信回退、schema/signature/版本前进必需、下载/备份/GRUB/回滚失败可读并保留安全状态。
- [ ] 运行测试确认遗漏。
- [ ] 实现最小门禁和诊断状态补强。
- [ ] 定向测试、Shell 语法并提交。

### Task 5: 全量验证

- [ ] 运行全量 unittest、Python 编译、Shell `bash -n`、`git diff --check`。
- [ ] 复核构建安装链和最终差异，提交实现。
