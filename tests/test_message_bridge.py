"""services/message_bridge.py 消息格式化测试。"""

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
