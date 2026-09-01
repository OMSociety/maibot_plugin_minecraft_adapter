"""MC 与其他平台之间转发消息的消息桥接服务。

从 AstrBot 版 services/message_bridge.py 迁移：
- 出站（MC → 外部）由 context.send_message(umo) 改为注入的 send_text 回调（ctx.send.text）。
- 入站（外部 → MC）由 @filter 事件监听改为由 plugin 的 chat.receive.after_process Hook 调用。
- 移除 napcat 专属的表情反应（set_msg_emoji_like 在 MaiBot SDK 无对应能力）。
"""

import logging
import time
from typing import TYPE_CHECKING

from ..core.models import MCMessage, MessageType, ServerConfig

if TYPE_CHECKING:
    from ..core.server_manager import ServerManager

logger = logging.getLogger(__name__)


class MessageBridge:
    """在 MC 服务器和 MaiBot 会话之间转发消息的服务"""

    def __init__(
        self,
        server_manager: "ServerManager",
        server_configs: dict[str, ServerConfig],
        send_text,
    ):
        """
        Args:
            server_manager: 服务器连接管理器
            server_configs: server_id -> ServerConfig 映射
            send_text: async (text, stream_id) -> bool，发送文本到外部会话
        """
        self.server_manager = server_manager
        self._server_configs = server_configs
        self._send_text = send_text
        # 从会话 stream_id 到希望接收消息的服务器配置的映射
        self._session_to_servers: dict[str, list[tuple[str, ServerConfig]]] = {}
        # Track recently forwarded messages to suppress echo
        # Key: (server_id, content), Value: timestamp
        self._recently_forwarded: dict[tuple[str, str], float] = {}
        # Echo suppression window in seconds
        self._echo_suppress_window = 5.0

    def register_server(self, config: ServerConfig):
        """注册用于消息转发的服务器"""
        self._server_configs[config.server_id] = config

        # 为目标会话构建反向映射
        for session in config.target_sessions:
            if session not in self._session_to_servers:
                self._session_to_servers[session] = []
            self._session_to_servers[session].append((config.server_id, config))

    def unregister_server(self, server_id: str):
        """从消息转发中取消注册服务器"""
        config = self._server_configs.pop(server_id, None)
        if config:
            # 从反向映射中移除
            for session in config.target_sessions:
                if session in self._session_to_servers:
                    self._session_to_servers[session] = [
                        (sid, cfg)
                        for sid, cfg in self._session_to_servers[session]
                        if sid != server_id
                    ]

    async def handle_mc_message(self, server_id: str, msg: MCMessage) -> bool:
        """处理来自 MC 服务器的消息并转发到目标会话

        如果消息被转发则返回 True。
        """
        config = self._server_configs.get(server_id)
        if not config:
            return False

        # 检查是否已启用转发
        if msg.type == MessageType.MESSAGE_FORWARD:
            if not config.forward_chat_to_astrbot:
                return False
            # Suppress echo: if this message was recently forwarded FROM external
            content = msg.payload.get("content", "")
            echo_key = (server_id, content)
            now = time.time()
            if echo_key in self._recently_forwarded:
                if (
                    now - self._recently_forwarded[echo_key]
                    < self._echo_suppress_window
                ):
                    del self._recently_forwarded[echo_key]
                    return False
                del self._recently_forwarded[echo_key]
        elif msg.type in (MessageType.PLAYER_JOIN, MessageType.PLAYER_QUIT):
            if not config.forward_join_leave_to_astrbot:
                return False
        else:
            return False

        # 获取目标会话
        targets = config.target_sessions
        if not targets:
            return False

        # 格式化消息内容
        content = self._format_mc_message(msg, config)
        if not content:
            return False

        # 发送到每个目标会话
        for target_stream_id in targets:
            await self._send_to_stream(target_stream_id, content)

        return True

    def _format_mc_message(self, msg: MCMessage, config: ServerConfig) -> str:
        """格式化 MC 消息以转发到外部平台。

        支持 MESSAGE_FORWARD、PLAYER_JOIN 和 PLAYER_QUIT 消息类型。
        """
        if msg.type == MessageType.MESSAGE_FORWARD:
            player_name = msg.source.player_name if msg.source else "未知"
            content = msg.payload.get("content", "")
            return config.forward_chat_format.format(
                player=player_name, message=content
            )

        if msg.type in (MessageType.PLAYER_JOIN, MessageType.PLAYER_QUIT):
            player_name = msg.source.player_name if msg.source else "未知"
            server_name = msg.source.server_name if msg.source else ""
            online = msg.payload.get("onlineCount", 0)
            max_players = msg.payload.get("maxPlayers", 0)
            count_part = f" ({online}/{max_players})" if max_players else ""
            server_part = f" {server_name}" if server_name else "服务器"

            if msg.type == MessageType.PLAYER_JOIN:
                return f"🟢 {player_name} 加入了{server_part}{count_part}"

            reason = msg.payload.get("reason", "QUIT")
            reason_text = {
                "QUIT": "离开",
                "KICK": "被踢出",
                "TIMEOUT": "超时断开",
            }.get(reason, "离开")
            return f"🔴 {player_name} {reason_text}了{server_part}{count_part}"

        return ""

    async def _send_to_stream(self, stream_id: str, content: str):
        """通过注入的回调发送文本到目标会话。"""
        try:
            sent = await self._send_text(content, stream_id)
            if not sent:
                logger.warning(f"[MessageBridge] 发送消息失败: {stream_id}")
        except Exception as e:
            logger.error(f"[MessageBridge] 发送消息失败: {e}")

    async def handle_external_message(
        self,
        stream_id: str,
        platform: str,
        user_id: str,
        user_name: str,
        content: str,
    ) -> bool:
        """处理来自外部平台的消息并在需要时转发到 MC

        由 plugin 的 chat.receive.after_process Hook 调用。
        如果消息被转发则返回 True（用于决定是否中止后续消息处理）。
        """
        any_forwarded = False
        for server_id, config in self._server_configs.items():
            # 检查此会话是否在目标会话列表中
            if not config.target_sessions or stream_id not in config.target_sessions:
                continue

            # 前缀为空时转发全部消息，否则检查前缀
            if config.auto_forward_prefix:
                if not content.startswith(config.auto_forward_prefix):
                    continue
                # 移除前缀
                forwarded = content[len(config.auto_forward_prefix) :].strip()
            else:
                forwarded = content.strip()

            if not forwarded:
                continue

            # 发送到 MC 服务器
            server = self.server_manager.get_server(server_id)
            if server and server.connected:
                success = await server.ws_client.send_incoming_message(
                    platform=platform,
                    user_id=user_id,
                    user_name=user_name,
                    content=forwarded,
                )

                if success:
                    # Track this message to suppress echo
                    echo_key = (server_id, forwarded)
                    self._recently_forwarded[echo_key] = time.time()
                    # Clean up old entries
                    self._cleanup_recently_forwarded()
                    # Send feedback based on mark_option (only once)
                    if not any_forwarded:
                        await self._send_forward_feedback(stream_id, config)
                    any_forwarded = True

        return any_forwarded

    async def _send_forward_feedback(self, stream_id: str, config: ServerConfig):
        """在消息转发成功后发送反馈

        Behavior by mark_option:
        - "none": do nothing
        - "text": send text confirmation "✓ 消息已转发"
        - "emoji": MaiBot SDK 无表情反应能力，降级为不提醒
        """
        mark_option = config.mark_option

        if mark_option in ("none", "emoji"):
            return

        if mark_option == "text":
            try:
                await self._send_text("✓ 消息已转发", stream_id)
            except Exception:
                pass

    def _cleanup_recently_forwarded(self):
        """Clean up expired entries in the recently forwarded tracker"""
        now = time.time()
        expired = [
            k
            for k, t in self._recently_forwarded.items()
            if now - t > self._echo_suppress_window
        ]
        for k in expired:
            del self._recently_forwarded[k]

    def get_servers_for_session(self, stream_id: str) -> list[str]:
        """获取目标会话包含该 stream_id 的服务器 ID 列表"""
        result = []
        for server_id, config in self._server_configs.items():
            if config.target_sessions and stream_id in config.target_sessions:
                result.append(server_id)
        return result
