# Changelog

本项目所有重要更改都会记录在此文件。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)；
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [1.1.0] - 2026-09-11

### ✨ 新增
- 群友绑定：`/mc bind <游戏ID>`（Java 版）、`/mc geyserbind <游戏ID>`（基岩版）、`/mc mybind`（查看自己的绑定）、`/mc unbind`（解绑）。绑定后由服务器模组自动写入白名单。
- 服务端能力协商：通过免认证的 `GET /api/v1/health` 读取 `features` 并据此判断能力，而不是依赖模组版本号。新增能力位 `binding.v1`。
- 新增配置项 `bind_enabled`、`bind_geyser_enabled`、`bind_unbind_enabled`（均默认开启）。

### ⚙️ 变更
- 兼容两种服务端模组：连接新版 AstrBotAdapter_Forge_Forward（v1.1.0+）时绑定功能可用；连接原版 AstrBotAdapter_Forge 时自动识别为不支持，绑定指令给出升级提示，其余功能（状态查询、玩家列表、远程指令、AI 聊天、消息互通）完全不受影响。
- `/mc help` 的帮助文本按能力动态生成：服务端不支持绑定时不列出绑定指令。
- 用户可见文案中的「本会话的 Session ID」统一改为「会话 ID」。

### 🐛 修复
- 修复 WebSocket `CONNECTION_ACK` 解析错误：`sessionId` 与 `serverInfo` 实际位于 `payload` 下，此前读 `data` 导致服务器信息恒为空、连接日志不显示服务器名。
- 服务端错误码不再被丢弃：绑定相关的 4003/4004/4005/4006/5004 现在会映射为对应的用户提示，而不是直接回显服务端原文。
