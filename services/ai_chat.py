"""AI 聊天服务（MaiBot 版）——游戏内玩家与 bot 对话。

从 AstrBot 版 platform/adapter.py + platform/event.py 迁移：
- AstrBot 通过平台适配器 + 事件队列走完整消息管线；
- MaiBot 改为直连 ctx.llm.generate，人格由 ctx.config.get() 读全局 [personality] 注入，
  每个玩家维护一小段在内存的对话历史（Runner 子进程重启后清空，可接受）。
"""

import logging
import time
from typing import Any

from ..core.models import MCMessage, MessageType

logger = logging.getLogger(__name__)

# 每个玩家保留的最大历史轮次（一问一答为 2 条消息）
_HISTORY_MAX_MESSAGES = 12
# 人格缓存时长（秒）
_PERSONA_CACHE_TTL = 60


class AIChatService:
    """AI 聊天服务：处理来自 MC 服务器的 CHAT_REQUEST。"""

    def __init__(self, plugin):
        """Args:
        plugin: MaiBotPlugin 实例，提供 ctx.llm / ctx.config / ctx.logger
        """
        self._plugin = plugin
        self._persona_cache: tuple[str, float] | None = None
        self._histories: dict[str, list[dict[str, str]]] = {}

    async def _get_persona_prompt(self) -> str:
        """读取 MaiBot 全局人格（[personality] 三字段），带缓存。"""
        now = time.monotonic()
        if self._persona_cache and now - self._persona_cache[1] < _PERSONA_CACHE_TTL:
            return self._persona_cache[0]
        try:
            cfg = self._plugin.ctx.config
            personality = await cfg.get("personality.personality", "")
            behavior = await cfg.get("personality.behavior_style", "")
            reply_style = await cfg.get("personality.reply_style", "")
            parts = []
            if personality:
                parts.append(f"【人格】{personality}")
            if behavior:
                parts.append(f"【行为风格】{behavior}")
            if reply_style:
                parts.append(f"【表达风格】{reply_style}")
            text = "\n".join(parts)
            self._persona_cache = (text, now)
            return text
        except Exception as e:
            logger.warning(f"[AIChat] 读取全局人格失败: {e}")
            return ""

    @staticmethod
    def _history_key(server_id: str, player_uuid: str, player_name: str) -> str:
        return f"{server_id}:{player_uuid or player_name or 'unknown'}"

    def _get_history(self, key: str) -> list[dict[str, str]]:
        return self._histories.get(key, [])

    def _append_history(self, key: str, user_text: str, reply: str):
        history = self._histories.setdefault(key, [])
        history.append({"role": "user", "content": user_text})
        history.append({"role": "assistant", "content": reply})
        # 裁剪到上限
        if len(history) > _HISTORY_MAX_MESSAGES:
            del history[: len(history) - _HISTORY_MAX_MESSAGES]

    async def handle_chat_request(
        self, server_id: str, server_connection, msg: MCMessage
    ):
        """处理来自 Minecraft 的聊天请求：生成回复并回传 MC。"""
        if msg.type != MessageType.CHAT_REQUEST or not msg.source:
            return

        payload = msg.payload or {}
        chat_mode = payload.get("chatMode", "GROUP")
        content = payload.get("content", "")
        if not content:
            return

        player_uuid = msg.source.player_uuid
        player_name = msg.source.player_name
        request_id = msg.id

        # 构建消息列表（人格 + 历史 + 本次）
        system_prompt = await self._get_persona_prompt()
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        key = self._history_key(server_id, player_uuid, player_name)
        messages.extend(self._get_history(key))
        messages.append({"role": "user", "content": content})

        reply = ""
        try:
            result: dict[str, Any] = await self._plugin.ctx.llm.generate(
                prompt=messages
            )
            reply = str((result or {}).get("response") or "").strip()
        except Exception as e:
            logger.error(f"[AIChat-{server_id}] LLM 生成失败: {e}")

        if not reply:
            # 生成失败或为空时不回传，避免打扰
            return

        target_type = "PLAYER" if chat_mode == "PRIVATE" else "BROADCAST"
        await server_connection.ws_client.send_chat_response(
            reply_to=request_id,
            target_type=target_type,
            chat_mode=chat_mode,
            content=reply,
            player_uuid=player_uuid if chat_mode == "PRIVATE" else "",
        )
        self._append_history(key, content, reply)
        logger.debug(
            f"[AIChat-{server_id}] {player_name or player_uuid} -> {reply[:50]}"
        )
