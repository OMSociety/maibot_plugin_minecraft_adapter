"""Minecraft 聊天适配器插件的命令处理器。

从 AstrBot 版 handlers/commands.py 迁移：
- 原 AstrMessageEvent 事件式接口 → 改为 CommandContext 上下文 + 直接返回 RenderResult。
- 纯逻辑（自定义指令解析、黑白名单、多服务器目标选择、Velocity 后端展开）原样保留。
"""

import logging
import re
import time
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from ..services.renderer import RenderResult

if TYPE_CHECKING:
    from ..core.server_manager import ServerManager
    from ..services.renderer import InfoRenderer

logger = logging.getLogger(__name__)


@dataclass
class CommandContext:
    """命令处理所需的会话上下文（替代 AstrBot 的 AstrMessageEvent）。"""

    stream_id: str = ""  # MaiBot 聊天流 ID（会话 ID），用于待选操作去重
    platform: str = ""  # 发送者所在平台名
    user_id: str = ""  # 发送者用户 ID


class CustomCommandParser:
    """自定义命令映射解析器"""

    # 格式: trigger <&arg1&> <&arg2&><<>>actual_command {sender} {arg1} {arg2}
    SEPARATOR = "<<>>"

    def __init__(self, mappings: list[str]):
        """使用映射字符串初始化

        格式: "trigger <&param&><<>>actual_command {param} {sender}"
        """
        self.mappings: list[dict[str, object]] = []
        for mapping in mappings:
            parsed = self._parse_mapping(mapping)
            if parsed:
                self.mappings.append(parsed)

    def _parse_mapping(self, mapping: str) -> dict[str, object] | None:
        """解析映射字符串

        返回:
            tuple: (trigger_pattern, param_names, command_template) 或 None
        """
        if self.SEPARATOR not in mapping:
            return None

        trigger_part, command_part = mapping.split(self.SEPARATOR, 1)
        trigger_part = trigger_part.strip()
        command_part = command_part.strip()

        # 从触发器中提取参数占位符: <&name&>
        param_pattern = r"<&(\w+)&>"
        param_names = re.findall(param_pattern, trigger_part)

        # 构建用于匹配触发器的正则表达式模式
        # 将 <&name&> 替换为命名捕获组
        trigger_regex = trigger_part
        for param in param_names:
            trigger_regex = trigger_regex.replace(f"<&{param}&>", f"(?P<{param}>\\S+)")

        trigger_name = trigger_part.split()[0] if trigger_part else ""
        return {
            "trigger_part": trigger_part,
            "trigger_name": trigger_name,
            "trigger_regex": trigger_regex,
            "param_names": param_names,
            "command_template": command_part,
        }

    def match(
        self, text: str, sender_mc_name: str | None = None
    ) -> tuple[str, dict] | None:
        """尝试将输入文本与自定义命令匹配

        返回:
            tuple: (actual_command, matched_params) 或 None
        """
        for mapping in self.mappings:
            trigger_regex = mapping["trigger_regex"]
            command_template = mapping["command_template"]
            match = re.match(f"^{trigger_regex}$", text, re.IGNORECASE)
            if match:
                params = match.groupdict()
                # 添加发送者参数
                params["sender"] = sender_mc_name or ""

                # 构建实际命令
                command = command_template
                for key, value in params.items():
                    command = command.replace(f"{{{key}}}", value)
                    command = command.replace(f"<&{key}&>", value)

                return command, params

        return None

    def get_missing_usage(self, text: str) -> str | None:
        """If text looks like a custom command but misses params, return usage."""
        tokens = re.split(r"\s+", text.strip())
        if not tokens or not tokens[0]:
            return None

        first_token = tokens[0].lower()
        for mapping in self.mappings:
            trigger_name = str(mapping["trigger_name"]).lower()
            if not trigger_name or first_token != trigger_name:
                continue
            param_names = mapping["param_names"]
            expected_count = 1 + len(param_names)
            if len(tokens) < expected_count:
                return str(mapping["trigger_part"])

        return None


@dataclass
class CmdTarget:
    """A selectable command target (proxy itself or a backend server)"""

    label: str  # display label
    server: object  # ServerConnection
    target_server: str | None = None  # None = execute on proxy itself


@dataclass
class PendingAction:
    """A pending action waiting for the user to select a number"""

    action: str  # The command name: "status", "list", "player", "cmd"
    args: dict[str, Any] = field(default_factory=dict)
    servers: list = field(default_factory=list)  # list of ServerConnection
    cmd_targets: list[CmdTarget] = field(
        default_factory=list
    )  # unified cmd target choices (proxy + backends)
    timestamp: float = 0.0


# Pending actions expire after 60 seconds
PENDING_ACTION_TIMEOUT = 60


class CommandHandler:
    """所有 mc 命令的处理器"""

    def __init__(
        self,
        server_manager: "ServerManager",
        renderer: "InfoRenderer",
        get_server_config,
    ):
        self.server_manager = server_manager
        self.renderer = renderer
        self.get_server_config = get_server_config
        self._custom_parsers: dict[str, CustomCommandParser] = {}
        # Pending actions per stream_id
        self._pending_actions: dict[str, PendingAction] = {}

    def register_custom_commands(self, server_id: str, mappings: list[str]):
        """为服务器注册自定义命令"""
        self._custom_parsers[server_id] = CustomCommandParser(mappings)
        logger.info(
            f"[CommandHandler] 已为服务器 {server_id} 注册了 {len(mappings)} 个自定义命令"
        )

    def has_pending_action(self, stream_id: str) -> bool:
        """Check if a session has a valid pending action."""
        pending = self._pending_actions.get(stream_id)
        if not pending:
            return False
        if time.time() - pending.timestamp > PENDING_ACTION_TIMEOUT:
            del self._pending_actions[stream_id]
            return False
        return True

    async def handle_number_selection(
        self, ctx: CommandContext, selection_text: str
    ) -> RenderResult:
        """处理多服务器/多后端目标的编号选择（由消息 Hook 调用）。"""
        pending = self._pending_actions.pop(ctx.stream_id, None)
        if not pending:
            return RenderResult("", is_image=False)

        if not selection_text.isdigit():
            self._pending_actions[ctx.stream_id] = pending
            return RenderResult("❌ 请发送有效的数字编号", is_image=False)

        idx = int(selection_text)
        action = pending.action
        args = pending.args

        if pending.cmd_targets:
            # Unified cmd target selection (proxy + backends)
            if idx < 1 or idx > len(pending.cmd_targets):
                choices = self._format_target_choices(pending.cmd_targets)
                self._pending_actions[ctx.stream_id] = pending
                return RenderResult(
                    f"❌ 编号无效，请从以下列表中选择:\n{choices}", is_image=False
                )

            target = pending.cmd_targets[idx - 1]
            server = target.server
            target_server = target.target_server

            # Auth check only for user-initiated cmd
            if action == "cmd":
                allowed, deny_message = self._is_cmd_allowed_on_server(
                    args["command"], server
                )
                if not allowed:
                    return RenderResult(deny_message, is_image=False)
            return await self._do_cmd(
                ctx, server, args["command"], target_server=target_server
            )

        # Server selection (multi-server mode) for non-cmd actions
        if idx < 1 or idx > len(pending.servers):
            choices = self._format_server_choices(pending.servers)
            self._pending_actions[ctx.stream_id] = pending
            return RenderResult(
                f"❌ 编号无效，请从以下列表中选择:\n{choices}", is_image=False
            )

        server = pending.servers[idx - 1]
        return await self._dispatch_server_action(ctx, action, server, args)

    async def handle_custom_command(
        self, ctx: CommandContext, text: str
    ) -> RenderResult | None:
        """尝试匹配并执行自定义指令；无匹配返回 None。"""
        text = text.strip()
        if not text:
            return None

        # Collect all matching servers and their resolved commands
        all_targets: list[CmdTarget] = []
        matched_command: str | None = None
        first_missing_usage: str | None = None

        for server_id, parser in self._custom_parsers.items():
            config = self.get_server_config(server_id)
            if not config:
                continue
            if (
                not config.target_sessions
                or ctx.stream_id not in config.target_sessions
            ):
                continue
            if not config.cmd_enabled:
                continue

            # Check missing usage (show hint from first match)
            usage = parser.get_missing_usage(text)
            if usage and first_missing_usage is None:
                first_missing_usage = usage

            # Get sender's bound MC name
            result = parser.match(text)
            if result:
                command, _ = result
                # 自定义指令同样受白名单约束（与 /mc cmd 一致）
                if not self._check_command_allowed(command, config):
                    continue
                matched_command = command
                server = self.server_manager.get_server(server_id)
                if not server or not server.connected:
                    continue

                # Build targets for this server (reuse common method)
                targets = await self._build_server_targets(server)
                all_targets.extend(targets)

        # If no match found but missing usage detected, show hint
        if not all_targets and first_missing_usage:
            return RenderResult(
                f"❌ 参数不足，格式: {first_missing_usage}", is_image=False
            )

        if all_targets and matched_command:
            return await self._execute_or_select_target(
                ctx, all_targets, matched_command, action="custom_cmd"
            )

        return None

    async def handle_help(self, ctx: CommandContext) -> RenderResult:
        """显示帮助信息"""
        help_text = """📖 Minecraft 聊天适配器指令帮助

基础指令:
    /mc help - 显示此帮助信息
    /mc sid - 查看可用的会话 stream_id（用于目标会话配置）
    /mc status - 查看服务器状态
    /mc list - 查看在线玩家列表
    /mc player <玩家ID> - 查看玩家详细信息

远程指令:
    /mc cmd <指令> - 远程执行服务器指令

多服务器:
    status/list/player 会自动输出所有关联服务器结果
    cmd 在多目标下仍需编号选择"""

        # 收集自定义指令列表
        custom_cmds = self._get_custom_command_triggers()
        if custom_cmds:
            help_text += "\n\n自定义指令:\n"
            for trigger in custom_cmds:
                help_text += f"  {trigger}\n"
            help_text = help_text.rstrip("\n")

        return RenderResult(help_text, is_image=False)

    async def handle_status(self, ctx: CommandContext) -> RenderResult:
        """显示服务器状态"""
        all_servers = self._get_session_all_servers(ctx.stream_id)
        if not all_servers:
            return RenderResult(
                "❌ 当前会话未关联任何服务器，请在插件配置中将此会话的 stream_id 添加到服务器的目标会话列表",
                is_image=False,
            )

        online_servers = [s for s in all_servers if s.connected]
        if not online_servers:
            return RenderResult("❌ 当前会话关联的服务器均离线", is_image=False)

        cards: list[tuple[str, object, object]] = []
        errors: list[str] = []
        for server in online_servers:
            server_cards, err = await self._collect_status_cards(server)
            if err:
                errors.append(err)
                continue
            cards.extend(server_cards)

        if not cards:
            if errors:
                return RenderResult("\n".join(errors), is_image=False)
            return RenderResult("❌ 未获取到可用状态数据", is_image=False)

        use_image = any(
            (
                self.get_server_config(s.server_id).text2image
                if self.get_server_config(s.server_id)
                else True
            )
            for s in online_servers
        )
        return await self.renderer.render_multi_server_status(cards, as_image=use_image)

    async def _collect_status_cards(
        self, server
    ) -> tuple[list[tuple[str, object, object]], str]:
        """为单个服务器收集可合并渲染的状态卡片。"""
        server_label = (
            server.server_info.name
            if server.server_info and server.server_info.name
            else server.server_id
        )
        info, err = await server.rest_client.get_server_info()
        if not info:
            return [], f"❌ [{server_label}] 获取服务器信息失败: {err}"

        status, err = await server.rest_client.get_server_status()
        if not status:
            return [], f"❌ [{server_label}] 获取服务器状态失败: {err}"

        cards: list[tuple[str, object, object]] = [(server_label, info, status)]
        if status.is_proxy and status.backends:
            for backend in status.backends:
                backend_info = SimpleNamespace(
                    name=backend.name,
                    platform=backend.platform,
                    minecraft_version=backend.version,
                    online_count=backend.online_players,
                    max_players=backend.max_players,
                    uptime_formatted=backend.uptime_formatted,
                    is_proxy=False,
                    aggregate_online=0,
                    aggregate_max=0,
                )
                backend_status = SimpleNamespace(
                    is_proxy=False,
                    online_players=backend.online_players,
                    max_players=backend.max_players,
                    uptime_formatted=backend.uptime_formatted,
                    tps_1m=backend.tps_1m,
                    tps_5m=backend.tps_5m,
                    tps_15m=backend.tps_15m,
                    memory_used=backend.memory_used,
                    memory_max=backend.memory_max,
                    memory_usage_percent=backend.memory_usage_percent,
                    worlds=[],
                    backends=[],
                )
                cards.append(
                    (f"{server_label}/{backend.name}", backend_info, backend_status)
                )

        return cards, ""

    async def _do_status(self, ctx: CommandContext, server) -> RenderResult:
        """Execute status query on a resolved server"""
        cards, err = await self._collect_status_cards(server)
        if err:
            return RenderResult(err, is_image=False)
        config = self.get_server_config(server.server_id)
        use_image = config.text2image if config else True
        return await self.renderer.render_multi_server_status(cards, as_image=use_image)

    async def handle_list(self, ctx: CommandContext) -> RenderResult:
        """显示在线玩家列表"""
        all_servers = self._get_session_all_servers(ctx.stream_id)
        if not all_servers:
            return RenderResult(
                "❌ 当前会话未关联任何服务器，请在插件配置中将此会话的 stream_id 添加到服务器的目标会话列表",
                is_image=False,
            )

        online_servers = [s for s in all_servers if s.connected]
        if not online_servers:
            return RenderResult("❌ 当前会话关联的服务器均离线", is_image=False)

        cards: list[tuple[str, list, int, str]] = []
        errors: list[str] = []
        for server in online_servers:
            server_cards, err = await self._collect_list_cards(server)
            if err:
                errors.append(err)
                continue
            cards.extend(server_cards)

        if not cards:
            if errors:
                return RenderResult("\n".join(errors), is_image=False)
            return RenderResult("❌ 未获取到可用玩家列表", is_image=False)

        use_image = any(
            (
                self.get_server_config(s.server_id).text2image
                if self.get_server_config(s.server_id)
                else True
            )
            for s in online_servers
        )
        return await self.renderer.render_multi_player_list(cards, as_image=use_image)

    async def _do_list(self, ctx: CommandContext, server) -> RenderResult:
        """Execute player list query on a resolved server"""
        cards, err = await self._collect_list_cards(server)
        if err:
            return RenderResult(err, is_image=False)

        config = self.get_server_config(server.server_id)
        use_image = config.text2image if config else True

        return await self.renderer.render_multi_player_list(cards, as_image=use_image)

    async def handle_player(self, ctx: CommandContext, player_id: str) -> RenderResult:
        """显示玩家详细信息"""
        if not player_id:
            return RenderResult("❌ 请指定玩家ID", is_image=False)

        all_servers = self._get_session_all_servers(ctx.stream_id)
        if not all_servers:
            return RenderResult(
                "❌ 当前会话未关联任何服务器，请在插件配置中将此会话的 stream_id 添加到服务器的目标会话列表",
                is_image=False,
            )

        online_servers = [s for s in all_servers if s.connected]
        if not online_servers:
            return RenderResult("❌ 当前会话关联的服务器均离线", is_image=False)

        cards: list[tuple[str, object]] = []
        for server in online_servers:
            player, _ = await server.rest_client.get_player_by_name(player_id)
            if not player:
                continue
            player_server_name = await self._resolve_player_card_server_name(
                server, player
            )
            cards.append((player_server_name, player))

        if cards:
            use_image = any(
                (
                    self.get_server_config(s.server_id).text2image
                    if self.get_server_config(s.server_id)
                    else True
                )
                for s in online_servers
            )
            return await self.renderer.render_multi_player_detail(
                cards, as_image=use_image
            )

        return RenderResult("❌ 玩家在所有在线服务器中均无数据", is_image=False)

    async def _do_player(
        self, ctx: CommandContext, server, player_id: str
    ) -> RenderResult:
        """Execute player detail query on a resolved server"""
        server_label = self._server_label(server)
        player, err = await server.rest_client.get_player_by_name(player_id)
        if not player:
            return RenderResult(
                f"❌ [{server_label}] 获取玩家信息失败: {err}", is_image=False
            )

        config = self.get_server_config(server.server_id)
        use_image = config.text2image if config else True
        player_server_name = await self._resolve_player_card_server_name(server, player)

        return await self.renderer.render_player_detail(
            player, server_tag=player_server_name, as_image=use_image
        )

    async def handle_cmd(self, ctx: CommandContext, command: str) -> RenderResult:
        """执行远程命令

        流程: 构建目标列表(含proxy展开) → cmd_enabled/黑白名单检查 → 目标选择 → 执行
        """
        if not command:
            return RenderResult("❌ 请指定要执行的指令", is_image=False)

        servers = self._get_session_servers(ctx.stream_id)
        if not servers:
            return RenderResult(
                "❌ 当前会话未关联任何服务器，请在插件配置中将此会话的 stream_id 添加到服务器的目标会话列表",
                is_image=False,
            )

        # Build unified target list across all servers
        all_targets = await self._build_all_cmd_targets(servers)
        if not all_targets:
            return RenderResult("❌ 没有可用的执行目标", is_image=False)

        # Per-target auth checks: may differ by server config
        allowed_targets: list[CmdTarget] = []
        first_deny_message: str | None = None
        for target in all_targets:
            allowed, deny_message = self._is_cmd_allowed_on_server(
                command, target.server
            )
            if allowed:
                allowed_targets.append(target)
            elif first_deny_message is None:
                first_deny_message = deny_message

        if not allowed_targets:
            return RenderResult(
                first_deny_message or "❌ 没有可用的执行目标", is_image=False
            )

        return await self._execute_or_select_target(
            ctx, allowed_targets, command, action="cmd"
        )

    async def _dispatch_server_action(
        self, ctx: CommandContext, action: str, server, args: dict
    ) -> RenderResult:
        """Dispatch non-cmd pending actions to concrete executors."""
        if action == "status":
            return await self._do_status(ctx, server)

        if action == "list":
            return await self._do_list(ctx, server)

        if action == "player":
            return await self._do_player(ctx, server, args.get("player_id", ""))

        return RenderResult("❌ 未知操作", is_image=False)

    def _is_cmd_allowed_on_server(self, command: str, server) -> tuple[bool, str]:
        """Check cmd switch + whitelist/blacklist against the target server config."""
        config = self.get_server_config(server.server_id)
        if not config or not config.cmd_enabled:
            return False, "❌ 远程指令功能未启用"

        if not self._check_command_allowed(command, config):
            return False, "❌ 此指令不在允许列表中"

        return True, ""

    async def _build_all_cmd_targets(self, servers: list) -> list[CmdTarget]:
        """Build unified target list across all servers, expanding proxies."""
        all_targets: list[CmdTarget] = []
        for server in servers:
            targets = await self._build_server_targets(server)
            all_targets.extend(targets)
        return all_targets

    async def _execute_or_select_target(
        self,
        ctx: CommandContext,
        targets: list[CmdTarget],
        command: str,
        action: str = "cmd",
    ) -> RenderResult:
        """Execute directly if single target, otherwise prompt user to select."""
        if not targets:
            return RenderResult("❌ 没有可用的执行目标", is_image=False)

        if len(targets) == 1:
            t = targets[0]
            return await self._do_cmd(
                ctx, t.server, command, target_server=t.target_server
            )

        # Multiple targets: prompt user to select
        choices = self._format_target_choices(targets)
        self._pending_actions[ctx.stream_id] = PendingAction(
            action=action,
            args={"command": command},
            cmd_targets=targets,
            timestamp=time.time(),
        )
        return RenderResult(f"⚠️ 请选择执行目标:\n{choices}", is_image=False)

    async def _do_cmd(
        self,
        ctx: CommandContext,
        server,
        command: str,
        target_server: str | None = None,
    ) -> RenderResult:
        """Pure command executor — sends command to server and returns result."""
        success, output, _ = await server.rest_client.execute_command(
            command, target_server=target_server
        )

        target_label = f" [{target_server}]" if target_server else ""
        if success:
            return RenderResult(
                f"✅{target_label} 指令执行成功\n{output}", is_image=False
            )
        return RenderResult(f"❌{target_label} 指令执行失败: {output}", is_image=False)

    def _get_custom_command_triggers(self) -> list[str]:
        """获取所有服务器的自定义命令触发词列表（去重）"""
        triggers = []
        seen: set[str] = set()
        for server_id in self._custom_parsers:
            config = self.get_server_config(server_id)
            if not config or not config.custom_cmd_list:
                continue
            for mapping_str in config.custom_cmd_list:
                if CustomCommandParser.SEPARATOR in mapping_str:
                    trigger_part = mapping_str.split(CustomCommandParser.SEPARATOR, 1)[
                        0
                    ].strip()
                    if trigger_part not in seen:
                        seen.add(trigger_part)
                        triggers.append(trigger_part)
        return triggers

    def _get_session_servers(self, stream_id: str) -> list:
        if not stream_id:
            return []
        servers = []
        for server in self.server_manager.get_connected_servers():
            config = self.get_server_config(server.server_id)
            if (
                config
                and config.target_sessions
                and stream_id in config.target_sessions
            ):
                servers.append(server)
        return servers

    def _get_session_all_servers(self, stream_id: str) -> list:
        """获取会话关联的全部服务器（含离线）。"""
        if not stream_id:
            return []
        servers = []
        for server in self.server_manager.get_all_servers().values():
            config = self.get_server_config(server.server_id)
            if (
                config
                and config.target_sessions
                and stream_id in config.target_sessions
            ):
                servers.append(server)
        return servers

    @staticmethod
    def _server_label(server) -> str:
        return (
            server.server_info.name
            if server.server_info and server.server_info.name
            else server.server_id
        )

    @staticmethod
    def _is_proxy_like_name(name: str) -> bool:
        n = (name or "").strip().lower()
        if not n:
            return False
        if n in {"vc", "velocity", "proxy", "bungeecord", "waterfall"}:
            return True
        return any(k in n for k in ("velocity", "proxy", "bungee", "waterfall", "vc"))

    async def _collect_list_cards(
        self, server
    ) -> tuple[list[tuple[str, list, int, str]], str]:
        """将代理服后端映射为独立服务器卡片，和普通独立服同层级返回。"""
        server_label = self._server_label(server)
        players, total, err = await server.rest_client.get_players()
        if err:
            return [], f"❌ [{server_label}] 获取玩家列表失败: {err}"
        if total == 0 and players:
            total = len(players)

        status, _ = await server.rest_client.get_server_status()
        if not status or not status.is_proxy or not status.backends:
            return [(server_label, players, total, server_label)], ""

        grouped: dict[str, list] = {}
        unknown_players: list = []
        for p in players:
            backend = (getattr(p, "server", "") or "").strip()
            if backend:
                grouped.setdefault(backend, []).append(p)
            else:
                unknown_players.append(p)

        cards: list[tuple[str, list, int, str]] = []
        for backend in status.backends:
            backend_name = (backend.name or "").strip() or "未命名后端"
            backend_players = grouped.pop(backend_name, [])
            backend_total = (
                backend.online_players
                if backend.online_players > 0
                else len(backend_players)
            )
            cards.append((backend_name, backend_players, backend_total, backend_name))

        # 兜底：处理状态未上报但玩家数据里出现的后端名
        for extra_backend, extra_players in grouped.items():
            cards.append(
                (extra_backend, extra_players, len(extra_players), extra_backend)
            )

        if unknown_players:
            cards.append(
                ("未标记子服", unknown_players, len(unknown_players), "未标记子服")
            )

        return cards, ""

    async def _resolve_player_card_server_name(self, server, player) -> str:
        """解析玩家详情卡片展示服务器名：优先后端服，独立服回退主服名。"""
        server_label = self._server_label(server)

        status, _ = await server.rest_client.get_server_status()
        if not status or not status.is_proxy or not status.backends:
            return server_label

        backend_map = {
            (b.name or "").strip().lower(): (b.name or "").strip()
            for b in status.backends
            if (b.name or "").strip()
        }

        candidate = (getattr(player, "server", "") or "").strip()
        if candidate and candidate.lower() in backend_map:
            return backend_map[candidate.lower()]

        # 代理服场景下，玩家详情可能缺少server，回查players接口补齐
        players, _, _ = await server.rest_client.get_players()
        target_uuid = (getattr(player, "uuid", "") or "").strip().lower()
        target_name = (getattr(player, "name", "") or "").strip().lower()
        for p in players:
            puid = (getattr(p, "uuid", "") or "").strip().lower()
            pname = (getattr(p, "name", "") or "").strip().lower()
            if (target_uuid and puid == target_uuid) or (
                target_name and pname == target_name
            ):
                pserver = (getattr(p, "server", "") or "").strip()
                if pserver and pserver.lower() in backend_map:
                    return backend_map[pserver.lower()]
                if pserver and not self._is_proxy_like_name(pserver):
                    return pserver
                break

        # 不回退代理层名称，保持为空，交给渲染层显示“未提供”
        if candidate and not self._is_proxy_like_name(candidate):
            return candidate
        return ""

    def _format_server_choices(self, servers: list) -> str:
        lines = []
        for idx, server in enumerate(servers, start=1):
            name = (
                server.server_info.name
                if server.server_info and server.server_info.name
                else ""
            )
            name_part = f" ({name})" if name else ""
            lines.append(f"{idx}. {server.server_id}{name_part}")
        return "\n".join(lines)

    async def _server_is_proxy(self, server) -> bool:
        """Check if a server is in proxy (Velocity) mode by querying server info"""
        if server.server_info and server.server_info.is_proxy:
            return True
        info, _ = await server.rest_client.get_server_info()
        return info is not None and info.is_proxy

    async def _build_server_targets(self, server) -> list[CmdTarget]:
        """Build target list for a single server, auto-detecting proxy mode.

        For proxy servers: returns [proxy, backend1, backend2, ...]
        For standalone servers: returns [server]
        """
        if await self._server_is_proxy(server):
            return await self._build_proxy_targets(server)

        name = (
            server.server_info.name
            if server.server_info and server.server_info.name
            else server.server_id
        )
        return [CmdTarget(label=name, server=server, target_server=None)]

    async def _build_proxy_targets(self, server) -> list[CmdTarget]:
        """Build target list for a proxy server: [proxy itself, backend1, backend2, ...]"""
        info, _ = await server.rest_client.get_server_info()
        targets: list[CmdTarget] = []
        # Proxy itself is always a valid target
        proxy_label = info.name if info and info.name else server.server_id
        targets.append(
            CmdTarget(
                label=f"{proxy_label} (代理端)", server=server, target_server=None
            )
        )
        # Add backends
        if info and info.backends:
            for b in info.backends:
                if b.name:
                    targets.append(
                        CmdTarget(label=b.name, server=server, target_server=b.name)
                    )
        return targets

    def _format_target_choices(self, targets: list[CmdTarget]) -> str:
        """Format cmd target choices for user selection"""
        lines = []
        for idx, t in enumerate(targets, start=1):
            server_id = t.server.server_id if t.server else ""
            # Show server_id as context if label differs from server_id
            if t.label and t.label != server_id:
                lines.append(f"{idx}. {t.label} [{server_id}]")
            else:
                lines.append(f"{idx}. {t.label}")
        return "\n".join(lines)

    def _resolve_server_or_pending(
        self,
        stream_id: str,
        action: str = "",
        args: dict | None = None,
    ) -> tuple[object | None, str]:
        """Resolve the target server for a command.

        If only one server is associated, return it directly.
        If multiple servers are associated, create a pending action and
        return the server choice prompt. Returns (None, prompt_msg) when pending.
        Returns (None, error_msg) on error.
        Returns (server, "") on success.
        """
        servers = self._get_session_servers(stream_id)
        if not servers:
            return (
                None,
                "❌ 当前会话未关联任何服务器，请在插件配置中将此会话的 stream_id 添加到服务器的目标会话列表",
            )

        if len(servers) == 1:
            return servers[0], ""

        # Multiple servers: create pending action and return prompt
        choices = self._format_server_choices(servers)
        self._pending_actions[stream_id] = PendingAction(
            action=action,
            args=args or {},
            servers=servers,
            timestamp=time.time(),
        )
        return (
            None,
            f"⚠️ 当前会话关联多个服务器，请发送编号选择:\n{choices}",
        )

    def _check_command_allowed(self, command: str, config) -> bool:
        """检查命令是否在白名单/黑名单中允许"""
        parts = command.split()
        if not parts:
            return False
        cmd_name = parts[0].lower()

        cmd_list = [c.lower() for c in config.cmd_list]
        list_mode = (config.cmd_white_black_list or "white").lower()

        if list_mode == "none":
            return True

        if list_mode == "white":
            return cmd_name in cmd_list

        if list_mode == "black":
            return cmd_name not in cmd_list

        return cmd_name in cmd_list
