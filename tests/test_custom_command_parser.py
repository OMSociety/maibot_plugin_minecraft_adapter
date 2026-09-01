"""handlers/commands.py 自定义指令解析器与黑白名单测试。"""

from maibot_plugin_minecraft_adapter.core.models import ServerConfig
from maibot_plugin_minecraft_adapter.handlers.commands import (
    CustomCommandParser,
)


def test_parse_and_match_tp():
    parser = CustomCommandParser(
        ["tp <&X&> <&y&> <&z&><<>>tp {sender} <&X&> <&y&> <&z&>"]
    )
    result = parser.match("tp 114 514 1919", sender_mc_name="Misaka")
    assert result is not None
    command, params = result
    assert command == "tp Misaka 114 514 1919"
    assert params["sender"] == "Misaka"
    assert params["X"] == "114"


def test_match_without_sender():
    parser = CustomCommandParser(
        ["head <&player&><<>>give {sender} head '<&player&>' 1"]
    )
    result = parser.match("head Steve", sender_mc_name=None)
    assert result is not None
    command, _ = result
    assert command == "give  head 'Steve' 1"


def test_no_match_when_trigger_differs():
    parser = CustomCommandParser(["tp <&X&><<>>tp {sender} <&X&>"])
    assert parser.match("head Steve") is None


def test_missing_usage_hint():
    parser = CustomCommandParser(
        ["tp <&X&> <&y&> <&z&><<>>tp {sender} <&X&> <&y&> <&z&>"]
    )
    assert parser.get_missing_usage("tp 1") == "tp <&X&> <&y&> <&z&>"


def test_invalid_mapping_ignored():
    parser = CustomCommandParser(["no separator here", "ok <&a&><<>>say {a}"])
    assert len(parser.mappings) == 1


def test_command_whitelist_blacklist():
    from maibot_plugin_minecraft_adapter.handlers.commands import CommandHandler

    class _Mgr:
        pass

    handler = CommandHandler(
        server_manager=_Mgr(),
        binding_service=_Mgr(),
        renderer=_Mgr(),
        get_server_config=lambda _: None,
    )

    white = ServerConfig(cmd_white_black_list="white", cmd_list=["say", "list"])
    assert handler._check_command_allowed("say hello", white) is True
    assert handler._check_command_allowed("gamemode creative", white) is False

    black = ServerConfig(cmd_white_black_list="black", cmd_list=["stop"])
    assert handler._check_command_allowed("say hello", black) is True
    assert handler._check_command_allowed("stop", black) is False

    none = ServerConfig(cmd_white_black_list="none")
    assert handler._check_command_allowed("anything", none) is True
