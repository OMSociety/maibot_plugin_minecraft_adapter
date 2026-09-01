<div align="center">

<img src="https://raw.githubusercontent.com/OMSociety/maibot_plugin_minecraft_adapter/main/logo.png" width="120" alt="Minecraft 聊天适配器 Logo" />

# ⛏️ Minecraft 聊天适配器

**连接 Minecraft 服务器与 MaiBot** —— 游戏内 AI 聊天 · 跨平台消息互通 · 服务器远程管理

[![Version](https://img.shields.io/badge/version-1.0.0-blue.svg)](https://github.com/OMSociety/maibot_plugin_minecraft_adapter)
[![MaiBot](https://img.shields.io/badge/MaiBot-%E2%89%A51.0-green.svg)](https://github.com/Mai-with-u/MaiBot)
[![License](https://img.shields.io/badge/license-AGPL--3.0-orange.svg)](LICENSE)
[![Stars](https://img.shields.io/github/stars/OMSociety/maibot_plugin_minecraft_adapter)](https://github.com/OMSociety/maibot_plugin_minecraft_adapter/stargazers)
[![Issues](https://img.shields.io/github/issues/OMSociety/maibot_plugin_minecraft_adapter)](https://github.com/OMSociety/maibot_plugin_minecraft_adapter/issues)

[✨ 核心特性](#-核心特性) • [📖 功能概览](#-功能概览) • [🚀 快速开始](#-快速开始) • [⚙️ 配置项说明](#️-配置项说明) • [⌨️ 命令](#️-命令) • [⚠️ 常见问题](#️-常见问题) • [📝 更新日志](CHANGELOG.md)

</div>

> 🎨 本项目由 AstrBot 插件 [railgun19457/astrbot_plugin_minecraft_adapter](https://github.com/railgun19457/astrbot_plugin_minecraft_adapter) 迁移而来，改为 MaiBot 插件。
>
> 孪生项目（MC 服务端插件）：[AstrBot Adapter](https://github.com/railgun19457/AstrBotAdapter)（Bukkit/Paper/Folia/Velocity）· [AstrBotAdapter_Forge](https://github.com/OMSociety/AstrBotAdapter_Forge)

---

## ✨ 核心特性

| 特性 | 说明 |
|------|------|
| 🎮 **AI 聊天** | 游戏内玩家直接和 bot 对话，回复语气继承 MaiBot 全局人格 |
| 🔁 **消息互通** | MC 服务器 ↔ 外部群/私聊双向转发，支持自定义格式与进出提示 |
| 🖥️ **服务器管理** | 状态查询 / 在线玩家 / 玩家详情 / 远程指令，信息可渲染为图片 |
| 👥 **用户绑定** | 外部账号绑定 MC 游戏 ID，自定义指令自动带上 `{sender}` |
| 🛡️ **指令安全** | 远程指令支持白名单/黑名单，`/mc cmd` 为操作员级命令 |

---

## 📖 功能概览

### 🎮 游戏内 AI 聊天
玩家在 MC 游戏里说话，`CHAT_REQUEST` 经 WebSocket 送达插件，插件直连 `ctx.llm.generate` 生成回复并回传游戏内。自动读取 MaiBot 全局人格，语气与日常聊天一致。

### 🔁 跨平台消息互通
- **MC → 外部**：MC 玩家聊天 / 进出提示按 `forward_chat_format` 格式化后转发到目标会话。
- **外部 → MC**：目标会话里带 `auto_forward_prefix` 前缀的消息转发到 MC。

### 🖥️ 服务器远程管理
`/mc status` / `/mc list` / `/mc player` 查询服务器状态、在线玩家、玩家详情，默认渲染为精美图片卡片；`/mc cmd` 远程执行指令（白名单 + 操作员级双重校验）。

### 👥 用户绑定
外部账号绑定 MC 游戏 ID 后，自定义指令里的 `{sender}` 自动替换为绑定的游戏 ID。

### 🛡️ 指令安全
远程指令默认白名单（`["say","list","weather","time"]`），`/mc cmd` 标记为操作员级命令，只有配置了 operator 权限的用户能执行。

---

## 🚀 快速开始

### 第一步：准备 MC 服务端插件

1. 在 MC 服务器上安装 **AstrBotAdapter**（或 AstrBotAdapter_Forge）服务端插件
2. 在 MC 端配置服务器端口（默认 8765）与认证 Token

### 第二步：在 MaiBot 安装

**方式一：插件市场**
- MaiBot WebUI → 插件市场 → 搜索 `minecraft_adapter`

**方式二：手动安装**
- 克隆仓库到 MaiBot 的 `plugins/` 目录：

```bash
git clone https://github.com/OMSociety/maibot_plugin_minecraft_adapter.git plugins/maibot_plugin_minecraft_adapter
```

> 💡 插件依赖（aiohttp / Pillow）在 `_manifest.json` 中声明，MaiBot 启动时自动安装。

### 第三步：配置

1. 在插件配置里添加一个 MC 服务器，填 `server.server_id` / `server.host` / `server.port` / `server.token`
2. 用 `/mc sid` 查看目标会话的 `stream_id`，填入 `message.target_sessions`
3. 按需调整消息转发 / 远程指令 / 绑定相关设置

### 第四步：使用

- 在绑定会话发 `/mc status` 验证连通
- 发 `/mc cmd say 你好` 远程执行指令（需操作员）
- 在 MC 游戏内直接和 bot 聊天

---

## ⚙️ 配置项说明

| 分组 | 配置项 | 类型 | 默认值 | 说明 |
|:-----|:-------|:-----|:-------|:-----|
| 服务器连接 | `server_id` | string | `"my_server"` | 服务器唯一标识 |
| 服务器连接 | `server.host` | string | `"localhost"` | MC 服务端插件地址 |
| 服务器连接 | `server.port` | int | `8765` | MC 服务端端口 |
| 服务器连接 | `server.token` | string | `""` | 认证 Token |
| 消息转发 | `enable_ai_chat` | bool | `true` | 游戏内 AI 聊天开关 |
| 消息转发 | `text2image` | bool | `true` | 服务器信息渲染为图片 |
| 消息转发 | `forward_chat_to_astrbot` | bool | `true` | 转发 MC 聊天到目标会话 |
| 消息转发 | `forward_chat_format` | string | `"<{player}> {message}"` | 转发格式 |
| 消息转发 | `forward_join_leave_to_astrbot` | bool | `false` | 转发玩家进出提示 |
| 消息转发 | `target_sessions` | list | `[]` | 目标会话 stream_id 列表（`/mc sid` 获取） |
| 消息转发 | `auto_forward_prefix` | string | `"*"` | 转发前缀（留空转发全部） |
| 消息转发 | `mark_option` | string | `"text"` | 转发成功提醒（text/none） |
| 远程指令 | `cmd.enabled` | bool | `true` | 远程指令总开关 |
| 远程指令 | `cmd.cmd_white_black_list` | string | `"white"` | white/black/none |
| 远程指令 | `cmd.cmd_list` | list | `["say","list","weather","time"]` | 指令名单 |
| 远程指令 | `cmd.bind_enable` | bool | `true` | 用户绑定开关 |
| 远程指令 | `cmd.custom_cmd_list` | list | `["tp <&X&> <&y&> <&z&><<>>tp {sender} <&X&> <&y&> <&z&>"]` | 自定义指令映射 |

> 💡 **`/mc cmd` 与 `/mc sid` 为操作员级命令**：需在 MaiBot 的 `[plugin].permission` 配置操作员列表（如 `qq:123456789`）后才能执行。
>
> 💡 **目标会话用 stream_id**：MaiBot 给每个群/私聊分配唯一会话 ID（重启不变），用 `/mc sid` 命令可查，不是 AstrBot 的 UMO。

**快速配置模板（单个服务器）：**

```json
{
  "enabled": true,
  "server": { "server_id": "my_server", "host": "localhost", "port": 8765, "token": "" },
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

---

## ⌨️ 命令

| 命令 | 说明 | 权限 |
|:-----|:-----|:-----|
| `/mc help` | 显示帮助与自定义指令列表 | 公开 |
| `/mc sid` | 查看可用的会话 stream_id（填配置用） | **操作员** |
| `/mc status` | 查看服务器状态 | 公开 |
| `/mc list` | 查看在线玩家列表 | 公开 |
| `/mc player <玩家ID>` | 查看玩家详细信息 | 公开 |
| `/mc cmd <指令>` | 远程执行服务器指令 | **操作员** |
| `/mc bind <游戏ID>` | 绑定你的游戏 ID | 公开 |
| `/mc unbind` | 解除绑定 | 公开 |

> 💡 **多服务器选择**：当前会话关联多个服务器时，需要区分目标的指令会显示服务器列表，发送编号选择目标。

---

## ⚠️ 常见问题

**Q：目标会话怎么填？**
A：在插件配置 `message.target_sessions` 里填 **stream_id**（不是 AstrBot 的 UMO）。在任意和 bot 说过话的会话发 `/mc sid`，bot 会列出带群名/私聊名的会话列表，复制对应 `stream_id` 填入即可。

**Q：消息没互通？**
A：检查三点：① `target_sessions` 是否放了正确的 stream_id；② 外部消息是否带 `auto_forward_prefix` 前缀（默认 `*`）；③ 是否启用 `forward_chat_to_astrbot`。

**Q：`/mc cmd` 提示没权限？**
A：`/mc cmd` 是操作员级命令。需在 MaiBot 的 `[plugin].permission` 配置操作员列表，且该命令可能被 `cmd.cmd_white_black_list` 白名单拦截。

**Q：换平台适配器（如 QQ 官方）也能用吗？**
A：能。目标会话用 stream_id 而非平台专用群号，任何适配器（napcat / QQ 官方 / telegram）都通用。

**Q：数据存在哪？**
A：用户绑定在 `data/plugins/omsociety.minecraft-adapter/mc_bindings.json`；渲染缓存/字体在插件 `runtime_dir`。

---

## ⭐ 支持本项目

如果这个插件对你有帮助，欢迎点亮 Star ⭐，有问题和建议请提交 [Issue](https://github.com/OMSociety/maibot_plugin_minecraft_adapter/issues) 或 [Pull Request](https://github.com/OMSociety/maibot_plugin_minecraft_adapter/pulls)。

## 🙏 致谢

- [MaiBot](https://github.com/Mai-with-u/MaiBot) 开源聊天机器人框架
- [railgun19457/astrbot_plugin_minecraft_adapter](https://github.com/railgun19457/astrbot_plugin_minecraft_adapter) 上游 AstrBot 插件
- [AstrBotAdapter](https://github.com/railgun19457/AstrBotAdapter) MC 服务端插件

---

## 📜 许可证

本项目采用 **AGPL-3.0** 开源协议。

---

## 👤 作者

[@OMSociety](https://github.com/OMSociety)
