"""MaiBot Plugin: MinecraftAdapter — Minecraft 服务器适配器

从 AstrBot 插件 astrbot_plugin_minecraft_adapter 迁移（AGPL-3.0）。

功能：
- AI 聊天：游戏内玩家与 bot 对话（直连 ctx.llm.generate + 全局人格注入）
- 消息互通：MC 服务器 ↔ 外部会话（ctx.send.text + chat.receive.after_process Hook）
- 服务器管理：/mc status|list|player|cmd（@Command + PIL 渲染图）

关键差异（相对 AstrBot 版）：
- 平台适配器（Platform 基类 + 事件队列）→ 移除，AI 聊天改直连 LLM
- 目标会话：AstrBot UMO → MaiBot stream_id（/mc sid 命令可查）
- 入站消息监听：@filter 事件 → @HookHandler("chat.receive.after_process")
- 存储：get_astrbot_data_path() → ctx.paths.data_dir / runtime_dir
"""

import base64
import logging
from typing import Any, Literal

from maibot_sdk import (
    Command,
    Field,
    HookHandler,
    MaiBotPlugin,
    PluginConfigBase,
)
from maibot_sdk.types import HookMode, HookOrder

from .core.models import MCMessage, MessageType, ServerConfig, ServerInfo
from .core.server_manager import ServerManager
from .handlers.commands import CommandContext, CommandHandler
from .services.ai_chat import AIChatService
from .services.message_bridge import MessageBridge
from .services.renderer import InfoRenderer, RenderResult

logger = logging.getLogger(__name__)


# ============ 配置模型 ============


class ServerConnectionConfig(PluginConfigBase):
    """服务器连接信息"""

    __ui_label__ = "服务器连接"

    server_id: str = Field(default="my_server", description="服务器ID（唯一标识）")
    host: str = Field(
        default="localhost",
        description="服务器地址（AstrBotAdapter 所在服务器 IP/域名）",
    )
    port: int = Field(default=8765, description="服务器端口（默认 8765）")
    token: str = Field(
        default="", description="认证 Token（从 AstrBotAdapter 配置获取）"
    )


class MessageForwardConfig(PluginConfigBase):
    """消息转发配置"""

    __ui_label__ = "消息转发"

    forward_chat_to_astrbot: bool = Field(
        default=True, description="转发 MC 聊天消息到目标会话"
    )
    forward_chat_format: str = Field(
        default="<{player}> {message}",
        description="聊天消息格式（{player} 玩家名，{message} 消息内容）",
    )
    forward_join_leave_to_astrbot: bool = Field(
        default=False, description="转发玩家进出消息"
    )
    target_sessions: list = Field(
        default_factory=list,
        description="目标会话 stream_id 列表（用 /mc sid 命令查看）",
    )
    auto_forward_prefix: str = Field(
        default="*", description="自动转发前缀（外部消息以此开头才转发，留空转发全部）"
    )
    mark_option: Literal["text", "none"] = Field(
        default="text", description="转发成功提醒方式（text=文本提醒，none=不提醒）"
    )


class CmdConfig(PluginConfigBase):
    """远程指令配置"""

    __ui_label__ = "远程指令"

    enabled: bool = Field(default=True, description="启用远程执行指令")
    cmd_white_black_list: Literal["white", "black", "none"] = Field(
        default="white",
        description="指令名单类型（white=仅允许名单内，black=禁止名单内，none=不启用）",
    )
    cmd_list: list = Field(
        default_factory=lambda: ["say", "list", "weather", "time"],
        description="指令名单（填指令名，不带 /）",
    )
    custom_cmd_list: list = Field(
        default_factory=list,
        description="自定义指令映射（格式：触发词 <&参数&><<>>实际指令；实际指令名需在 cmd_list 白名单内）",
    )


class McServerConfig(PluginConfigBase):
    """单个 MC 服务器"""

    __ui_label__ = "MC 服务器"

    enabled: bool = Field(default=True, description="启用此服务器")
    server: ServerConnectionConfig = Field(
        default_factory=ServerConnectionConfig, description="服务器连接信息"
    )
    enable_ai_chat: bool = Field(
        default=True, description="启用 AI 对话（游戏内和 bot 聊天）"
    )
    text2image: bool = Field(default=True, description="服务器信息渲染为图片输出")
    message: MessageForwardConfig = Field(
        default_factory=MessageForwardConfig, description="消息转发配置"
    )
    cmd: CmdConfig = Field(default_factory=CmdConfig, description="远程指令配置")


class PluginBaseConfig(PluginConfigBase):
    """插件基础配置（MaiBot 运行时要求的 [plugin] 配置节）"""

    __ui_label__ = "插件基础设置"

    config_version: str = Field(default="1.0.0", description="配置版本号")
    enabled: bool = Field(default=True, description="是否启用插件")


class MinecraftAdapterConfig(PluginConfigBase):
    """插件完整配置"""

    __ui_label__ = "Minecraft 聊天适配器"

    plugin: PluginBaseConfig = Field(
        default_factory=PluginBaseConfig, description="插件基础配置"
    )
    enabled: bool = Field(default=True, description="启用 Minecraft 聊天适配器")
    mc_servers: list[McServerConfig] = Field(
        default_factory=list, description="MC 服务器列表"
    )


# ============ 插件主类 ============


class MinecraftAdapterPlugin(MaiBotPlugin):
    """Minecraft 聊天适配器插件"""

    config_model = MinecraftAdapterConfig

    def __init__(self) -> None:
        super().__init__()
        self.server_manager = ServerManager()
        self.message_bridge: MessageBridge | None = None
        self.ai_chat: AIChatService | None = None
        self.renderer: InfoRenderer | None = None
        self.command_handler: CommandHandler | None = None
        self._server_configs: dict[str, ServerConfig] = {}
        self._running = False

    # ── 生命周期 ────────────────────────────────────────

    async def on_load(self) -> None:
        data_dir = self.ctx.paths.data_dir
        runtime_dir = self.ctx.paths.runtime_dir
        data_dir.mkdir(parents=True, exist_ok=True)

        # 解析服务器配置
        for server_model in self.config.mc_servers:
            config = ServerConfig.from_dict(server_model.model_dump())
            if not config.enabled:
                logger.info(
                    f"[MC Adapter] 跳过已禁用的服务器: {config.server_id or '未命名'}"
                )
                continue
            if not config.server_id:
                logger.warning("[MC Adapter] 跳过 ID 为空的服务器")
                continue
            self._server_configs[config.server_id] = config
            self.server_manager.add_server(config)
            logger.info(f"[MC Adapter] 已配置服务器: {config.server_id}")

        # 渲染器
        any_text2image = any(c.text2image for c in self._server_configs.values())
        self.renderer = InfoRenderer(
            text2image_enabled=any_text2image,
            cache_dir=runtime_dir / "renderer_cache",
            data_dir=data_dir,
        )

        # 消息桥接（注入 send_text 回调，解耦 ctx）
        self.message_bridge = MessageBridge(
            self.server_manager,
            self._server_configs,
            send_text=self._send_text,
        )

        # AI 聊天
        self.ai_chat = AIChatService(self)

        # 命令处理器
        self.command_handler = CommandHandler(
            server_manager=self.server_manager,
            renderer=self.renderer,
            get_server_config=lambda sid: self._server_configs.get(sid),
        )
        for server_id, config in self._server_configs.items():
            if config.custom_cmd_list:
                self.command_handler.register_custom_commands(
                    server_id, config.custom_cmd_list
                )

        # 设置消息处理器
        self.server_manager.set_message_handler(self._on_server_message)
        self.server_manager.set_connect_handler(self._on_server_connect)
        self.server_manager.set_disconnect_handler(self._on_server_disconnect)

        # 启动服务器连接
        if self.config.enabled:
            await self.server_manager.start_all()
        self._running = True
        logger.info(
            f"[MC Adapter] 插件已加载，配置了 {len(self._server_configs)} 个服务器"
        )

    async def on_unload(self) -> None:
        self._running = False
        await self.server_manager.stop_all()
        logger.info("[MC Adapter] 插件已卸载")

    async def on_config_update(
        self, scope: str, config_data: dict[str, Any], version: str
    ) -> None:
        if scope != "self":
            return
        # 服务器连接/命令配置变更需重启 Runner 生效（子进程内部状态复杂，不做热迁移）
        logger.info("[MC Adapter] 配置已更新（重启插件后生效）")

    # ── 服务器回调 ──────────────────────────────────────

    async def _on_server_message(self, server_id: str, msg: MCMessage):
        config = self._server_configs.get(server_id)
        if not config:
            return

        if msg.type == MessageType.CHAT_REQUEST:
            if config.enable_ai_chat:
                server = self.server_manager.get_server(server_id)
                if server and self.ai_chat:
                    await self.ai_chat.handle_chat_request(server_id, server, msg)
        elif (
            msg.type
            in (
                MessageType.MESSAGE_FORWARD,
                MessageType.PLAYER_JOIN,
                MessageType.PLAYER_QUIT,
            )
            and self.message_bridge
        ):
            await self.message_bridge.handle_mc_message(server_id, msg)

    async def _on_server_connect(self, server_id: str, info: ServerInfo):
        logger.info(
            f"[MC-{server_id}] 已连接到 {info.name} "
            f"({info.platform} {info.minecraft_version})"
        )

    async def _on_server_disconnect(self, server_id: str, reason: str):
        logger.warning(f"[MC-{server_id}] 已断开连接: {reason}")

    # ── 发送辅助 ────────────────────────────────────────

    async def _send_text(self, text: str, stream_id: str) -> bool:
        """发送文本到指定聊天流（注入给 MessageBridge）。"""
        if not text or not stream_id:
            return False
        try:
            result = await self.ctx.send.text(text, stream_id)
            return bool(result)
        except Exception as e:
            logger.warning(f"[MC Adapter] 发送文本失败: {e}")
            return False

    async def _send_result(self, result: RenderResult, stream_id: str):
        """把命令结果（文本或图片）发送到当前会话。"""
        if result.is_image:
            b64 = base64.b64encode(result.image.getvalue()).decode("utf-8")
            await self.ctx.send.image(b64, stream_id)
        else:
            await self.ctx.send.text(result.text, stream_id)

    @staticmethod
    def _build_context(kwargs: dict[str, Any]) -> CommandContext:
        return CommandContext(
            stream_id=str(kwargs.get("stream_id", "") or ""),
            platform=str(kwargs.get("platform", "") or ""),
            user_id=str(kwargs.get("user_id", "") or ""),
        )

    # ── @Command：mc 命令组 ─────────────────────────────

    @Command(
        "mc_help",
        description="显示 Minecraft 聊天适配器帮助",
        pattern=r"^/mc(?:\s+help)?$",
    )
    async def handle_mc_help(self, **kwargs):
        if not self.command_handler:
            return False, "未初始化", 1
        ctx = self._build_context(kwargs)
        result = await self.command_handler.handle_help(ctx)
        await self._send_result(result, ctx.stream_id)
        return True, "帮助已发送", 2

    @Command(
        "mc_sid",
        description="查看可用的会话 stream_id",
        pattern=r"^/mc\s+sid$",
        permission="operator",
    )
    async def handle_mc_sid(self, **kwargs):
        stream_id = str(kwargs.get("stream_id", "") or "")
        try:
            streams = await self.ctx.chat.get_all_streams(platform="all_platforms")
            streams = streams or []
        except Exception as e:
            await self.ctx.send.text(f"❌ 获取会话列表失败: {e}", stream_id)
            return True, "获取失败", 2

        if not streams:
            await self.ctx.send.text(
                "📋 当前没有可用的聊天流。请先在目标群/私聊里和 bot 说过话。", stream_id
            )
            return True, "无会话", 2

        lines = ["📋 可用会话（把 stream_id 填进插件配置的 target_sessions）:"]
        for s in streams:
            if not isinstance(s, dict):
                continue
            sid = s.get("stream_id") or s.get("session_id") or ""
            platform = s.get("platform", "")
            if s.get("is_group_session") or s.get("chat_type") == "group":
                name = s.get("group_name") or "未命名群"
                kind = "群"
            else:
                name = f"{s.get('user_nickname') or '未知用户'}的私聊"
                kind = "私聊"
            lines.append(f"  [{name}] 平台={platform} ({kind})")
            lines.append(f"    stream_id = {sid}")
        await self.ctx.send.text("\n".join(lines), stream_id)
        return True, "会话列表已发送", 2

    @Command("mc_status", description="查看服务器状态", pattern=r"^/mc\s+status$")
    async def handle_mc_status(self, **kwargs):
        if not self.command_handler:
            return False, "未初始化", 1
        ctx = self._build_context(kwargs)
        result = await self.command_handler.handle_status(ctx)
        await self._send_result(result, ctx.stream_id)
        return True, "状态已发送", 2

    @Command("mc_list", description="查看在线玩家列表", pattern=r"^/mc\s+list$")
    async def handle_mc_list(self, **kwargs):
        if not self.command_handler:
            return False, "未初始化", 1
        ctx = self._build_context(kwargs)
        result = await self.command_handler.handle_list(ctx)
        await self._send_result(result, ctx.stream_id)
        return True, "列表已发送", 2

    @Command(
        "mc_player",
        description="查看玩家详细信息",
        pattern=r"^/mc\s+player(?:\s+(?P<player_id>\S+))?$",
    )
    async def handle_mc_player(self, **kwargs):
        if not self.command_handler:
            return False, "未初始化", 1
        ctx = self._build_context(kwargs)
        matched = kwargs.get("matched_groups") or {}
        player_id = (matched.get("player_id") or "").strip()
        result = await self.command_handler.handle_player(ctx, player_id)
        await self._send_result(result, ctx.stream_id)
        return True, "玩家信息已发送", 2

    @Command(
        "mc_cmd",
        description="远程执行服务器指令",
        pattern=r"^/mc\s+cmd\s+(?P<command>.+)$",
        permission="operator",
    )
    async def handle_mc_cmd(self, **kwargs):
        if not self.command_handler:
            return False, "未初始化", 1
        ctx = self._build_context(kwargs)
        matched = kwargs.get("matched_groups") or {}
        command = (matched.get("command") or "").strip()
        result = await self.command_handler.handle_cmd(ctx, command)
        await self._send_result(result, ctx.stream_id)
        return True, "指令已执行", 2

    # ── @HookHandler：入站消息观察（转发/编号选择/自定义指令）──

    @HookHandler(
        "chat.receive.after_process",
        name="mc_message_observer",
        description="观察入站消息，处理编号选择、自定义指令与消息转发",
        mode=HookMode.BLOCKING,
        order=HookOrder.EARLY,
    )
    async def on_incoming_message(self, message=None, hook_name="", **kwargs):
        if not isinstance(message, dict):
            return {"action": "continue"}

        stream_id = str(message.get("session_id") or "")
        platform = str(message.get("platform") or "")
        text = str(message.get("processed_plain_text") or "").strip()
        if not stream_id or not text:
            return {"action": "continue"}

        # 跳过命令消息（/mc xxx 由 @Command 处理，也避免把命令转发到 MC）
        if text.startswith("/"):
            return {"action": "continue"}

        message_info = message.get("message_info") or {}
        user_info = message_info.get("user_info") or {}
        user_id = str(user_info.get("user_id") or "")
        user_name = str(user_info.get("user_nickname") or user_id)

        ctx = CommandContext(stream_id=stream_id, platform=platform, user_id=user_id)

        # 1. 编号选择（多服务器/多后端待选）
        if (
            self.command_handler
            and text.isdigit()
            and self.command_handler.has_pending_action(stream_id)
        ):
            result = await self.command_handler.handle_number_selection(ctx, text)
            if result.text:
                await self._send_result(result, stream_id)
            return {"action": "abort"}

        # 2. 自定义指令匹配
        if self.command_handler:
            result = await self.command_handler.handle_custom_command(ctx, text)
            if result is not None:
                await self._send_result(result, stream_id)
                return {"action": "abort"}

        # 3. 消息转发到 MC
        if self.message_bridge:
            forwarded = await self.message_bridge.handle_external_message(
                stream_id=stream_id,
                platform=platform,
                user_id=user_id,
                user_name=user_name,
                content=text,
            )
            if forwarded:
                return {"action": "abort"}

        return {"action": "continue"}


def create_plugin():
    return MinecraftAdapterPlugin()
