# 更新日志

## [1.0.1] - 2026-09-02

### 🔒 安全修复
- **认证 Token 日志脱敏**：WebSocket / REST 客户端的异常日志在打印前把 Token 替换成 `***`，避免认证 Token 随连接 URL 或异常信息泄进日志（AstrBotAdapter 服务端 WebSocket 握手只认 URL query 里的 Token，无法改用 Header）。
- **自定义指令收窄为仅操作员可触发**：自定义指令与 `/mc cmd` 一致，发送者不在宿主 `[plugin].permission` 操作员列表时拒绝执行，堵住 `cmd_white_black_list` 放宽后目标会话内任意用户越权执行远程 MC 指令。

### ⚠️ 行为变化
- 自定义指令现在**仅操作员可触发**：需先在 MaiBot 的 `[plugin].permission` 配置操作员（格式 `平台:裸ID`，如 `qq:123456`），否则自定义指令会提示无权限。若无需自定义指令，可不配置。

> 本仓库首个 1.0.0 版本，暂无历史更新记录。
