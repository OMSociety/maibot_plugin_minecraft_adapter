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


# ── {sender}：模板里的发送者游戏 ID ─────────────────────


def test_uses_sender_flag():
    assert CustomCommandParser(["a <&x&><<>>b {x}"]).uses_sender is False
    assert CustomCommandParser(["a <&x&><<>>b {sender} {x}"]).uses_sender is True
    assert CustomCommandParser(["a <&x&><<>>b <&sender&> {x}"]).uses_sender is True


JAVA_BINDING = {
    "bound": True,
    "gameName": "Misaka",
    "bindings": [
        {
            "kind": "java",
            "gameName": "Misaka",
            "floodgate": False,
            "whitelistAdded": True,
        }
    ],
}


def _sender_env(mapping: list[str], lookup_result):
    """构造一个「自定义指令 + 绑定查询」的真实链路环境。"""
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
            lookup_binding=AsyncMock(return_value=lookup_result),
        ),
    )

    class _Mgr:
        def get_server(self, sid):
            return server

    config = ServerConfig(
        server_id="sv1",
        cmd_enabled=True,
        cmd_white_black_list="none",
        cmd_list=[],
        custom_cmd_list=mapping,
        target_sessions=["stream-1"],
    )

    async def _allow(platform, user_id):
        return True

    handler = CommandHandler(
        server_manager=_Mgr(),
        renderer=_Mgr(),
        get_server_config=lambda _: config,
        is_operator=_allow,
    )
    handler.register_custom_commands("sv1", mapping)
    ctx = CommandContext(stream_id="stream-1", platform="qq", user_id="123456")
    return handler, ctx, server, asyncio


def test_custom_command_sender_resolved_from_binding():
    """模板里的 {sender} 必须替换成发送者在目标服务器上的绑定游戏 ID。"""
    mapping = ["tpa <&p&><<>>tp {sender} <&p&>"]
    handler, ctx, server, asyncio = _sender_env(mapping, (True, JAVA_BINDING, 0, ""))

    result = asyncio.run(handler.handle_custom_command(ctx, "tpa Steve"))

    assert result is not None
    assert result.text.startswith("✅")
    server.rest_client.execute_command.assert_awaited_once()
    assert server.rest_client.execute_command.await_args.args[0] == "tp Misaka Steve"
    assert server.rest_client.lookup_binding.await_count == 1


def test_custom_command_sender_lookup_is_cached():
    """{sender} 的绑定查询带 TTL 缓存：连发两条消息只查一次。"""
    mapping = ["tpa <&p&><<>>tp {sender} <&p&>"]
    handler, ctx, server, asyncio = _sender_env(mapping, (True, JAVA_BINDING, 0, ""))

    asyncio.run(handler.handle_custom_command(ctx, "tpa Steve"))
    server.rest_client.execute_command.reset_mock()
    asyncio.run(handler.handle_custom_command(ctx, "tpa Alex"))

    assert server.rest_client.lookup_binding.await_count == 1
    assert server.rest_client.execute_command.await_args.args[0] == "tp Misaka Alex"


def test_custom_command_without_sender_skips_binding_lookup():
    """模板不引用 {sender} 时不得产生绑定查询。"""
    mapping = ["s <&x&><<>>say {x}"]
    handler, ctx, server, asyncio = _sender_env(mapping, (True, JAVA_BINDING, 0, ""))

    result = asyncio.run(handler.handle_custom_command(ctx, "s hello"))

    assert result is not None
    assert server.rest_client.lookup_binding.await_count == 0
    assert server.rest_client.execute_command.await_args.args[0] == "say hello"


def test_custom_command_sender_falls_back_to_geyser_binding():
    """只绑了基岩版时（服务端仍回 4004，但 bindings 有内容）仍要取到名字。"""
    mapping = ["tpa <&p&><<>>tp {sender} <&p&>"]
    lookup = (
        False,
        {"bindings": [{"kind": "geyser", "gameName": ".SteveBE"}]},
        4004,
        "",
    )
    handler, ctx, server, asyncio = _sender_env(mapping, lookup)

    asyncio.run(handler.handle_custom_command(ctx, "tpa Steve"))

    assert server.rest_client.execute_command.await_args.args[0] == "tp .SteveBE Steve"


def test_custom_command_sender_empty_when_unbound():
    """未绑定时 {sender} 为空串，命令照旧执行（不因查不到绑定而报错）。"""
    mapping = ["tpa <&p&><<>>tp {sender} <&p&>"]
    handler, ctx, server, asyncio = _sender_env(mapping, (False, {}, 4004, ""))

    result = asyncio.run(handler.handle_custom_command(ctx, "tpa Steve"))

    assert result is not None
    assert result.text.startswith("✅")
    assert server.rest_client.execute_command.await_args.args[0] == "tp  Steve"


def test_custom_command_sender_skips_lookup_when_trigger_not_matched():
    """触发词不命中时不查绑定（模板匹配在每条群消息路径上）。"""
    mapping = ["tpa <&p&><<>>tp {sender} <&p&>"]
    handler, ctx, server, asyncio = _sender_env(mapping, (True, JAVA_BINDING, 0, ""))

    assert asyncio.run(handler.handle_custom_command(ctx, "闲聊一句")) is None
    assert server.rest_client.lookup_binding.await_count == 0


# ── {sender} 缓存必须有界（键含 user_id，不设上限会随用户数慢泄漏） ──


def test_sender_name_cache_prunes_expired_entries():
    from maibot_plugin_minecraft_adapter.handlers.commands import (
        SENDER_NAME_CACHE_MAX,
    )

    mapping = ["tpa <&p&><<>>tp {sender} <&p&>"]
    handler, ctx, server, asyncio = _sender_env(mapping, (True, JAVA_BINDING, 0, ""))
    # 预置超量「早已过期」的条目（时间戳 0）
    handler._sender_name_cache = {
        (f"sv{i}", "qq", str(i)): (0.0, f"name{i}")
        for i in range(SENDER_NAME_CACHE_MAX + 10)
    }

    asyncio.run(handler.handle_custom_command(ctx, "tpa Steve"))

    assert len(handler._sender_name_cache) <= SENDER_NAME_CACHE_MAX
    # 过期项被清掉，本次查到的条目保留
    assert handler._sender_name_cache[("sv1", "qq", "123456")][1] == "Misaka"
    assert len(handler._sender_name_cache) == 1


def test_sender_name_cache_clears_when_all_entries_fresh():
    """全是未过期条目时整体清空（宁可多查一次，也不让表无界增长）。"""
    import time

    from maibot_plugin_minecraft_adapter.handlers.commands import (
        SENDER_NAME_CACHE_MAX,
    )

    mapping = ["tpa <&p&><<>>tp {sender} <&p&>"]
    handler, ctx, server, asyncio = _sender_env(mapping, (True, JAVA_BINDING, 0, ""))
    fresh = time.time()
    handler._sender_name_cache = {
        (f"sv{i}", "qq", str(i)): (fresh, f"name{i}")
        for i in range(SENDER_NAME_CACHE_MAX)
    }

    asyncio.run(handler.handle_custom_command(ctx, "tpa Steve"))

    assert len(handler._sender_name_cache) <= SENDER_NAME_CACHE_MAX
    assert handler._sender_name_cache[("sv1", "qq", "123456")][1] == "Misaka"
