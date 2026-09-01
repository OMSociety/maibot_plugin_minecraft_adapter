"""services/message_bridge.py 消息格式化与外部消息转发测试。"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from maibot_plugin_minecraft_adapter.core.models import (
    MCMessage,
    MCMessageSource,
    MessageType,
    ServerConfig,
    SourceType,
)
from maibot_plugin_minecraft_adapter.services.message_bridge import MessageBridge


def _bridge() -> MessageBridge:
    return MessageBridge(None, {}, None)


def _connected_bridge(prefix: str, target_sessions: list[str] | None = None) -> tuple[MessageBridge, MagicMock]:
    """构造一个带「已连接 MC 服务器」的 bridge，用于测试 handle_external_message。"""
    config = ServerConfig(
        server_id="sv1",
        auto_forward_prefix=prefix,
        target_sessions=target_sessions or ["sess-1"],
    )
    server = MagicMock()
    server.connected = True
    server.ws_client.send_incoming_message = AsyncMock(return_value=True)
    server_manager = MagicMock()
    server_manager.get_server.return_value = server
    bridge = MessageBridge(server_manager, {"sv1": config}, None)
    return bridge, server


def test_format_chat_message():
    b = _bridge()
    config = ServerConfig(forward_chat_format="<{player}> {message}")
    msg = MCMessage(
        type=MessageType.MESSAGE_FORWARD,
        source=MCMessageSource(type=SourceType.PLAYER, player_name="Steve"),
        payload={"content": "hello"},
    )
    assert b._format_mc_message(msg, config) == "<Steve> hello"


def test_format_player_join():
    b = _bridge()
    config = ServerConfig()
    msg = MCMessage(
        type=MessageType.PLAYER_JOIN,
        source=MCMessageSource(player_name="Steve", server_name="lobby"),
        payload={"onlineCount": 3, "maxPlayers": 20},
    )
    text = b._format_mc_message(msg, config)
    assert "Steve" in text
    assert "加入" in text
    assert "3/20" in text


def test_format_player_quit_kick():
    b = _bridge()
    config = ServerConfig()
    msg = MCMessage(
        type=MessageType.PLAYER_QUIT,
        source=MCMessageSource(player_name="Alex"),
        payload={"reason": "KICK"},
    )
    text = b._format_mc_message(msg, config)
    assert "被踢出" in text


def test_unsupported_type_returns_empty():
    b = _bridge()
    msg = MCMessage(type=MessageType.HEARTBEAT)
    assert b._format_mc_message(msg, ServerConfig()) == ""


# ============ handle_external_message：前缀与中止语义 ============


def test_empty_prefix_mirrors_all_and_does_not_consume():
    """空前缀=镜像全部：转发成功但返回 False（不中止，bot 仍可回复）。"""
    bridge, server = _connected_bridge(prefix="")
    forwarded = asyncio.run(
        bridge.handle_external_message("sess-1", "qq", "u1", "u1", "大家好")
    )
    assert forwarded is False
    server.ws_client.send_incoming_message.assert_called_once()
    # 镜像时发送完整原文
    kwargs = server.ws_client.send_incoming_message.call_args.kwargs
    assert kwargs["content"] == "大家好"


def test_prefix_hit_consumes_message():
    """非空前缀命中：转发（去掉前缀）+ 返回 True（中止）。"""
    bridge, server = _connected_bridge(prefix="*")
    forwarded = asyncio.run(
        bridge.handle_external_message("sess-1", "qq", "u1", "u1", "*hello")
    )
    assert forwarded is True
    kwargs = server.ws_client.send_incoming_message.call_args.kwargs
    assert kwargs["content"] == "hello"


def test_prefix_miss_does_not_forward():
    """非空前缀未命中：不转发、返回 False（正常对话）。"""
    bridge, server = _connected_bridge(prefix="*")
    forwarded = asyncio.run(
        bridge.handle_external_message("sess-1", "qq", "u1", "u1", "hello")
    )
    assert forwarded is False
    server.ws_client.send_incoming_message.assert_not_called()


def test_target_session_not_listed_is_ignored():
    """未在 target_sessions 里的会话不转发。"""
    bridge, server = _connected_bridge(prefix="")
    forwarded = asyncio.run(
        bridge.handle_external_message("other", "qq", "u1", "u1", "hello")
    )
    assert forwarded is False
    server.ws_client.send_incoming_message.assert_not_called()
