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


def test_is_operator_match():
    from maibot_plugin_minecraft_adapter.handlers.commands import is_operator_match

    perms = ["qq:123456", "Telegram:98765"]
    # 平台名大小写不敏感、用户 ID 保留原样（与 MaiBot 操作员口径一致）
    assert is_operator_match(perms, "qq", "123456") is True
    assert is_operator_match(perms, "QQ", "123456") is True
    assert is_operator_match(perms, "telegram", "98765") is True
    # 未命中 / 空平台或用户 / 空列表
    assert is_operator_match(perms, "qq", "999999") is False
    assert is_operator_match(perms, "", "123456") is False
    assert is_operator_match(None, "qq", "123456") is False
    # 无冒号条目按默认平台 qq 解释
    assert is_operator_match(["789"], "qq", "789") is True
    assert is_operator_match(["789"], "telegram", "789") is False


def test_custom_command_requires_operator():
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from maibot_plugin_minecraft_adapter.handlers.commands import (
        CommandContext,
        CommandHandler,
    )

    server = SimpleNamespace(
        server_id="sv1",
        connected=True,
        server_info=SimpleNamespace(name="sv1", is_proxy=False),
        rest_client=SimpleNamespace(
            get_server_info=AsyncMock(
                return_value=(SimpleNamespace(is_proxy=False), "")
            ),
            execute_command=AsyncMock(return_value=(True, "ok", None)),
        ),
    )
    mapping = ["s <&x&><<>>say {x}"]

    class _Mgr:
        def get_server(self, sid):
            return server

    config = ServerConfig(
        server_id="sv1",
        cmd_enabled=True,
        cmd_white_black_list="none",  # 名单放宽，只能靠操作员鉴权兜底
        cmd_list=[],
        custom_cmd_list=mapping,
        target_sessions=["stream-1"],
    )

    async def _deny(platform, user_id):
        return False

    async def _allow(platform, user_id):
        return True

    ctx = CommandContext(stream_id="stream-1", platform="qq", user_id="123456")

    # 非操作员 → 拒绝
    denied = CommandHandler(
        server_manager=_Mgr(),
        renderer=_Mgr(),
        get_server_config=lambda _: config,
        is_operator=_deny,
    )
    denied.register_custom_commands("sv1", mapping)
    res = asyncio.run(denied.handle_custom_command(ctx, "s hello"))
    assert res is not None
    assert "仅操作员可触发" in res.text

    # 操作员 → 放行执行
    allowed = CommandHandler(
        server_manager=_Mgr(),
        renderer=_Mgr(),
        get_server_config=lambda _: config,
        is_operator=_allow,
    )
    allowed.register_custom_commands("sv1", mapping)
    server.rest_client.execute_command.reset_mock()
    res2 = asyncio.run(allowed.handle_custom_command(ctx, "s hello"))
    assert res2 is not None
    assert res2.text.startswith("✅")
    server.rest_client.execute_command.assert_awaited_once()
