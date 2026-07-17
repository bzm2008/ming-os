# Ming OS 发布信任与双副本恢复保全设计

状态：设计已获方案 A 批准，待实施前审查
适用版本：Ming OS 26.4.0 及后续事务型 OTA 发布

## 1. 目标与边界

目标是避免“ISO 还在，但签名密钥、bootstrap 或 OTA 发布链无法恢复”。任何一台发布机、生产服务器或工作树损坏后，恢复人员仍能在受控离线工作站上恢复签名能力，并证明恢复材料与公开信任根一致。

本设计不改变事务 OTA 引擎、manifest 协议、initramfs、GRUB、rollback journal 或 recovery ISO 的同盘保护逻辑，也不替换内核和驱动。

## 2. GitHub 公开仓库边界

允许保存：

- 只含公钥的 `assets/trust/ming-ota-release-keyring.gpg`；
- `assets/trust/ming-ota-key-policy.json` 和已审核指纹；
- manifest、content-index、bootstrap、payload 的 detached signature；
- 公开 manifest、SHA256、版本说明、发布收据和校验脚本；
- 主域名 `ming.scallion.uno` 及备用域名 `ming.sca-hub.cn` 的登记说明。

禁止保存：

- 任何 `sec`/`ssb` 私钥、私钥导出文本、口令或恢复码；
- NAS 凭据、反向隧道 token、SSH 私钥或生产环境变量；
- 加密恢复包本体，即使文件后缀是 `.age`、`.gpg` 或其他密文后缀；
- 未发布签名工作区、临时 GPG home、构建机镜像和带私有路径的日志。

加密恢复包不放 GitHub：公开对象无法真正撤回，密文可被长期离线猜测，且文件名、大小和时间会泄露密钥生命周期信息。GitHub 只保存恢复包的不可逆 SHA256 和不含主机信息的 opaque bundle id。

## 3. 信任材料与密钥分层

发布密钥分为离线主密钥和受限签名子密钥。主密钥只用于管理、撤销和恢复；正常发布只使用签名子密钥。公开 keyring 包含主密钥和签名子密钥的公钥部分，policy 固定允许的 primary fingerprint 和 signing fingerprint。

长期保管的私钥只存在于两个加密副本中：

1. 受控发布工作站上的本地加密发布库；
2. NAS 上的独立加密恢复包。

生产服务器永远不持有私钥，也不持有解密口令。构建机只接收公开信任材料和已签名发布物。

签名时可以在隔离、临时的 GPG home 中短暂解密并导入签名子密钥；签名结束后必须销毁临时 GPG home、明文导出和缓存，且不得把它作为第三份长期副本。

恢复包采用审核过的 `age` v1 密文格式和高熵人工保管口令。恢复包内包含私钥导出、撤销证书、公钥指纹、key policy、生成代次、格式版本和每个文件的 SHA256；不包含在线 token 或服务器登录凭据。

## 4. 目录与权限边界

仓库公开路径：

```text
assets/trust/ming-ota-release-keyring.gpg
assets/trust/ming-ota-key-policy.json
docs/releases/<version>-release-receipt.json
```

受控工作站私有路径由 `MING_RELEASE_VAULT` 显式指定，禁止默认落入仓库：

```text
<MING_RELEASE_VAULT>/
  public/release-keyring.gpg
  public/key-policy.json
  encrypted/recovery-bundle-<generation>.age
  encrypted/recovery-bundle-<generation>.sha256
  receipts/recovery-bundle-<generation>.json
  audit/preflight-<timestamp>.json
```

NAS 目录：

```text
<NAS_RELEASE_VAULT>/ming-os/release-vault/v1/
  recovery-bundle-<generation>.age
  recovery-bundle-<generation>.sha256
  recovery-bundle-<generation>.json
```

NAS 目录不得被 Web、NFS guest 或生产应用公开。对象禁止符号链接，权限只允许受限账户访问和管理员审计。

## 5. 反向 SSH 隧道边界

生产服务器继续通过现有阿里云 FRP/反向 SSH 链路访问 NAS，不新增 NAS 公网监听端口。隧道只允许访问固定 NAS SSH 服务和固定恢复包目录。

生产服务器在 NAS 使用单独的只读 SSH key：

- `restrict`、无 PTY、无 agent forwarding、无 X11 forwarding；
- forced command 只允许 `stat`、读取指定对象和计算 SHA256；
- 拒绝 shell、任意路径、删除、重命名、上传和命令替换；
- `known_hosts` 固定 NAS 主机公钥指纹，指纹变化立即失败。

恢复包上传由离线发布工作站通过单独的人工授权上传 key 完成；生产服务器只读 key 不得复用上传权限。

## 6. 公开收据

每一代恢复包生成 JSON 收据。收据可以进入公开仓库，但只包含非秘密字段：

```json
{
  "format": "ming-release-vault-receipt-v1",
  "bundle_id": "opaque-generation-id",
  "generation": 1,
  "primary_fingerprint": "40-or-64-hex",
  "signing_fingerprint": "40-or-64-hex",
  "bundle_sha256": "64-hex",
  "bundle_bytes": 0,
  "public_keyring_sha256": "64-hex",
  "key_policy_sha256": "64-hex",
  "encryption_format": "age-v1",
  "created_at": "RFC3339 timestamp",
  "nas_object": "opaque-generation-id",
  "status": "verified"
}
```

收据禁止包含 NAS 地址、SSH 用户名、文件系统路径、口令、私钥内容或生产机标识。hash 和大小写入后必须重新读回确认。

## 7. 发布流程与强制门禁

发布负责人必须按以下顺序操作：

1. 在隔离发布工作站创建临时 GPG home，导入离线签名子密钥并核对 fingerprint；导入完成后断开普通网络。
2. 导入公开 keyring 和 policy，确认允许的 fingerprint 与工作站签名状态一致。
3. 生成 bootstrap、manifest、content-index、payload 及 detached signature；每个签名立即用独立临时 keyring 和 `gpgv` 回读验证。
4. 生成恢复包收据，通过 NAS 上传 key 上传密文、sidecar 和收据；下载回本机后重新计算 SHA256。
5. 执行发布前门禁：本地密文、NAS 密文、收据、公钥、policy、bootstrap 签名、manifest 签名和 payload 索引全部一致。
6. 只把公开产物和不含秘密的收据推送 GitHub，再部署公开对象和 discovery。任一密钥缺失、NAS 不可达、hash 不一致或签名不匹配都禁止构建和发布。
7. 使用 26.3.2 bootstrap 能力检测和 26.4.0 discovery 契约验收；不能把 legacy 26.3.2 JSON 当作事务 OTA 成功。

状态必须区分 `prepared`、`signed`、`backups-verified`、`published` 和 `rejected`。已下载、已上传或已生成签名不能显示为已发布。

## 8. 每月校验与恢复演练

每月自动校验只读执行：

- 通过反向 SSH 隧道读取 NAS 对象元数据和密文；
- 重新计算 SHA256，与 NAS sidecar 和公开收据比较；
- 核对权限、符号链接状态、SSH 主机指纹和剩余空间；
- 确认程序没有调用 `gpg --decrypt`、没有读取口令、没有产生明文临时文件；
- 写入结构化结果，保留最近 12 个月记录。

每季度由发布负责人手动进行一次离线恢复演练：下载密文到一次性工作区，人工输入口令，解密到内存或临时加密盘，核对指纹、撤销证书、policy 和文件 hash，然后销毁明文和 GPG home。演练失败时冻结发布，不自动生成替代密钥。

## 9. 失败码与日志

- `E_VAULT_NOT_CONFIGURED`：未配置本地或 NAS vault；
- `E_VAULT_UNREACHABLE`：隧道、NAS SSH 或主机指纹校验失败；
- `E_VAULT_PERMISSION`：目录、对象或 key 权限不符合要求；
- `E_VAULT_HASH_MISMATCH`：本地、NAS、sidecar 或收据 hash 不一致；
- `E_PUBLIC_TRUST_MISMATCH`：keyring、policy 或 fingerprint 不一致；
- `E_SECRET_EXPOSURE`：私钥、口令、恢复包或敏感路径进入 Git、构建上下文或公开产物；
- `E_SIGNING_KEY_UNAVAILABLE`：签名子密钥不可用；
- `E_RECOVERY_DECRYPT_FAILED`：口令或密文格式校验失败；
- `E_RECEIPT_STALE`：收据过期或代次不一致；
- `E_RELEASE_NOT_READY`：前置门禁未全部通过。

日志位置：发布工作站 `<MING_RELEASE_VAULT>/audit/`；生产服务器 `/var/log/ming-os/release-vault-check.jsonl`；桌面诊断只读取脱敏摘要。

## 10. 测试契约

实现时先写失败测试并观察失败，再实现最小逻辑：

- secret key、恢复包、口令、NAS 凭据和私有路径进入 Git 时被拒绝；公开 keyring、policy、signature 和 hash 被允许；
- keyring 只含公钥，policy fingerprint 与 keyring 一致；
- 本地、NAS、sidecar 和收据 hash 不一致时发布失败；
- NAS 断开、主机指纹变化、受限命令越权和路径穿越均失败；
- 月度检查不解密、不落地明文、不把口令传给子进程；
- 错误口令和篡改密文恢复失败；
- 缺 trust material 时现有构建门禁继续失败；
- 26.3.2 未 bootstrap 时只返回官方签名 bootstrap 指引；
- HTML、legacy JSON、错误版本或未签名对象使公开验收失败；
- Shell、Python、完整 unittest、diff 检查和 secret scanner 全部通过。

## 11. 权限和接口边界

核心 OTA 负责人独占修改 keyring、key policy、事务验证器、bootstrap 能力检测、initramfs、GRUB 和 rollback journal。发布侧只修改收据 schema、vault 校验、月度 timer、诊断导出、官网/GitHub 文案和公开对象配置。

Terra 只消费已签名 discovery、manifest、content-index、payload locator 和 fingerprint，不接触私钥、NAS 密文或解密接口。Luna 只消费结构化状态、收据摘要和失败码，不解析终端文本、不执行 GPG、不接受任意路径。

## 12. 回退与密钥丢失

若本地副本损坏但 NAS 密文可恢复，发布负责人在离线工作站人工恢复并重建本地副本。若两份密文或独立口令均不可用，不得生成替代密钥继续发布；应冻结事务 OTA，保留旧版本手动升级路径，并由核心负责人启动密钥轮换和兼容窗口流程。
