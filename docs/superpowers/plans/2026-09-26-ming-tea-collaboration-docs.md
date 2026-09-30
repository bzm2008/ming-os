# 铭荼协作文档 Implementation Plan

> **For agentic workers:** This documentation change is complete in the current worktree; future implementation work must read `docs/ming-tea-live-status.md` first.

**Goal:** 建立一份准确的铭荼项目说明和一份由 Codex/ZCode 共享的实时状态文档，并将入口链接放入根 README 和跨平台 README。

**Architecture:** 项目说明描述稳定的产品、架构、安全和构建边界；实时状态记录带日期的实现证据、阻塞、插件状态和下一步。机器可读插件清单与文档保持一致，避免把锁定包误报为已安装。

**Tech Stack:** Markdown、JSON、现有 TypeScript/Tauri/DSH workspace 文档。

**Spec:** 用户要求的“工作室介绍文档、项目说明文档和 Codex/ZCode 实时同步文档”。

## Global Constraints

- 继续开发前必须读取 `docs/ming-tea-live-status.md`。
- 只记录可验证事实；状态只能使用 `done`、`in-progress`、`blocked`、`planned`。
- 不把社区插件锁文件登记写成安装成功。
- OpenCode/Big Pickle 保持已移除，不重新加入。

### Task 1: 建立项目说明

**Files:**
- Create: `docs/ming-tea-project.md`

- [x] 记录产品定位、平台、场景、架构、社区插件、权限、模型、OTA、构建和验证边界。

### Task 2: 建立实时协作状态

**Files:**
- Create: `docs/ming-tea-live-status.md`

- [x] 记录工作树、完成项、进行中、阻塞、决策、插件状态、验证证据、下一步和更新规则。

### Task 3: 接入文档入口并校准事实

**Files:**
- Modify: `README.md`
- Modify: `platform/ming-tea/README.md`
- Modify: `assets/ming-tea-community-candidates.json`
- Modify: `docs/ming-tea-plugin-audit.md`

- [x] 添加两份文档链接，并将 dsh-codex-ui 与 dsh-data-agent 的状态文字改为与当前安装证据一致。
