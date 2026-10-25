"""编号选择的发起者门禁测试（handlers/commands.py）。

多目标（多服务器 / 代理后端）会话里，操作员发出 `/mc cmd` 或自定义指令后，命令
先变成一条按会话存档的待选动作；真正执行发生在用户回数字时，而那条路径走的是
入站消息钩子，不经过宿主 `@Command(permission="operator")` 过滤。这些用例锁住
「回数字的必须是发起者本人、且仍是操作员」，以及执行前的命令开关与黑白名单重查。

文案常量从被测模块导入，避免测试自带一份会漂移的副本。
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from maibot_plugin_minecraft_adapter.core.models import ServerConfig, ServerInfo
from maibot_plugin_minecraft_adapter.handlers.commands import (
    CMD_SELECTOR_MISMATCH,
    CMD_SELECTOR_NOT_OPERATOR,
    CommandContext,
    CommandHandler,
)

STREAM = "sess-1"
OPERATOR_ID = "123456789"
OTHER_ID = "987654321"

OPERATOR = CommandContext(stream_id=STREAM, platform="qq", user_id=OPERATOR_ID)
OTHER = CommandContext(stream_id=STREAM, platform="qq", user_id=OTHER_ID)

# 自定义指令映射：触发词 <<>> 实际指令（第一个词须在 cmd_list 白名单里）
CUSTOM_MAPPING = "hello <&who&><<>>say hello <&who&>"


class _Permission:
    """可变操作员名单：模拟「发起时是操作员、回编号时已不在名单」。"""

    def __init__(self, allowed: bool = True):
        self.allowed = allowed

    async def __call__(self, platform: str, user_id: str) -> bool:
        return self.allowed and user_id == OPERATOR_ID


def _make_env(server_count: int = 2, permission: _Permission | None = None):
    """构造「一个会话关联多台独立服务器」的环境（两台 → 两个执行目标）。"""
    servers = []
    configs: dict[str, ServerConfig] = {}
    for i in range(1, server_count + 1):
        server = MagicMock()
        server.server_id = f"sv{i}"
        server.connected = True
        server.server_info = ServerInfo(name=f"sv{i}")
        server.rest_client.get_server_info = AsyncMock(
            return_value=(ServerInfo(name=f"sv{i}"), "")
        )
        server.rest_client.execute_command = AsyncMock(return_value=(True, "done", 0))
        servers.append(server)
        configs[server.server_id] = ServerConfig(
            server_id=server.server_id,
            target_sessions=[STREAM],
            cmd_list=["say"],
            custom_cmd_list=[CUSTOM_MAPPING],
        )

    server_manager = MagicMock()
    server_manager.get_all_servers.return_value = {s.server_id: s for s in servers}
    server_manager.get_connected_servers.return_value = list(servers)
    server_manager.get_server.side_effect = lambda sid: next(
        (s for s in servers if s.server_id == sid), None
    )

    handler = CommandHandler(
        server_manager=server_manager,
        renderer=MagicMock(),
        get_server_config=lambda sid: configs.get(sid),
        is_operator=permission or _Permission(),
    )
    for server in servers:
        handler.register_custom_commands(server.server_id, [CUSTOM_MAPPING])
    return handler, servers, configs


def _executed(servers) -> int:
    return sum(s.rest_client.execute_command.await_count for s in servers)


# ── /mc cmd 的选号门禁 ─────────────────────────────────


def test_cmd_selection_rejects_non_initiator_and_keeps_pending():
    handler, servers, _ = _make_env()

    prompt = asyncio.run(handler.handle_cmd(OPERATOR, "say hi"))
    assert "请选择执行目标" in prompt.text
    assert _executed(servers) == 0

    denied = asyncio.run(handler.handle_number_selection(OTHER, "1"))
    assert denied.text == CMD_SELECTOR_MISMATCH
    assert _executed(servers) == 0
    # 别人回错不消耗发起者的 60 秒窗口
    assert handler.has_pending_action(STREAM) is True

    done = asyncio.run(handler.handle_number_selection(OPERATOR, "2"))
    assert done.text.startswith("✅")
    assert servers[1].rest_client.execute_command.await_count == 1
    assert servers[0].rest_client.execute_command.await_count == 0
    assert handler.has_pending_action(STREAM) is False


def test_cmd_selection_rechecks_operator_at_reply_time():
    permission = _Permission()
    handler, servers, _ = _make_env(permission=permission)

    prompt = asyncio.run(handler.handle_cmd(OPERATOR, "say hi"))
    assert "请选择执行目标" in prompt.text

    # 发起后操作员名单被改：回编号时已不再命中，不得执行
    permission.allowed = False
    denied = asyncio.run(handler.handle_number_selection(OPERATOR, "1"))
    assert denied.text == CMD_SELECTOR_NOT_OPERATOR
    assert _executed(servers) == 0
    assert handler.has_pending_action(STREAM) is True


def test_cmd_selection_without_operator_checker_refuses():
    """未注入操作员判定时按拒绝处理（fail-closed），而不是放行。"""
    handler, servers, _ = _make_env()
    handler._is_operator = None

    asyncio.run(handler.handle_cmd(OPERATOR, "say hi"))
    denied = asyncio.run(handler.handle_number_selection(OPERATOR, "1"))
    assert denied.text == CMD_SELECTOR_NOT_OPERATOR
    assert _executed(servers) == 0


# ── 自定义指令的选号门禁 ───────────────────────────────


def test_custom_cmd_selection_rejects_non_initiator():
    handler, servers, _ = _make_env()

    prompt = asyncio.run(handler.handle_custom_command(OPERATOR, "hello world"))
    assert prompt is not None
    assert "请选择执行目标" in prompt.text
    assert _executed(servers) == 0

    denied = asyncio.run(handler.handle_number_selection(OTHER, "1"))
    assert denied.text == CMD_SELECTOR_MISMATCH
    assert _executed(servers) == 0

    done = asyncio.run(handler.handle_number_selection(OPERATOR, "1"))
    assert done.text.startswith("✅")
    assert servers[0].rest_client.execute_command.await_count == 1


def test_custom_cmd_selection_rechecks_whitelist():
    handler, servers, configs = _make_env()

    prompt = asyncio.run(handler.handle_custom_command(OPERATOR, "hello world"))
    assert prompt is not None
    assert "请选择执行目标" in prompt.text

    # 60 秒窗口内该服务器的名单由白名单改成黑名单：选号时必须重查
    configs["sv2"].cmd_white_black_list = "black"
    denied = asyncio.run(handler.handle_number_selection(OPERATOR, "2"))
    assert denied.text == "❌ 此指令不在允许列表中"
    assert _executed(servers) == 0
