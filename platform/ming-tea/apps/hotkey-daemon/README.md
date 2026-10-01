# 铭荼热键守护进程

`ming-tea-hotkey` 是一个极小的常驻进程：它注册系统全局快捷键，在铭荼主应用完全退出时仍可响应快捷键，并通过 `open mingtea://summon` 唤起应用。

## 设置

默认读取：

- macOS：`~/Library/Application Support/铭荼/settings.json`
- 其它平台：`dirs::config_dir()/铭荼/settings.json`

可用环境变量 `MING_TEA_SETTINGS` 覆盖设置文件路径。文件支持的字段：

```json
{
  "hotkey": "alt+space"
}
```

`hotkey` 默认值为 `alt+space`。支持 `alt`/`option`、`ctrl`/`control`、`shift`、`cmd`/`super`/`meta` 修饰键，以及 `space`、字母、数字和 `F1`–`F12` 主键。快捷键被占用时进程会记录错误并每 30 秒重试，而不会退出。

日志追加写入 `~/Library/Logs/铭荼/hotkey.log`（其它平台使用 `dirs::config_dir()/铭荼/hotkey.log`），同时输出到 stderr。

本程序**不申请任何 TCC 权限**。使用的 Carbon 全局热键机制不需要辅助功能（Accessibility）权限。
