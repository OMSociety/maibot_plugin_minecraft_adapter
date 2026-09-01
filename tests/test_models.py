"""core/models.py 纯逻辑测试。"""

from maibot_plugin_minecraft_adapter.core.models import (
    MCMessage,
    MessageType,
    PlayerDetail,
    ServerConfig,
    ServerInfo,
    safe_enum,
)


def test_safe_enum_valid_and_invalid():
    assert (
        safe_enum(MessageType, "CHAT_REQUEST", MessageType.ERROR)
        is MessageType.CHAT_REQUEST
    )
    assert safe_enum(MessageType, "NOT_EXIST", MessageType.ERROR) is MessageType.ERROR


def test_message_type_values():
    assert MessageType.CHAT_REQUEST.value == "CHAT_REQUEST"
    assert MessageType.MESSAGE_FORWARD.value == "MESSAGE_FORWARD"


def test_server_config_from_full_dict():
    data = {
        "enabled": True,
        "server": {
            "server_id": "s1",
            "host": "127.0.0.1",
            "port": 8765,
            "token": "tok",
        },
        "enable_ai_chat": True,
        "text2image": False,
        "message": {
            "forward_chat_to_astrbot": True,
            "forward_chat_format": "<{player}> {message}",
            "forward_join_leave_to_astrbot": True,
            "target_sessions": ["a", "b"],
            "auto_forward_prefix": "*",
            "mark_option": "text",
        },
        "cmd": {
            "enabled": True,
            "cmd_white_black_list": "white",
            "cmd_list": ["say", "list"],
            "bind_enable": True,
            "custom_cmd_list": ["tp <&X&><<>>tp {sender} <&X&>"],
        },
    }
    c = ServerConfig.from_dict(data)
    assert c.server_id == "s1"
    assert c.host == "127.0.0.1"
    assert c.port == 8765
    assert c.token == "tok"
    assert c.text2image is False
    assert c.target_sessions == ["a", "b"]
    assert c.mark_option == "text"
    assert c.cmd_white_black_list == "white"
    assert c.cmd_list == ["say", "list"]


def test_server_config_defaults():
    c = ServerConfig.from_dict({})
    assert c.server_id == ""
    assert c.host == "localhost"
    assert c.port == 8765
    assert c.target_sessions == []
    assert c.mark_option == "emoji"


def test_server_info_proxy_backends():
    data = {
        "name": "proxy",
        "platform": "velocity",
        "minecraftVersion": "1.20",
        "backends": [
            {
                "name": "lobby",
                "platform": "paper",
                "onlinePlayers": 5,
                "maxPlayers": 100,
            },
        ],
        "aggregate": {"totalOnlinePlayers": 5, "totalMaxPlayers": 100},
    }
    info = ServerInfo.from_dict(data)
    assert info.is_proxy is True
    assert len(info.backends) == 1
    assert info.backends[0].name == "lobby"
    assert info.backends[0].online_count == 5


def test_mcmessage_roundtrip():
    msg = MCMessage.from_dict(
        {
            "type": "CHAT_REQUEST",
            "id": "msg-1",
            "source": {
                "type": "PLAYER",
                "server": {"name": "s1"},
                "player": {"uuid": "u1", "name": "Steve", "displayName": "Steve"},
            },
            "payload": {"content": "hello", "chatMode": "GROUP"},
        }
    )
    assert msg.type is MessageType.CHAT_REQUEST
    assert msg.source.player_name == "Steve"
    assert msg.payload["content"] == "hello"

    d = msg.to_dict()
    assert d["type"] == "CHAT_REQUEST"
    assert d["id"] == "msg-1"


def test_player_detail_from_dict():
    p = PlayerDetail.from_dict(
        {
            "uuid": "u1",
            "name": "Steve",
            "health": 20.0,
            "maxHealth": 20.0,
            "foodLevel": 18,
            "level": 3,
            "exp": 0.5,
        }
    )
    assert p.name == "Steve"
    assert p.health == 20.0
    assert p.food_level == 18
    assert p.level == 3
