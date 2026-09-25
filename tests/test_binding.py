"""群友绑定（BindingHandler）门禁与文案测试。

覆盖：绑定总开关、基岩版开关、能力探测失败、能力不支持（旧版模组）、
本地名称校验（不得调用服务端）、服务端错误码映射、未连接路径、
以及多服务器编号选择与帮助文本的优雅降级。
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from maibot_plugin_minecraft_adapter.core.models import ServerConfig
from maibot_plugin_minecraft_adapter.core.protocol import (
    FEATURE_BINDING,
    ServerCapabilities,
)
from maibot_plugin_minecraft_adapter.handlers.commands import (
    BIND_MSG_DISABLED,
    BIND_MSG_GEYSER_DISABLED,
    BIND_MSG_INVALID_NAME,
    BIND_MSG_MOD_DISABLED,
    BIND_MSG_NAME_TAKEN,
    BIND_MSG_NOT_BOUND,
    BIND_MSG_NOT_CONNECTED,
    BIND_MSG_PROBE_FAILED,
    BIND_MSG_UNSUPPORTED,
    BIND_MSG_UNBIND_DISABLED,
    BIND_MSG_WHITELIST_FAILED,
    BindingHandler,
    CommandContext,
    CommandHandler,
)

STREAM = "sess-1"
USER_ID = "123456789"

CONTEXT = CommandContext(stream_id=STREAM, platform="qq", user_id=USER_ID)

# 旧版 AstrBotAdapter_Forge (v1.0.0) 的真实能力列表：没有 binding.v1
OLD_MOD_FEATURES = [
    "rest.servers.v2",
    "rest.health",
    "server.mspt",
    "players.detail",
    "players.offline-cache",
    "command.async-result",
    "command.target-server-id",
    "command.ws-session-reply",
    "ws.disconnect",
]


def _capless(probed: bool, features=None) -> ServerCapabilities:
    return ServerCapabilities(
        protocol_version=2 if probed else 0,
        features=set(features or ()),
        probed=probed,
    )


def _make_server(supports_binding: bool = True, connected: bool = True):
    """构造一个 ServerConnection 替身（真实属性 + mock 的 REST 客户端）。"""
    server = MagicMock()
    server.server_id = "sv1"
    server.connected = connected
    server.server_info = MagicMock(name="survival")
    server.capabilities = _capless(
        probed=supports_binding, features=[FEATURE_BINDING] if supports_binding else []
    )
    server.supports_binding = supports_binding
    server.rest_client.bind_player = AsyncMock(
        return_value=(
            True,
            {"gameName": "Steve", "whitelistAdded": True, "created": True},
            0,
            "",
        )
    )
    server.rest_client.unbind_player = AsyncMock(
        return_value=(True, {"gameName": "Steve", "unbound": True}, 0, "")
    )
    # 新版服务端：一个账号可同时有 Java 版与基岩版两条绑定
    server.rest_client.lookup_binding = AsyncMock(
        return_value=(
            True,
            {
                "bound": True,
                "gameName": "Steve",
                "javaBound": True,
                "geyserBound": True,
                "bindings": [
                    {
                        "kind": "java",
                        "gameName": "Steve",
                        "floodgate": False,
                        "whitelistAdded": True,
                    },
                    {
                        "kind": "geyser",
                        "gameName": ".SteveBE",
                        "floodgate": True,
                        "whitelistAdded": True,
                    },
                ],
            },
            0,
            "",
        )
    )
    return server


def _make_handler(config: ServerConfig, server, resolve_calls: list | None = None):
    calls = resolve_calls if resolve_calls is not None else []

    def resolve_server(stream_id, action="", args=None, servers=None):
        calls.append((stream_id, action, args, servers))
        return server, ""

    handler = BindingHandler(
        server_manager=MagicMock(),
        get_server_config=lambda sid: config if sid == config.server_id else None,
        resolve_server=resolve_server,
        session_servers=lambda stream_id, connected_only=False: [server],
    )
    return handler, calls


def _bind(config: ServerConfig, server, name: str, bedrock: bool = False):
    handler, calls = _make_handler(config, server)
    result = asyncio.run(handler.handle_bind(CONTEXT, name, bedrock=bedrock))
    return handler, calls, result


# ── 门禁 1：绑定总开关 ─────────────────────────────────


def test_bind_disabled():
    server = _make_server()
    _, _, result = _bind(
        ServerConfig(server_id="sv1", bind_enabled=False), server, "Steve"
    )
    assert result.is_image is False
    assert result.text == BIND_MSG_DISABLED
    server.rest_client.bind_player.assert_not_called()


# ── 门禁 2：基岩版开关 ─────────────────────────────────


def test_geyser_disabled():
    server = _make_server()
    config = ServerConfig(server_id="sv1", bind_geyser_enabled=False)
    _, _, result = _bind(config, server, "Steve", bedrock=True)
    assert result.text == BIND_MSG_GEYSER_DISABLED
    server.rest_client.bind_player.assert_not_called()

    # Java 版绑定不受基岩版开关影响
    _, _, java_result = _bind(config, server, "Steve", bedrock=False)
    assert java_result.text.startswith("✅")


def test_unbind_disabled():
    server = _make_server()
    config = ServerConfig(server_id="sv1", bind_unbind_enabled=False)
    handler, _ = _make_handler(config, server)
    result = asyncio.run(handler.handle_unbind(CONTEXT))
    assert result.text == BIND_MSG_UNBIND_DISABLED
    server.rest_client.unbind_player.assert_not_called()


# ── 门禁 3：能力探测 ───────────────────────────────────


def test_probe_failed_when_not_probed():
    server = _make_server(supports_binding=False)
    server.capabilities = _capless(probed=False)
    server.supports_binding = False
    _, _, result = _bind(ServerConfig(server_id="sv1"), server, "Steve")
    assert result.text == BIND_MSG_PROBE_FAILED
    server.rest_client.bind_player.assert_not_called()


def test_old_mod_features_refuse_binding_with_upgrade_hint():
    """回归基准：旧版模组（无 binding.v1）→ 升级提示，且不调用服务端。"""
    server = _make_server(supports_binding=False)
    server.capabilities = ServerCapabilities.from_health(
        {"protocolVersion": 2, "features": list(OLD_MOD_FEATURES)}
    )
    assert server.capabilities.probed is True
    server.supports_binding = False

    _, _, result = _bind(ServerConfig(server_id="sv1"), server, "Steve")
    assert result.text == BIND_MSG_UNSUPPORTED
    assert "binding.v1" not in result.text
    server.rest_client.bind_player.assert_not_called()


# ── 门禁 4：本地名称校验 ───────────────────────────────


@pytest.mark.parametrize(
    "bad_name",
    ["", "has space", "a" * 33, "bad!name", "名字@", "emoji😀"],
)
def test_invalid_name_rejected_without_calling_server(bad_name):
    server = _make_server()
    _, _, result = _bind(ServerConfig(server_id="sv1"), server, bad_name)
    assert result.text == BIND_MSG_INVALID_NAME
    server.rest_client.bind_player.assert_not_called()


@pytest.mark.parametrize(
    "good_name", ["Steve", "player_1", "a.b", "玩家一号", "x" * 32]
)
def test_valid_name_passes_local_validation(good_name):
    server = _make_server()
    _, _, result = _bind(ServerConfig(server_id="sv1"), server, good_name)
    assert result.text.startswith("✅ 绑定成功")
    server.rest_client.bind_player.assert_awaited_once()


# ── 门禁 5：服务端错误码映射 ───────────────────────────


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (4005, BIND_MSG_NAME_TAKEN),
        (4006, BIND_MSG_INVALID_NAME),
        # 4003 是「模组支持绑定但没开启」，不是「模组不支持」，两者文案必须区分开
        (4003, BIND_MSG_MOD_DISABLED),
        (5004, BIND_MSG_WHITELIST_FAILED),
    ],
)
def test_server_error_code_mapping(code, expected):
    """服务端错误码必须被映射成用户文案，而不是把服务端原文直接抛给用户。"""
    server = _make_server()
    server.rest_client.bind_player = AsyncMock(
        return_value=(False, {}, code, f"服务端错误 {code}")
    )
    _, _, result = _bind(ServerConfig(server_id="sv1"), server, "Steve")
    assert result.text == expected


def test_name_taken_message_does_not_leak_other_account():
    server = _make_server()
    server.rest_client.bind_player = AsyncMock(
        return_value=(False, {}, 4005, "游戏 ID Steve 已被其他账号绑定")
    )
    _, _, result = _bind(ServerConfig(server_id="sv1"), server, "Steve")
    assert result.text == BIND_MSG_NAME_TAKEN


def test_bind_error_with_server_message_is_passed_through():
    server = _make_server()
    server.rest_client.bind_player = AsyncMock(
        return_value=(False, {}, 3001, "服务器内部错误 3001")
    )
    _, _, result = _bind(ServerConfig(server_id="sv1"), server, "Steve")
    assert "3001" in result.text


def test_bind_error_without_message_has_fallback():
    server = _make_server()
    server.rest_client.bind_player = AsyncMock(return_value=(False, {}, 0, ""))
    _, _, result = _bind(ServerConfig(server_id="sv1"), server, "Steve")
    assert result.text == "❌ 操作失败，请稍后重试"


def test_bind_success_reports_whitelist():
    server = _make_server()
    _, _, result = _bind(ServerConfig(server_id="sv1"), server, "Steve")
    assert "Steve" in result.text
    assert "白名单" in result.text
    # 不回显他人 QQ 号/自己的 QQ 号
    assert USER_ID not in result.text


def test_bind_passes_platform_and_user_id_to_server():
    server = _make_server()
    _, _, _ = _bind(ServerConfig(server_id="sv1"), server, "Steve")
    kwargs = server.rest_client.bind_player.await_args.kwargs
    assert kwargs["platform"] == "qq"
    assert kwargs["user_id"] == USER_ID
    assert kwargs["game_name"] == "Steve"
    assert kwargs["bedrock"] is False


def test_geyserbind_sends_bedrock_flag():
    server = _make_server()
    _, _, _ = _bind(ServerConfig(server_id="sv1"), server, "Bedrock_1", bedrock=True)
    kwargs = server.rest_client.bind_player.await_args.kwargs
    assert kwargs["bedrock"] is True


# ── 门禁 6：WS 未连接 ─────────────────────────────────


def test_not_connected():
    server = _make_server(connected=False)
    _, _, result = _bind(ServerConfig(server_id="sv1"), server, "Steve")
    assert result.text == BIND_MSG_NOT_CONNECTED
    server.rest_client.bind_player.assert_not_called()


# ── mybind / unbind 流程 ───────────────────────────────


def test_mybind_not_bound():
    server = _make_server()
    server.rest_client.lookup_binding = AsyncMock(
        return_value=(False, {}, 4004, "尚未绑定")
    )
    handler, _ = _make_handler(ServerConfig(server_id="sv1"), server)
    result = asyncio.run(handler.handle_mybind(CONTEXT))
    assert result.text == BIND_MSG_NOT_BOUND


def test_mybind_bound_shows_game_name():
    server = _make_server()
    handler, _ = _make_handler(ServerConfig(server_id="sv1"), server)
    result = asyncio.run(handler.handle_mybind(CONTEXT))
    assert "Steve" in result.text
    assert USER_ID not in result.text


def test_mybind_unbound_flag():
    server = _make_server()
    server.rest_client.lookup_binding = AsyncMock(
        return_value=(True, {"bound": False, "gameName": ""}, 0, "")
    )
    handler, _ = _make_handler(ServerConfig(server_id="sv1"), server)
    result = asyncio.run(handler.handle_mybind(CONTEXT))
    assert result.text == BIND_MSG_NOT_BOUND


def test_mybind_shows_both_java_and_geyser():
    """同一账号的两条绑定必须同时展示，不能只显示一条。"""
    server = _make_server()
    handler, _ = _make_handler(ServerConfig(server_id="sv1"), server)
    result = asyncio.run(handler.handle_mybind(CONTEXT))
    assert "Steve" in result.text
    assert ".SteveBE" in result.text
    assert "Java 版" in result.text
    assert "基岩版" in result.text
    assert USER_ID not in result.text


def test_mybind_only_geyser_binding_is_shown():
    """只绑了基岩版时也要能显示出来（旧逻辑会因 Java 未绑定而误报未绑定）。"""
    server = _make_server()
    server.rest_client.lookup_binding = AsyncMock(
        return_value=(
            False,
            {
                "bound": False,
                "gameName": "",
                "javaBound": False,
                "geyserBound": True,
                "bindings": [
                    {
                        "kind": "geyser",
                        "gameName": ".OnlyBE",
                        "floodgate": True,
                        "whitelistAdded": True,
                    }
                ],
            },
            4004,
            "尚未绑定",
        )
    )
    handler, _ = _make_handler(ServerConfig(server_id="sv1"), server)
    result = asyncio.run(handler.handle_mybind(CONTEXT))
    assert ".OnlyBE" in result.text
    assert "基岩版" in result.text


def test_mybind_falls_back_for_old_mod_without_bindings_array():
    """连接旧模组（响应无 bindings 数组）时回退到旧的单条展示。"""
    server = _make_server()
    server.rest_client.lookup_binding = AsyncMock(
        return_value=(
            True,
            {"bound": True, "gameName": "Legacy", "floodgate": False},
            0,
            "",
        )
    )
    handler, _ = _make_handler(ServerConfig(server_id="sv1"), server)
    result = asyncio.run(handler.handle_mybind(CONTEXT))
    assert "Legacy" in result.text


def test_unbind_defaults_to_all_kinds():
    """/mc unbind 清空该账号全部绑定。"""
    server = _make_server()
    handler, _ = _make_handler(ServerConfig(server_id="sv1"), server)
    asyncio.run(handler.handle_unbind(CONTEXT))
    assert server.rest_client.unbind_player.await_args.kwargs["kind"] == "all"


def test_geyserunbind_only_targets_geyser():
    """/mc geyserunbind 只解基岩版那条，Java 版不受影响。"""
    server = _make_server()
    server.rest_client.unbind_player = AsyncMock(
        return_value=(
            True,
            {
                "unbound": True,
                "whitelistRemoved": True,
                "removed": [
                    {
                        "kind": "geyser",
                        "gameName": ".SteveBE",
                        "whitelistRemoved": True,
                    }
                ],
            },
            0,
            "",
        )
    )
    handler, _ = _make_handler(ServerConfig(server_id="sv1"), server)
    result = asyncio.run(handler.handle_geyserunbind(CONTEXT))
    assert server.rest_client.unbind_player.await_args.kwargs["kind"] == "geyser"
    assert ".SteveBE" in result.text
    assert "基岩版" in result.text
    assert USER_ID not in result.text


def test_unbind_reports_both_removed():
    """清空全部绑定时逐条回报被移除的记录。"""
    server = _make_server()
    server.rest_client.unbind_player = AsyncMock(
        return_value=(
            True,
            {
                "unbound": True,
                "whitelistRemoved": True,
                "removed": [
                    {"kind": "java", "gameName": "Steve", "whitelistRemoved": True},
                    {
                        "kind": "geyser",
                        "gameName": ".SteveBE",
                        "whitelistRemoved": True,
                    },
                ],
            },
            0,
            "",
        )
    )
    handler, _ = _make_handler(ServerConfig(server_id="sv1"), server)
    result = asyncio.run(handler.handle_unbind(CONTEXT))
    assert "Steve" in result.text
    assert ".SteveBE" in result.text


def test_unbind_success():
    server = _make_server()
    server.rest_client.unbind_player = AsyncMock(
        return_value=(
            True,
            {"gameName": "Steve", "unbound": True, "whitelistRemoved": True},
            0,
            "",
        )
    )
    handler, _ = _make_handler(ServerConfig(server_id="sv1"), server)
    result = asyncio.run(handler.handle_unbind(CONTEXT))
    assert "Steve" in result.text
    assert "白名单" in result.text
    assert USER_ID not in result.text


def test_no_session_server():
    handler = BindingHandler(
        server_manager=MagicMock(),
        get_server_config=lambda sid: None,
        resolve_server=lambda *a, **kw: (None, ""),
        session_servers=lambda stream_id, connected_only=False: [],
    )
    result = asyncio.run(handler.handle_bind(CONTEXT, "Steve"))
    assert "未关联任何服务器" in result.text


# ── 多服务器编号选择 ───────────────────────────────────
#
# 待选动作一律由真实命令入口（handle_bind / handle_unbind / handle_mybind →
# CommandHandler._resolve_server_or_pending）产生，不手工构造 PendingAction：
# 手工塞 args 会掩盖「待选动作没带 game_name / 真实 action」这类链路缺陷。


def _make_multi_server_env(
    server_count: int = 2, config_overrides: dict | None = None
):
    """构造「一个会话关联多台服务器」的真实链路环境。

    ``config_overrides``: {"sv3": {"bind_enabled": False}} —— 给个别服务器改门禁开关。
    返回 (CommandHandler, BindingHandler, servers)。
    """
    servers = []
    configs: dict[str, ServerConfig] = {}
    for i in range(1, server_count + 1):
        server = _make_server()
        server.server_id = f"sv{i}"
        servers.append(server)
        configs[server.server_id] = ServerConfig(
            server_id=server.server_id, target_sessions=[STREAM]
        )
    for server_id, overrides in (config_overrides or {}).items():
        if server_id not in configs:
            raise ValueError(f"unknown server_id: {server_id}")
        configs[server_id] = ServerConfig(
            server_id=server_id, target_sessions=[STREAM], **overrides
        )

    server_manager = MagicMock()
    server_manager.get_all_servers.return_value = {s.server_id: s for s in servers}
    server_manager.get_connected_servers.return_value = list(servers)
    server_manager.get_server.side_effect = lambda sid: next(
        (s for s in servers if s.server_id == sid), None
    )

    command_handler = CommandHandler(
        server_manager=server_manager,
        renderer=MagicMock(),
        get_server_config=lambda sid: configs.get(sid),
    )
    binding = BindingHandler(
        server_manager=server_manager,
        get_server_config=lambda sid: configs.get(sid),
        resolve_server=command_handler._resolve_server_or_pending,
        session_servers=command_handler._get_session_servers_by,
    )
    command_handler.pending_dispatcher = binding.handle_selection
    return command_handler, binding, servers


def _make_multi_server_handler(action_recorder: list):
    """只需观察「写进待选动作的 action / args」时用的轻量替身。"""
    server = _make_server()
    config = ServerConfig(server_id="sv1", target_sessions=[STREAM])
    handler = BindingHandler(
        server_manager=MagicMock(),
        get_server_config=lambda sid: config,
        resolve_server=lambda stream_id, action="", args=None, servers=None: (
            action_recorder.append((stream_id, action, args)) or None,
            "⚠️ 当前会话关联多个服务器，请发送编号选择:\n1. sv1\n2. sv2",
        ),
        session_servers=lambda stream_id, connected_only=False: [server, server],
    )
    return handler, server


def test_multi_server_prompts_for_selection_with_intent():
    """两台可用服务器 → 回选号提示，且待选动作必须带真实意图与原始参数。

    待选动作缺 game_name 时，用户回编号后会拿到「游戏 ID 只能包含…」且什么都不做。
    """
    calls: list = []
    handler, server = _make_multi_server_handler(calls)

    result = asyncio.run(handler.handle_bind(CONTEXT, "Steve"))
    assert "编号选择" in result.text
    assert calls == [(STREAM, "bind:java", {"bedrock": False, "game_name": "Steve"})]
    server.rest_client.bind_player.assert_not_called()


def test_multi_server_geyser_bind_pending_keeps_bedrock_intent():
    calls: list = []
    handler, _ = _make_multi_server_handler(calls)

    result = asyncio.run(handler.handle_bind(CONTEXT, "Bedrock_1", bedrock=True))
    assert "编号选择" in result.text
    assert calls == [
        (STREAM, "bind:geyser", {"bedrock": True, "game_name": "Bedrock_1"})
    ]


def test_multi_server_unbind_pending_carries_real_action():
    """`/mc unbind` 与 `/mc geyserunbind` 必须写 bind:unbind（而不是 bind:java）。"""
    calls: list = []
    handler, _ = _make_multi_server_handler(calls)

    asyncio.run(handler.handle_unbind(CONTEXT))
    asyncio.run(handler.handle_geyserunbind(CONTEXT))
    assert calls == [
        (STREAM, "bind:unbind", {"bedrock": False, "game_name": "", "kind": "all"}),
        (STREAM, "bind:unbind", {"bedrock": False, "game_name": "", "kind": "geyser"}),
    ]


def test_multi_server_mybind_pending_carries_real_action():
    calls: list = []
    handler, _ = _make_multi_server_handler(calls)

    asyncio.run(handler.handle_mybind(CONTEXT))
    assert calls == [(STREAM, "bind:mybind", {"bedrock": False, "game_name": ""})]


def test_bind_selection_executes_on_chosen_server():
    command_handler, binding, servers = _make_multi_server_env()

    prompt = asyncio.run(binding.handle_bind(CONTEXT, "Steve"))
    assert "编号选择" in prompt.text
    assert all(s.rest_client.bind_player.await_count == 0 for s in servers)

    result = asyncio.run(command_handler.handle_number_selection(CONTEXT, "2"))
    assert result.text.startswith("✅ 绑定成功")
    assert servers[0].rest_client.bind_player.await_count == 0
    kwargs = servers[1].rest_client.bind_player.await_args.kwargs
    assert kwargs["game_name"] == "Steve"
    assert kwargs["bedrock"] is False
    assert kwargs["platform"] == "qq"
    assert kwargs["user_id"] == USER_ID
    assert command_handler.has_pending_action(STREAM) is False


def test_geyser_bind_selection_keeps_game_name_and_bedrock():
    command_handler, binding, servers = _make_multi_server_env()

    prompt = asyncio.run(binding.handle_bind(CONTEXT, "Bedrock_1", bedrock=True))
    assert "编号选择" in prompt.text

    result = asyncio.run(command_handler.handle_number_selection(CONTEXT, "1"))
    assert result.text.startswith("✅ 绑定成功")
    kwargs = servers[0].rest_client.bind_player.await_args.kwargs
    assert kwargs["game_name"] == "Bedrock_1"
    assert kwargs["bedrock"] is True


def test_unbind_selection_calls_unbind_player():
    command_handler, binding, servers = _make_multi_server_env()

    prompt = asyncio.run(binding.handle_unbind(CONTEXT))
    assert "编号选择" in prompt.text
    assert all(s.rest_client.unbind_player.await_count == 0 for s in servers)

    result = asyncio.run(command_handler.handle_number_selection(CONTEXT, "1"))
    assert result.text.startswith("✅ 已解除绑定")
    assert servers[0].rest_client.unbind_player.await_args.kwargs["kind"] == "all"
    assert servers[1].rest_client.unbind_player.await_count == 0


def test_bind_prompt_excludes_binding_disabled_servers():
    """绑定门禁关掉的服务器不得出现在编号提示里，更不能被选中执行。

    提示里列出全部已连接服务器、待选动作却只带过滤后列表（或反之），都会让
    「提示里列出的 ≠ 能选的」——用户照提示选号只能拿到服务端错误。
    """
    command_handler, binding, servers = _make_multi_server_env(
        server_count=3, config_overrides={"sv3": {"bind_enabled": False}}
    )

    prompt = asyncio.run(binding.handle_bind(CONTEXT, "Steve"))
    assert "编号选择" in prompt.text
    assert "1. sv1" in prompt.text
    assert "2. sv2" in prompt.text
    assert "sv3" not in prompt.text
    # 过滤说明与实际列出的条数必须一致（3 台里 1 台不合门禁）
    assert "过滤掉 1 个不可用服务器" in prompt.text

    pending = command_handler._pending_actions[STREAM]
    assert [s.server_id for s in pending.servers] == ["sv1", "sv2"]

    # 编号 3（sv3）无效 → 不执行任何绑定
    result = asyncio.run(command_handler.handle_number_selection(CONTEXT, "3"))
    assert "编号无效" in result.text
    assert "sv3" not in result.text
    assert all(s.rest_client.bind_player.await_count == 0 for s in servers)


def test_geyser_bind_prompt_excludes_geyser_disabled_servers():
    """只开了 Java 绑定的服务器，在 /mc geyserbind 的选号里同样不出现。"""
    command_handler, binding, _servers = _make_multi_server_env(
        server_count=3, config_overrides={"sv3": {"bind_geyser_enabled": False}}
    )

    prompt = asyncio.run(binding.handle_bind(CONTEXT, "Bedrock_1", bedrock=True))
    assert "sv3" not in prompt.text
    assert [s.server_id for s in command_handler._pending_actions[STREAM].servers] == [
        "sv1",
        "sv2",
    ]


def test_unbind_prompt_excludes_binding_disabled_servers():
    """/mc unbind 的选号同样只列可用服务器（解绑与绑定共用同一套门禁口径）。"""
    command_handler, binding, _servers = _make_multi_server_env(
        server_count=3, config_overrides={"sv3": {"bind_enabled": False}}
    )

    prompt = asyncio.run(binding.handle_unbind(CONTEXT))
    assert "sv3" not in prompt.text
    assert [s.server_id for s in command_handler._pending_actions[STREAM].servers] == [
        "sv1",
        "sv2",
    ]


def test_single_available_server_binds_without_prompt():
    """过滤后只剩一台可用服务器 → 直接执行，不该因为会话里还有别的服务器就选号。"""
    command_handler, binding, servers = _make_multi_server_env(
        server_count=3,
        config_overrides={
            "sv2": {"bind_enabled": False},
            "sv3": {"bind_enabled": False},
        },
    )

    result = asyncio.run(binding.handle_bind(CONTEXT, "Steve"))
    assert result.text.startswith("✅ 绑定成功")
    assert command_handler.has_pending_action(STREAM) is False
    assert servers[0].rest_client.bind_player.await_count == 1
    assert servers[1].rest_client.bind_player.await_count == 0
    assert servers[2].rest_client.bind_player.await_count == 0


def test_bind_prompt_excludes_offline_servers():
    """离线服务器不得出现在编号提示里，也不能被选中执行（在线性也是门禁维度）。"""
    command_handler, binding, servers = _make_multi_server_env(server_count=3)
    servers[2].connected = False  # sv3 绑定门禁开着，但没连上

    prompt = asyncio.run(binding.handle_bind(CONTEXT, "Steve"))
    assert "编号选择" in prompt.text
    assert "sv3" not in prompt.text
    assert "过滤掉 1 个不可用服务器" in prompt.text

    assert [s.server_id for s in command_handler._pending_actions[STREAM].servers] == [
        "sv1",
        "sv2",
    ]

    result = asyncio.run(command_handler.handle_number_selection(CONTEXT, "3"))
    assert "编号无效" in result.text
    assert all(s.rest_client.bind_player.await_count == 0 for s in servers)


def test_offline_only_available_server_reports_not_connected():
    """只剩一台「门禁都过但离线」的服务器 → 必须是「未连接」，不能是「不支持绑定」。"""
    command_handler, binding, servers = _make_multi_server_env(
        server_count=3,
        config_overrides={
            "sv2": {"bind_enabled": False},
            "sv3": {"bind_enabled": False},
        },
    )
    servers[0].connected = False

    result = asyncio.run(binding.handle_bind(CONTEXT, "Steve"))
    assert result.text == BIND_MSG_NOT_CONNECTED
    assert "不支持绑定" not in result.text
    assert command_handler.has_pending_action(STREAM) is False
    assert all(s.rest_client.bind_player.await_count == 0 for s in servers)


def test_offline_only_available_server_unbind_reports_not_connected():
    """解绑/查询走同一套门禁：离线时同样给「未连接」而不是「未启用解绑」。"""
    command_handler, binding, servers = _make_multi_server_env(
        server_count=2, config_overrides={"sv2": {"bind_enabled": False}}
    )
    servers[0].connected = False

    for result in (
        asyncio.run(binding.handle_unbind(CONTEXT)),
        asyncio.run(binding.handle_mybind(CONTEXT)),
    ):
        assert result.text == BIND_MSG_NOT_CONNECTED
    assert all(s.rest_client.unbind_player.await_count == 0 for s in servers)
    assert all(s.rest_client.lookup_binding.await_count == 0 for s in servers)


def test_geyserunbind_selection_keeps_kind():
    command_handler, binding, servers = _make_multi_server_env()

    prompt = asyncio.run(binding.handle_geyserunbind(CONTEXT))
    assert "编号选择" in prompt.text

    asyncio.run(command_handler.handle_number_selection(CONTEXT, "2"))
    assert servers[1].rest_client.unbind_player.await_args.kwargs["kind"] == "geyser"


def test_mybind_selection_calls_lookup_binding():
    command_handler, binding, servers = _make_multi_server_env()

    prompt = asyncio.run(binding.handle_mybind(CONTEXT))
    assert "编号选择" in prompt.text

    result = asyncio.run(command_handler.handle_number_selection(CONTEXT, "2"))
    assert "你的绑定" in result.text
    assert servers[1].rest_client.lookup_binding.await_count == 1
    assert servers[0].rest_client.lookup_binding.await_count == 0


def test_selection_rejects_invalid_name_without_calling_server():
    """选号只解决「在哪台服执行」，本地名称校验仍必须挡住非法 ID。"""
    command_handler, binding, servers = _make_multi_server_env()

    asyncio.run(binding.handle_bind(CONTEXT, "bad name"))
    result = asyncio.run(command_handler.handle_number_selection(CONTEXT, "1"))
    assert result.text == BIND_MSG_INVALID_NAME
    assert servers[0].rest_client.bind_player.await_count == 0


def test_selection_invalid_index_lists_choices():
    command_handler, binding, servers = _make_multi_server_env()
    asyncio.run(binding.handle_bind(CONTEXT, "Steve"))
    pending = command_handler._pending_actions[STREAM]

    result = asyncio.run(binding.handle_selection(pending, 9, CONTEXT))
    assert "编号无效" in result.text
    assert "sv1" in result.text
    assert "sv2" in result.text


def test_command_handler_routes_bind_pending_and_keeps_it_on_bad_index():
    command_handler, binding, servers = _make_multi_server_env()
    asyncio.run(binding.handle_bind(CONTEXT, "Steve"))

    bad = asyncio.run(command_handler.handle_number_selection(CONTEXT, "7"))
    assert "编号无效" in bad.text
    assert command_handler.has_pending_action(STREAM) is True

    good = asyncio.run(command_handler.handle_number_selection(CONTEXT, "1"))
    assert good.text.startswith("✅ 绑定成功")
    assert command_handler.has_pending_action(STREAM) is False
    assert servers[0].rest_client.bind_player.await_args.kwargs["game_name"] == "Steve"


# ── 帮助文本降级 ───────────────────────────────────────


def _help_handler(server, config):
    server_manager = MagicMock()
    server_manager.get_all_servers.return_value = {"sv1": server}
    return CommandHandler(
        server_manager=server_manager,
        renderer=MagicMock(),
        get_server_config=lambda sid: config if sid == "sv1" else None,
    )


def test_help_lists_binding_when_supported():
    server = _make_server(supports_binding=True)
    config = ServerConfig(server_id="sv1", target_sessions=[STREAM])
    handler = _help_handler(server, config)
    text = asyncio.run(handler.handle_help(CONTEXT)).text
    assert "/mc bind" in text
    assert "/mc geyserbind" in text
    assert "/mc unbind" in text
    assert "/mc mybind" in text


def test_help_omits_binding_on_old_mod():
    server = _make_server(supports_binding=False)
    server.capabilities = ServerCapabilities.from_health(
        {"protocolVersion": 2, "features": list(OLD_MOD_FEATURES)}
    )
    config = ServerConfig(server_id="sv1", target_sessions=[STREAM])
    handler = _help_handler(server, config)
    text = asyncio.run(handler.handle_help(CONTEXT)).text
    assert "/mc geyserbind" not in text
    assert "/mc mybind" not in text
    assert "/mc status" in text


def test_help_geyser_line_hidden_when_disabled():
    server = _make_server(supports_binding=True)
    config = ServerConfig(
        server_id="sv1", target_sessions=[STREAM], bind_geyser_enabled=False
    )
    handler = _help_handler(server, config)
    text = asyncio.run(handler.handle_help(CONTEXT)).text
    assert "/mc bind" in text
    assert "/mc geyserbind" not in text


# ── WS CONNECTION_ACK 解析（协议 §4.1：字段在 payload 内） ──


class _FakeWS:
    def __init__(self, payload):
        self._payload = payload
        self.closed = False

    async def receive(self):
        import aiohttp

        msg = MagicMock()
        msg.type = aiohttp.WSMsgType.TEXT
        msg.json.return_value = self._payload
        return msg

    async def close(self):
        self.closed = True


def test_connection_ack_reads_payload_for_server_info():
    from maibot_plugin_minecraft_adapter.core.ws_client import WebSocketClient

    client = WebSocketClient("sv1", "127.0.0.1", 8765, "tok")
    client._ws = _FakeWS(
        {
            "type": "CONNECTION_ACK",
            "id": "m1",
            "payload": {
                "protocolVersion": 2,
                "sessionId": "ws-session-1",
                "serverInfo": {
                    "name": "survival",
                    "platform": "Paper",
                    "version": "1.21.1",
                },
            },
        }
    )

    session = MagicMock()
    session.closed = False
    session.ws_connect = AsyncMock(return_value=client._ws)
    client._session = session  # 让 connect() 直接复用替身会话（不发起真实网络请求）

    ok = asyncio.run(client.connect())
    assert ok is True
    assert client.connected is True
    assert client.server_info is not None
    assert client.server_info.name == "survival"
    assert client.server_info.platform == "Paper"
    assert client.server_info.minecraft_version == "1.21.1"


def test_connection_ack_tolerates_missing_payload():
    from maibot_plugin_minecraft_adapter.core.ws_client import WebSocketClient

    client = WebSocketClient("sv1", "127.0.0.1", 8765, "tok")
    client._ws = _FakeWS({"type": "CONNECTION_ACK", "id": "m1"})

    session = MagicMock()
    session.closed = False
    session.ws_connect = AsyncMock(return_value=client._ws)
    client._session = session  # 让 connect() 直接复用替身会话（不发起真实网络请求）

    assert asyncio.run(client.connect()) is True
    assert client.connected is True
    assert client.server_info is not None
    assert client.server_info.name == ""


# ── 连接时能力探测（ServerConnection） ─────────────────


def _make_connection(capabilities):
    """构造真实 ServerConnection（REST/WS 客户端替换为替身）。"""
    from maibot_plugin_minecraft_adapter.core.server_manager import ServerConnection

    conn = ServerConnection(ServerConfig(server_id="sv1", host="127.0.0.1"))
    conn.rest_client.fetch_capabilities = AsyncMock(return_value=capabilities)
    return conn


def test_server_connection_probes_capabilities_on_connect():
    conn = _make_connection(
        ServerCapabilities.from_health(
            {"protocolVersion": 2, "features": [FEATURE_BINDING]}
        )
    )
    # 未探测前必须视为不支持（fail closed）
    assert conn.supports_binding is False
    assert conn.capabilities.probed is False

    asyncio.run(conn._handle_connect(None))
    conn.rest_client.fetch_capabilities.assert_awaited_once()
    assert conn.capabilities.probed is True
    assert conn.supports_binding is True


def test_server_connection_probe_failure_degrades_without_error():
    conn = _make_connection(ServerCapabilities.unprobed())
    asyncio.run(conn._handle_connect(None))
    assert conn.capabilities.probed is False
    assert conn.supports_binding is False


def test_server_connection_reprobes_on_every_connect():
    for caps in (
        ServerCapabilities.from_health({"features": [FEATURE_BINDING]}),
        ServerCapabilities.from_health({"features": list(OLD_MOD_FEATURES)}),
    ):
        conn = _make_connection(caps)
        asyncio.run(conn._handle_connect(None))
        assert conn.supports_binding is caps.supports_binding
