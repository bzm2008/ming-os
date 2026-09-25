# 铭荼插件审计清单

铭荼首期遵循“先找社区项目，再做适配”的规则。插件清单的机器可读版本位于 `assets/ming-tea-plugins.json`，构建阶段会把它安装到 `/usr/share/ming-os/ming-tea/plugins.json` 并做内容校验。

## 社区来源

- [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness)：运行时和官方插件接口，MIT，当前处于 developer preview。
- [DSH 插件主题](https://github.com/topics/dsh-plugin)：发现可复用的浏览器、记忆、桌面和工作流插件。
- [awesome-dsh-plugin](https://github.com/awesome-dsh-plugin/awesome-dsh-plugin)：社区插件精选索引。
- [dsh-desktop](https://github.com/anywhere-labs/dsh-desktop)：桌面交互和插件化 UI 的参考实现；铭荼不直接复制其网页壳。
- [dsh-web](https://github.com/zhu1090093659/dsh-web)：插件聚合和分发的参考实现；铭荼仅借鉴清单/发现思路。
- [LibreOffice core](https://github.com/LibreOffice/core)：办公能力使用系统已有 LibreOffice/UNO 运行时，不把第三方二进制复制进 ISO。

## 当前适配策略

| 能力 | 铭荼适配方式 | 默认权限 |
| --- | --- | --- |
| 浏览器 | 复用 DSH 工具接口，经本地审批层接入 | 读取/打开自动；提交/上传确认 |
| 终端与诊断 | 优先调用 Ming OS 现有诊断工具 | 低风险命令自动；sudo/包管理确认 |
| 办公 | 通过系统 LibreOffice/UNO 适配 | 读取自动；写入/上传确认 |
| 学习笔记 | 本地 Markdown 扩展，避免常驻向量库 | 读取自动；写入确认 |

## 进入正式 ISO 前的门槛

每个插件必须记录固定版本、许可证、运行时、权限、Debian 13 兼容性、低配置可用性和适配说明。未满足任一项时，插件只能留在开发目录，不能进入正式构建。

构建门会检查：

- 插件清单存在且包含核心适配器。
- 铭荼桌面入口、运行时和清单同时存在。
- 桌面应用没有回退到 `127.0.0.1:3080` 网页入口。
- 审批事件和本地审计日志仍由铭荼核心策略统一处理。
