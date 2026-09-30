# 铭荼宠物素材规格

本文是制作铭荼自有桌宠素材的依据，来自宠物插件 `@linxin666/dsh-pet@0.4.3` 随包发布的
`contracts/pet-manifest-v2.schema.json` 与其内置样例（`assets/whale/`、`assets/ouo-neko/`）。

## 一只宠物 = 一个目录

```
$DSH_HOME/pets/<pet-id>/
  pet.json            # 清单，见下
  spritesheet.webp    # 图集
  previews/           # 可选：选择器里的预览动画，每个轨道一个 GIF
```

宠物目录按以下顺序解析，`$DSH_HOME/pets` 优先于旧版 Codex 目录：

1. `${CODEX_HOME:-~/.codex}/pets/<pet>/pet.json`
2. `$DSH_HOME/pets/<pet-id>/pet.json`（铭荼使用此路径）

## 图集几何（硬性）

| 项 | 值 |
| --- | --- |
| 格式 | **webp，带透明通道**（不用 GIF/APNG；GIF 只用于 `previews/`） |
| 网格 | **8 列**（`columns` 默认 8，可 1–32） |
| 单元格 | **192 × 208 px**（`cell` 默认即此值，可 1–2048） |
| 行数 | **9 行**（经典契约）；11 行是 Codex v2 的“视线行”，非必需 |
| 总尺寸 | 8 × 192 = **1536 宽**；9 × 208 = **1872 高** |

内置样例实测：`assets/whale/spritesheet.webp` = 1536 × 1872（9 行）；
`assets/ouo-neko/spritesheet.webp` = 1536 × 2288（11 行）。**未使用的格子必须全透明。**

## 行序与轨道名（固定，不可调换）

| 行号 | 轨道名 | 含义 |
| --- | --- | --- |
| 0 | `idle` | 待机 |
| 1 | `running-right` | 向右移动 |
| 2 | `running-left` | 向左移动 |
| 3 | `waving` | 挥手 |
| 4 | `jumping` | 跳跃 |
| 5 | `failed` | 失败 |
| 6 | `waiting` | 等待 |
| 7 | `running` | 奔跑 / 思考中 |
| 8 | `review` | 审阅 |

## 应用相位到轨道的映射

界面侧只有 7 个相位，通过 `sequences` 把它们编排成轨道序列（可重复、可插空）：

`idle`、`waiting`、`thinking`、`tool`、`review`、`done`、`failed`

## pet.json 清单

必需字段：`petManifestVersion`（固定 `2`）、`id`、`displayName`、`license`。
`renderer` 默认 `sprite2d`（另有 `live2d`、`frames2d`，铭荼不用）。

```json
{
  "petManifestVersion": 2,
  "id": "ming-tea",
  "displayName": "铭荼",
  "renderer": "sprite2d",
  "description": "铭荼的桌宠伙伴。",
  "license": "<素材许可证，必须填写>",
  "sprite2d": {
    "spritesheetPath": "spritesheet.webp",
    "cell": { "width": 192, "height": 208 },
    "columns": 8,
    "atlasRows": 9,
    "frames": [6, 8, 8, 4, 5, 8, 6, 6, 6],
    "tracks": {
      "idle": { "durations": [500, 500, 600, 500, 500, 600] }
    }
  },
  "sequences": {
    "idle": ["idle", "waving", "idle", "waiting", "idle"],
    "thinking": ["running", "running-right", "running", "running-left"],
    "tool": ["running-right", "running", "running-left"],
    "review": ["review", "waiting", "review"],
    "done": ["jumping", "waving", "jumping"],
    "failed": ["failed", "waiting", "failed"],
    "waiting": ["waiting", "idle", "waving"]
  }
}
```

- `frames`：逐行**实际使用的帧数**（≤ 列数），供渲染器裁剪空白格。
- `tracks.<轨道名>.durations`：该行逐帧时长（毫秒），长度需与 `frames` 对应。
- `sequences`：相位 → 轨道名序列。

## 校验

宠物插件的校验 CLI（`scripts/dsh-pet.cjs`）**未随 npm 包发布**，因此走两条替代路径：

1. 用 `contracts/pet-manifest-v2.schema.json` 对 `pet.json` 做 JSON Schema 校验。
2. 把宠物目录放进 `$DSH_HOME/pets/<id>/`，重启 profile，在宠物选择器里确认出现且动画正常。

## 还需要提供的素材

| 素材 | 必需性 | 说明 |
| --- | --- | --- |
| 宠物图集 `spritesheet.webp` | **必需** | 规格如上 |
| `pet.json` 里的 `license` 字段 | **必需** | 素材许可证，铭荼自有素材填我们自己的授权 |
| 预览 GIF（每轨道一个） | 可选 | 选择器/市场列表用；内置样例放在 `previews/*.gif` |
| 台词 `voice.json` | 可选 | 纯文本，无需录音；放在宠物目录或 `$DSH_HOME/pets/.voice.json` 全局覆盖 |
| 状态装饰徽标 | 可选 | 独立 schema `contracts/status-decoration-v1.schema.json`，放在 `$DSH_HOME/pets/decorations` |

**不需要**：Live2D 模型、背景大图、MP3/音频文件。

## 铭荼一致性生图流程

铭荼素材可以使用 `ming-tea-imagegen` 技能调用已审计的批量生图端点。技能和脚本只读取环境变量 `MING_TEA_IMAGE_API_KEY`，不会把密钥写入项目、恢复记录或日志。

推荐先生成一张 4K 的 2×2 四格角色动作图，再在本地检查并裁切；这样比为四个动作分别请求更省资源。每次请求都必须附带同一张角色设定参考图，并保持以下不变量：黑色中长发和橙色花饰、圆框眼镜、紫灰色眼睛、深蓝制服/领带/徽章、脸型和身体比例，以及参考图中出现的黑白企鹅伙伴。不要把生成的四格图直接当作最终图集，必须经过一致性检查、透明背景处理和 192×208 网格裁切。

技能位置：`~/.codex/skills/ming-tea-imagegen/`。批次脚本会在输出目录写入不含密钥的 `request-manifest.json` 和 `batch-image-resume.json`，便于 ZCode 或 Codex 恢复轮询；人物漂移的结果必须标记为失败并重新生成。
