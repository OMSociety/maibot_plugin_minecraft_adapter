<div align="center">

<img src="https://raw.githubusercontent.com/OMSociety/maibot_plugin_minecraft_adapter/main/logo.png" width="120" alt="logo">

# ⛏️ Minecraft 聊天适配器

连接 **Minecraft 服务器** 与 **MaiBot**，实现游戏内 AI 聊天、跨平台消息互通和服务器远程管理。

![version](https://img.shields.io/badge/version-1.0.0-blue)
![license](https://img.shields.io/badge/license-AGPL--3.0-orange)
![python](https://img.shields.io/badge/python-3.11%2B-green)

[🐦 特性](#-核心特性) · [🚀 快速开始](#-快速开始) · [⚙️ 配置](#-配置说明) · [⌨️ 命令](#-命令)

</div>

---

> 📌 本插件由 [railgun19457/astrbot_plugin_minecraft_adapter](https://github.com/railgun19457/astrbot_plugin_minecraft_adapter) 迁移而来，将 AstrBot 版改为 MaiBot 插件。
>
> 孪生项目（MC 服务端插件）：
> - [AstrBot Adapter](https://github.com/railgun19457/AstrBotAdapter)（Bukkit/Paper/Folia/Velocity）
> - [AstrBotAdapter_Forge](https://github.com/OMSociety/AstrBotAdapter_Forge)

## 🐦 核心特性

| 特性 | 说明 |
|------|------|
| 🎮 AI 聊天 | 游戏内玩家直接和 MaiBot 聊天，回复语气继承 MaiBot 全局人格 |
| 🔁 消息互通 | MC 服务器 ↔ 外部群/私聊双向转发，支持自定义格式与进出提示 |
| 🖥️ 服务器管理 | 状态查询 / 在线玩家 / 玩家详情 / 远程指令，信息可渲染为图片 |
| 👥 用户绑定 | 外部账号绑定 MC 游戏 ID，自定义指令自动带上 `{sender}` |
| 🛡️ 指令安全 | 远程指令支持白名单/黑名单，`/mc cmd` 为操作员级命令 |

## 🚀 快速开始

1. 在 MC 服务器安装 **AstrBotAdapter** 服务端插件并配置端口与 token；
2. 在 MaiBot 插件中心安装本插件，填入服务器连接信息（见下方配置）；
3. 用 `/mc sid` 查看目标会话的 `stream_id`，填入 `message.target_sessions`；
4. 在群里发 `/mc status` 验证连通。

## ⚙️ 配置说明

插件支持添加多个服务器。单个服务器的完整配置结构如下（WebUI 里 `mc_servers` 列表每项对应一个服务器）：

```json
{
  "enabled": true,
  "server": {
    "server_id": "my_server",
    "host": "localhost",
    "port": 8765,
    "token": ""
  },
  "enable_ai_chat": true,
  "text2image": true,
  "message": {
    "forward_chat_to_astrbot": true,
    "forward_chat_format": "<{player}> {message}",
    "forward_join_leave_to_astrbot": false,
    "target_sessions": [],
    "auto_forward_prefix": "*",
    "mark_option": "text"
  },
  "cmd": {
    "enabled": true,
    "cmd_white_black_list": "white",
    "cmd_list": ["say", "list", "weather", "time"],
    "bind_enable": true,
    "custom_cmd_list": [
      "tp <&X&> <&y&> <&z&><<>>tp {sender} <&X&> <&y&> <&z&>"
    ]
  }
}
```

### 关键字段

- **`server`**：连接 AstrBotAdapter 的地址、端口、认证 token。
- **`message.target_sessions`**：消息互通的目标会话 `stream_id` 列表。⚠️ **MaiBot 用 `stream_id`（不是 AstrBot 的 UMO）**，用 `/mc sid` 命令查看。**这个配置标志 MC 服务器与群聊的绑定关系**。
- **`message.auto_forward_prefix`**：外部消息以此前缀开头才转发到 MC（默认 `*`），留空转发全部。
- **`message.forward_chat_format`**：转发格式，占位符 `{player}`（玩家名）`{message}`（内容）。
- **`cmd.cmd_white_black_list`**：`white`（仅名单内可执行）/ `black`（名单内禁止）/ `none`（不启用）。
- **`cmd.custom_cmd_list`**：自定义指令，用 `<<>>` 分隔「触发词」与「实际指令」，`{sender}` 替换为用户绑定的游戏 ID，`<&参数&>` 为自定义参数占位符。

## ⌨️ 命令

| 命令 | 说明 |
|------|------|
| `/mc help` | 显示帮助与自定义指令列表 |
| `/mc sid` | 查看可用的会话 `stream_id`（填配置用） |
| `/mc status` | 查看服务器状态 |
| `/mc list` | 查看在线玩家列表 |
| `/mc player <玩家ID>` | 查看玩家详细信息 |
| `/mc cmd <指令>` | 远程执行服务器指令（**操作员级**） |
| `/mc bind <游戏ID>` | 绑定你的游戏 ID |
| `/mc unbind` | 解除绑定 |

当前会话关联多个服务器时，需要区分目标的指令会显示服务器列表，发送编号选择目标。

### 关于 `/mc sid`

外部消息互通需要在配置里填 `stream_id`（MaiBot 给每个群/私聊分配的唯一会话 ID，重启不变）。在任何和 bot 说过话的会话里发 `/mc sid`，bot 会直接列出带可读名称的会话列表：

```
📋 可用会话（把 stream_id 填进插件配置的 target_sessions）:
  [山海学社] 平台=qq_official (群)
    stream_id = 64C82D3A253B54A1AE510FFFA4D1DFB4
```

把对应的 `stream_id` 复制进配置即可，无需翻日志。

## ⚠️ 与 AstrBot 版的行为差异

- **目标会话**：由 AstrBot 的 UMO（`aiocqhttp:GroupMessage:xxx`）改为 MaiBot 的 `stream_id`。
- **AI 聊天**：AstrBot 走平台适配器完整管线；MaiBot 改为直连 `ctx.llm.generate`，每个玩家保留一小段在内存的对话历史（Runner 重启后清空）。
- **转发提醒**：AstrBot 的「贴表情」依赖 napcat 专属 API，MaiBot SDK 无对应能力，改为 `text`/`none` 两种。
- **许可证**：上游 LICENSE 为 **AGPL-3.0**（原 README 误标 MIT），本插件继承 AGPL-3.0。

## 📄 许可证

[AGPL-3.0](LICENSE)。本项目由 [railgun19457/astrbot_plugin_minecraft_adapter](https://github.com/railgun19457/astrbot_plugin_minecraft_adapter) 迁移而来，继承其 AGPL-3.0 许可证。

## 👤 作者

[OMSociety](https://github.com/OMSociety)
