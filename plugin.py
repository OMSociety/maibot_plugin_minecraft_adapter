"""MaiBot Plugin: MinecraftAdapter — Minecraft 服务器适配器

从 AstrBot 插件 astrbot_plugin_minecraft_adapter 迁移（AGPL-3.0）。

功能：
- AI 聊天：游戏内玩家与 bot 对话（直连 ctx.llm.generate + 全局人格注入）
- 消息互通：MC 服务器 ↔ 外部会话（ctx.send.text + chat.receive.after_process Hook）
- 服务器管理：/mc status|list|player|cmd（@Command + PIL 渲染图）

关键差异（相对 AstrBot 版）：
- 平台适配器（Platform 基类 + 事件队列）→ 移除，AI 聊天改直连 LLM
- 目标会话：AstrBot UMO → MaiBot Session ID（在 MaiBot WebUI『聊天管理』查看）
- 入站消息监听：@filter 事件 → @HookHandler("chat.receive.after_process")
- 存储：get_astrbot_data_path() → ctx.paths.data_dir / runtime_dir
"""

import base64
import logging
from typing import Any, ClassVar, Literal

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
from .handlers.commands import (
    BindingHandler,
    CommandContext,
    CommandHandler,
    is_operator_match,
)
from .services.ai_chat import AIChatService
from .services.message_bridge import MessageBridge
from .services.renderer import InfoRenderer, RenderResult

logger = logging.getLogger(__name__)


# ============ 配置模型 ============


class McServerConfig(PluginConfigBase):
    """单个 MC 服务器"""

    __ui_label__ = "MC 服务器"

    __ui_i18n__: ClassVar[dict[str, dict[str, str]]] = {
        "en-US": {
            "title": "MC Server",
            "description": "A single MC server",
        },
        "ja-JP": {
            "title": "MC サーバー",
            "description": "個々の MC サーバー",
        },
    }

    enabled: bool = Field(
        default=True,
        description="启用此服务器",
        json_schema_extra={
            "label": "启用此服务器",
            "i18n": {
                "en-US": {
                    "label": "Enable this server",
                    "hint": "Enable this server",
                },
                "ja-JP": {
                    "label": "このサーバーを有効化",
                    "hint": "このサーバーを有効化",
                },
            },
        },
    )
    # 服务器连接信息
    server_id: str = Field(
        default="my_server",
        description="服务器ID（唯一标识）",
        json_schema_extra={
            "label": "服务器 ID",
            "placeholder": "例如 my_server",
            "i18n": {
                "en-US": {
                    "label": "Server ID",
                    "hint": "Server ID (unique identifier)",
                    "placeholder": "e.g. my_server",
                },
                "ja-JP": {
                    "label": "サーバー ID",
                    "hint": "サーバー ID（一意の識別子）",
                    "placeholder": "例: my_server",
                },
            },
        },
    )
    host: str = Field(
        default="localhost",
        description="服务器地址（AstrBotAdapter 所在服务器 IP/域名）",
        json_schema_extra={
            "label": "服务器地址",
            "placeholder": "例如 192.168.1.10",
            "i18n": {
                "en-US": {
                    "label": "Server address",
                    "hint": "Server address (IP/domain of the server running AstrBotAdapter)",
                    "placeholder": "e.g. 192.168.1.10",
                },
                "ja-JP": {
                    "label": "サーバーアドレス",
                    "hint": "サーバーアドレス（AstrBotAdapter が動作するサーバーの IP / ドメイン）",
                    "placeholder": "例: 192.168.1.10",
                },
            },
        },
    )
    port: int = Field(
        default=8765,
        description="服务器端口（默认 8765）",
        json_schema_extra={
            "label": "端口",
            "placeholder": "8765",
            "i18n": {
                "en-US": {
                    "label": "Port",
                    "hint": "Server port (default 8765)",
                    "placeholder": "8765",
                },
                "ja-JP": {
                    "label": "ポート",
                    "hint": "サーバーポート（デフォルト 8765）",
                    "placeholder": "8765",
                },
            },
        },
    )
    token: str = Field(
        default="",
        description="认证 Token（从 AstrBotAdapter 配置获取）",
        json_schema_extra={
            "label": "认证 Token",
            "placeholder": "AstrBotAdapter 的 token",
            "i18n": {
                "en-US": {
                    "label": "Auth Token",
                    "hint": "Auth Token (get it from the AstrBotAdapter config)",
                    "placeholder": "Token of AstrBotAdapter",
                },
                "ja-JP": {
                    "label": "認証トークン",
                    "hint": "認証トークン（AstrBotAdapter の設定から取得）",
                    "placeholder": "AstrBotAdapter の token",
                },
            },
        },
    )
    # AI 对话 / 渲染
    enable_ai_chat: bool = Field(
        default=True,
        description="启用 AI 对话（游戏内和 bot 聊天）",
        json_schema_extra={
            "label": "启用 AI 对话",
            "i18n": {
                "en-US": {
                    "label": "Enable AI chat",
                    "hint": "Enable AI chat (players chat with the bot in-game)",
                },
                "ja-JP": {
                    "label": "AI チャットを有効化",
                    "hint": "AI チャットを有効化（ゲーム内で bot と会話）",
                },
            },
        },
    )
    text2image: bool = Field(
        default=True,
        description="服务器信息渲染为图片输出",
        json_schema_extra={
            "label": "渲染为图片",
            "i18n": {
                "en-US": {
                    "label": "Render as image",
                    "hint": "Render server info as an image",
                },
                "ja-JP": {
                    "label": "画像レンダリング",
                    "hint": "サーバー情報を画像として出力",
                },
            },
        },
    )
    # 消息转发配置
    forward_chat_to_astrbot: bool = Field(
        default=True,
        description="转发 MC 聊天消息到目标会话",
        json_schema_extra={
            "label": "转发聊天到目标会话",
            "i18n": {
                "en-US": {
                    "label": "Forward chat to target sessions",
                    "hint": "Forward MC chat messages to target sessions",
                },
                "ja-JP": {
                    "label": "チャットを転送先セッションへ転送",
                    "hint": "MC チャットメッセージを転送先セッションへ転送",
                },
            },
        },
    )
    forward_chat_format: str = Field(
        default="<{player}> {message}",
        description="聊天消息格式（{player} 玩家名，{message} 消息内容）",
        json_schema_extra={
            "label": "聊天消息格式",
            "placeholder": "<{player}> {message}",
            "i18n": {
                "en-US": {
                    "label": "Chat message format",
                    "hint": "Chat message format ({player} player name, {message} message content)",
                    "placeholder": "<{player}> {message}",
                },
                "ja-JP": {
                    "label": "チャットメッセージのフォーマット",
                    "hint": "チャットメッセージのフォーマット（{player} プレイヤー名、{message} メッセージ内容）",
                    "placeholder": "<{player}> {message}",
                },
            },
        },
    )
    forward_join_leave_to_astrbot: bool = Field(
        default=False,
        description="转发玩家进出消息",
        json_schema_extra={
            "label": "转发进出消息",
            "i18n": {
                "en-US": {
                    "label": "Forward join/leave messages",
                    "hint": "Forward player join and leave messages",
                },
                "ja-JP": {
                    "label": "参加・退出メッセージを転送",
                    "hint": "プレイヤーの参加・退出メッセージを転送",
                },
            },
        },
    )
    target_sessions: list[str] = Field(
        default_factory=list,
        description="目标会话 Session ID 列表（在 MaiBot WebUI『聊天管理』查看）",
        json_schema_extra={
            "label": "目标会话列表",
            "i18n": {
                "en-US": {
                    "label": "Target session list",
                    "hint": "List of target Session IDs (see 'Chat Management' in the MaiBot WebUI)",
                },
                "ja-JP": {
                    "label": "転送先セッションリスト",
                    "hint": "転送先セッションの Session ID リスト（MaiBot WebUI の『チャット管理』で確認）",
                },
            },
        },
    )
    auto_forward_prefix: str = Field(
        default="*",
        description="自动转发前缀（外部消息以此开头才转发，留空转发全部）",
        json_schema_extra={
            "label": "自动转发前缀",
            "placeholder": "留空转发全部",
            "i18n": {
                "en-US": {
                    "label": "Auto-forward prefix",
                    "hint": "Auto-forward prefix (only external messages starting with it are forwarded; leave empty to forward all)",
                    "placeholder": "Leave empty to forward all",
                },
                "ja-JP": {
                    "label": "自動転送プレフィックス",
                    "hint": "自動転送プレフィックス（これで始まる外部メッセージのみ転送。空欄ですべて転送）",
                    "placeholder": "空欄ですべて転送",
                },
            },
        },
    )
    mark_option: Literal["text", "none"] = Field(
        default="text",
        description="转发成功提醒方式（text=文本提醒，none=不提醒）",
        json_schema_extra={
            "label": "转发提醒方式",
            "i18n": {
                "en-US": {
                    "label": "Forward notification",
                    "hint": "How to notify when forwarding succeeds (text=text notice, none=no notice)",
                },
                "ja-JP": {
                    "label": "転送通知方式",
                    "hint": "転送成功時の通知方式（text=テキスト通知、none=通知なし）",
                },
            },
        },
    )
    # 远程指令配置
    cmd_enabled: bool = Field(
        default=True,
        description="启用远程执行指令",
        json_schema_extra={
            "label": "启用远程指令",
            "i18n": {
                "en-US": {
                    "label": "Enable remote commands",
                    "hint": "Enable remote command execution",
                },
                "ja-JP": {
                    "label": "リモートコマンドを有効化",
                    "hint": "リモートでのコマンド実行を有効化",
                },
            },
        },
    )
    cmd_white_black_list: Literal["white", "black", "none"] = Field(
        default="white",
        description="指令名单类型（white=仅允许名单内，black=禁止名单内，none=不启用）",
        json_schema_extra={
            "label": "指令名单类型",
            "i18n": {
                "en-US": {
                    "label": "Command list type",
                    "hint": "Command list type (white=allowlist only, black=denylist only, none=disabled)",
                },
                "ja-JP": {
                    "label": "コマンドリストの種類",
                    "hint": "コマンドリストの種類（white=許可リスト内のみ許可、black=拒否リスト内を禁止、none=無効）",
                },
            },
        },
    )
    cmd_list: list[str] = Field(
        default_factory=lambda: ["say", "list", "weather", "time"],
        description="指令名单（填指令名，不带 /）",
        json_schema_extra={
            "label": "指令名单",
            "i18n": {
                "en-US": {
                    "label": "Command list",
                    "hint": "Command list (enter command names, without /)",
                },
                "ja-JP": {
                    "label": "コマンドリスト",
                    "hint": "コマンドリスト（コマンド名を入力、「/」は不要）",
                },
            },
        },
    )
    custom_cmd_list: list[str] = Field(
        default_factory=list,
        description="自定义指令映射（格式：触发词 <&参数&><<>>实际指令；实际指令名需在 cmd_list 白名单内）",
        json_schema_extra={
            "label": "自定义指令映射",
            "i18n": {
                "en-US": {
                    "label": "Custom command mapping",
                    "hint": "Custom command mapping (format: trigger <&参数&><<>>actual command; the actual command name must be in the cmd_list allowlist)",
                },
                "ja-JP": {
                    "label": "カスタムコマンドマッピング",
                    "hint": "カスタムコマンドマッピング（形式: トリガーワード <&参数&><<>>実際のコマンド。実際のコマンド名は cmd_list の許可リスト内である必要があります）",
                },
            },
        },
    )
    # 群友绑定配置
    bind_enabled: bool = Field(
        default=True,
        description="启用群友绑定功能（QQ 账号绑定游戏 ID 并写入白名单，需模组支持）",
        json_schema_extra={
            "label": "启用群友绑定",
            "i18n": {
                "en-US": {
                    "label": "Enable group member binding",
                    "hint": "Enable group member binding (bind QQ accounts to game IDs and add them to the allowlist; requires server mod support)",
                },
                "ja-JP": {
                    "label": "グループメンバーのバインドを有効化",
                    "hint": "バインド機能を有効化（QQ アカウントをゲーム ID に紐付けて許可リストに登録。サーバーモッドの対応が必要）",
                },
            },
        },
    )
    bind_geyser_enabled: bool = Field(
        default=True,
        description="启用基岩版绑定（/mc geyserbind，需服务端开启 Floodgate）",
        json_schema_extra={
            "label": "启用基岩版绑定",
            "i18n": {
                "en-US": {
                    "label": "Enable Bedrock binding",
                    "hint": "Enable Bedrock Edition binding (/mc geyserbind; requires Floodgate enabled on the server)",
                },
                "ja-JP": {
                    "label": "Bedrock 版バインドを有効化",
                    "hint": "Bedrock 版バインドを有効化（/mc geyserbind。サーバー側で Floodgate を有効にする必要があります）",
                },
            },
        },
    )
    bind_unbind_enabled: bool = Field(
        default=True,
        description="启用解绑功能（/mc unbind）",
        json_schema_extra={
            "label": "启用解绑功能",
            "i18n": {
                "en-US": {
                    "label": "Enable unbinding",
                    "hint": "Enable unbinding (/mc unbind)",
                },
                "ja-JP": {
                    "label": "バインド解除を有効化",
                    "hint": "バインド解除機能を有効化（/mc unbind）",
                },
            },
        },
    )


class PluginBaseConfig(PluginConfigBase):
    """插件基础配置"""

    __ui_label__ = "插件基础设置"

    __ui_i18n__: ClassVar[dict[str, dict[str, str]]] = {
        "en-US": {
            "title": "Plugin Basics",
            "description": "Plugin base configuration",
        },
        "ja-JP": {
            "title": "プラグイン基本設定",
            "description": "プラグインの基本設定",
        },
    }

    config_version: str = Field(
        default="1.0.0",
        description="配置版本号",
        json_schema_extra={
            "label": "配置版本",
            "disabled": True,
            "i18n": {
                "en-US": {
                    "label": "Config version",
                    "hint": "Config version number",
                },
                "ja-JP": {
                    "label": "設定バージョン",
                    "hint": "設定のバージョン番号",
                },
            },
        },
    )
    enabled: bool = Field(
        default=True,
        description="是否启用插件",
        json_schema_extra={
            "label": "启用插件",
            "i18n": {
                "en-US": {
                    "label": "Enable plugin",
                    "hint": "Whether to enable the plugin",
                },
                "ja-JP": {
                    "label": "プラグインを有効化",
                    "hint": "プラグインを有効にするかどうか",
                },
            },
        },
    )


class GeneralConfig(PluginConfigBase):
    """通用设置"""

    __ui_label__ = "通用设置"

    __ui_i18n__: ClassVar[dict[str, dict[str, str]]] = {
        "en-US": {
            "title": "General",
            "description": "General settings",
        },
        "ja-JP": {
            "title": "一般設定",
            "description": "一般設定",
        },
    }

    enabled: bool = Field(
        default=True,
        description="启用 Minecraft 聊天适配器",
        json_schema_extra={
            "label": "启用适配器",
            "i18n": {
                "en-US": {
                    "label": "Enable adapter",
                    "hint": "Enable the Minecraft chat adapter",
                },
                "ja-JP": {
                    "label": "アダプターを有効化",
                    "hint": "Minecraft チャットアダプターを有効化",
                },
            },
        },
    )
    mc_servers: list[McServerConfig] = Field(
        default_factory=list,
        description="MC 服务器列表",
        json_schema_extra={
            "label": "MC 服务器列表",
            "hint": "点「添加项目」逐个填写服务器连接信息",
            "i18n": {
                "en-US": {
                    "label": "MC server list",
                    "hint": "MC server list (click 'Add item' and fill in each server's connection info)",
                },
                "ja-JP": {
                    "label": "MC サーバーリスト",
                    "hint": "MC サーバーリスト（「項目を追加」をクリックして、各サーバーの接続情報を順に入力）",
                },
            },
        },
    )


class MinecraftAdapterConfig(PluginConfigBase):
    """插件完整配置"""

    __ui_label__ = "Minecraft 聊天适配器"

    plugin: PluginBaseConfig = Field(
        default_factory=PluginBaseConfig, description="插件基础配置"
    )
    general: GeneralConfig = Field(
        default_factory=GeneralConfig, description="通用设置"
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
        self.binding_handler: BindingHandler | None = None
        self._server_configs: dict[str, ServerConfig] = {}
        self._running = False

    # ── 生命周期 ────────────────────────────────────────

    async def on_load(self) -> None:
        data_dir = self.ctx.paths.data_dir
        runtime_dir = self.ctx.paths.runtime_dir
        data_dir.mkdir(parents=True, exist_ok=True)

        # 设置消息处理器（必须在 add_server 之前，否则 ServerConnection 拿到的 on_message 是 None，消息永远不转发）
        self.server_manager.set_message_handler(self._on_server_message)
        self.server_manager.set_connect_handler(self._on_server_connect)
        self.server_manager.set_disconnect_handler(self._on_server_disconnect)

        # 解析服务器配置
        for server_model in self.config.general.mc_servers:
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
            is_operator=self._is_operator,
        )
        for server_id, config in self._server_configs.items():
            if config.custom_cmd_list:
                self.command_handler.register_custom_commands(
                    server_id, config.custom_cmd_list
                )

        # 群友绑定处理器（复用命令处理器的会话作用域与编号选择待选机制）
        self.binding_handler = BindingHandler(
            server_manager=self.server_manager,
            get_server_config=lambda sid: self._server_configs.get(sid),
            resolve_server=self.command_handler._resolve_server_or_pending,
            session_servers=self.command_handler._get_session_servers_by,
        )
        self.command_handler.pending_dispatcher = self.binding_handler.handle_selection

        # 启动服务器连接
        if self.config.general.enabled:
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

    async def _is_operator(self, platform: str, user_id: str) -> bool:
        """判断发送者是否命中宿主操作员列表（`[plugin].permission`）。

        自定义指令与 `/mc cmd` 一致，收窄为仅操作员可触发，防止
        `cmd_white_black_list` 放宽后目标会话内任意用户越权执行远程指令。
        """
        if not platform or not user_id:
            return False
        try:
            perms = await self.ctx.config.get("plugin.permission") or []
            return is_operator_match(perms, platform, user_id)
        except Exception:
            return False

    # ── @Command：mc 命令组 ─────────────────────────────

    @Command(
        "mc_help",
        description="显示 Minecraft 聊天适配器帮助",
        pattern=r"^/mc(?:\s+help)?\s*$",
    )
    async def handle_mc_help(self, **kwargs):
        if not self.command_handler:
            return False, "未初始化", 1
        ctx = self._build_context(kwargs)
        result = await self.command_handler.handle_help(ctx)
        await self._send_result(result, ctx.stream_id)
        return True, "帮助已发送", 2

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

    # ── @Command：群友绑定（自助，默认权限） ────────────

    @Command(
        "mc_bind",
        description="绑定 QQ 账号到游戏 ID 并加入白名单",
        pattern=r"^/mc\s+bind\s+(?P<game_name>\S+)$",
    )
    async def handle_mc_bind(self, **kwargs):
        if not self.binding_handler:
            return False, "未初始化", 1
        ctx = self._build_context(kwargs)
        matched = kwargs.get("matched_groups") or {}
        game_name = (matched.get("game_name") or "").strip()
        result = await self.binding_handler.handle_bind(ctx, game_name)
        await self._send_result(result, ctx.stream_id)
        return True, "绑定结果已发送", 2

    @Command(
        "mc_geyserbind",
        description="绑定基岩版 ID 并加入白名单",
        pattern=r"^/mc\s+geyserbind\s+(?P<bedrock_name>\S+)$",
    )
    async def handle_mc_geyserbind(self, **kwargs):
        if not self.binding_handler:
            return False, "未初始化", 1
        ctx = self._build_context(kwargs)
        matched = kwargs.get("matched_groups") or {}
        bedrock_name = (matched.get("bedrock_name") or "").strip()
        result = await self.binding_handler.handle_bind(ctx, bedrock_name, bedrock=True)
        await self._send_result(result, ctx.stream_id)
        return True, "绑定结果已发送", 2

    @Command(
        "mc_unbind",
        description="解除 QQ 账号的全部游戏 ID 绑定",
        pattern=r"^/mc\s+unbind$",
    )
    async def handle_mc_unbind(self, **kwargs):
        if not self.binding_handler:
            return False, "未初始化", 1
        ctx = self._build_context(kwargs)
        result = await self.binding_handler.handle_unbind(ctx)
        await self._send_result(result, ctx.stream_id)
        return True, "解绑结果已发送", 2

    @Command(
        "mc_geyserunbind",
        description="只解除基岩版绑定，保留 Java 版绑定",
        pattern=r"^/mc\s+geyserunbind$",
    )
    async def handle_mc_geyserunbind(self, **kwargs):
        if not self.binding_handler:
            return False, "未初始化", 1
        ctx = self._build_context(kwargs)
        result = await self.binding_handler.handle_geyserunbind(ctx)
        await self._send_result(result, ctx.stream_id)
        return True, "解绑结果已发送", 2

    @Command(
        "mc_mybind",
        description="查看自己的游戏 ID 绑定",
        pattern=r"^/mc\s+mybind$",
    )
    async def handle_mc_mybind(self, **kwargs):
        if not self.binding_handler:
            return False, "未初始化", 1
        ctx = self._build_context(kwargs)
        result = await self.binding_handler.handle_mybind(ctx)
        await self._send_result(result, ctx.stream_id)
        return True, "绑定信息已发送", 2

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

        # 3. 消息转发到 MC（返回 True 表示「前缀命中、已作为 MC 指令转发」，需要中止后续处理；
        #    空前缀=全量镜像只转发不中止，bot 仍可正常回复）
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
