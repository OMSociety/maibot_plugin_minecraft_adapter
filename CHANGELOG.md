# Changelog

本项目所有重要更改都会记录在此文件。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)；
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [1.3.2] - 2026-09-25

### 修复 (Fixed)

- **多服务器会话下的选号链路修好**：`/mc bind`、`/mc geyserbind`、`/mc unbind`、`/mc geyserunbind`、`/mc mybind` 回复编号后不再落到无效提示或重复提示。待选动作现在携带完整的调用意图与参数，编号选中的服务器会被直接用于执行，且编号列表与可执行集合一致（按绑定开关、能力与在线状态过滤）；只剩一台可用服务器时直接执行。
- **自定义指令模板的 `{sender}` 现在会替换成发送者在对应服务器上的游戏 ID**（未绑定或查询失败时仍为空串）。仅在模板真正引用它时才查询，结果按服务器与用户缓存 30 秒，缓存数量有上限。
- **WebSocket 握手未完成时关闭已建立的连接**：此前重连循环会在每次失败握手后泄漏一条连接（含超时、异常与取消路径）。

---

## [1.3.1] - 2026-09-18

### 修复 (Fixed)
- 游戏内 AI 对话显式使用 `replyer` 模型任务槽。此前未指定任务槽，由 SDK 兜底落到 `utils` 槽，与麦麦日常回复所用模型不一致。

---

## [1.3.0] - 2026-09-14

### 新增 (Added)
- **配置界面多语言（en-US / ja-JP）**：WebUI 配置页的字段标题、提示与占位文本、分区标题与分区描述按界面语言自动切换（宿主界面语言支持中文/英文/日文/韩文，无对应译文时回退中文），MC 服务器列表项的字段文案同样覆盖；manifest 声明 `supported_locales`。
- 新增配置界面 i18n 回归测试：离线生成配置 Schema，校验各语言覆盖率、数值一致与技术标识符保全。

---

## [1.2.1] - 2026-09-12

### 修复 (Fixed)
- 区分「服务端模组未开启绑定」与「服务端模组不支持绑定」这两种情况。此前服务端返回 `4003`（`binding.enabled=false`，即模组支持绑定但没开启）时会被提示成「该服务器模组不支持绑定功能，请升级到 AstrBotAdapter_NeoForge」——实际使用中会把用户引去升级一个并不缺功能的模组。现在会直接给出开启步骤：修改服务端 `config/astrbotadapter/config.yml` 的 `binding.enabled` 为 `true` 后执行 `/astrbot reload`。

---

## [1.2.0] - 2026-09-11

### 新增 (Added)
- 一个账号可**同时**持有 Java 版与基岩版两条绑定：两条白名单条目并存、互不覆盖。此前第二次绑定会撤掉前一条。
- `/mc mybind` 现在一条回复里同时列出两条绑定（Java 版与基岩版各自的游戏 ID、是否已入白名单）。
- 新增 `/mc geyserunbind`：只解除基岩版绑定，保留 Java 版绑定。
- `/mc unbind` 改为清空该账号的全部绑定（Java 与基岩一起解除）。

### 变更 (Changed)
- 服务端绑定类型（`kind`）随绑定一起透传；`/mc geyserbind` 与 `/mc bind` 只影响各自那一类。

---

## [1.1.0] - 2026-09-11

### 新增 (Added)
- 群友绑定：`/mc bind <游戏ID>`（Java 版）、`/mc geyserbind <游戏ID>`（基岩版）、`/mc mybind`（查看自己的绑定）、`/mc unbind`（解绑）。绑定后由服务器模组自动写入白名单。
- 服务端能力协商：通过免认证的 `GET /api/v1/health` 读取 `features` 并据此判断能力，而不是依赖模组版本号。新增能力位 `binding.v1`。
- 新增配置项 `bind_enabled`、`bind_geyser_enabled`、`bind_unbind_enabled`（均默认开启）。

### 变更 (Changed)
- 兼容两种服务端模组：连接新版 AstrBotAdapter_NeoForge（v1.1.0+）时绑定功能可用；连接原版 AstrBotAdapter_Forge 时自动识别为不支持，绑定指令给出升级提示，其余功能（状态查询、玩家列表、远程指令、AI 聊天、消息互通）完全不受影响。
- `/mc help` 的帮助文本按能力动态生成：服务端不支持绑定时不列出绑定指令。
- 用户可见文案中的「本会话的 Session ID」统一改为「会话 ID」。

### 修复 (Fixed)
- 修复 WebSocket `CONNECTION_ACK` 解析错误：`sessionId` 与 `serverInfo` 实际位于 `payload` 下，此前读 `data` 导致服务器信息恒为空、连接日志不显示服务器名。
- 服务端错误码不再被丢弃：绑定相关的 4003/4004/4005/4006/5004 现在会映射为对应的用户提示，而不是直接回显服务端原文。
