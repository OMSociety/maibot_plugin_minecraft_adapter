"""Minecraft 服务器通信的 REST API 客户端"""

import logging
from typing import Any

import aiohttp

from .models import (
    ApiResponse,
    LogEntry,
    PlayerDetail,
    PlayerInfo,
    ServerInfo,
    ServerStatus,
)
from .protocol import ServerCapabilities

logger = logging.getLogger(__name__)

# REST API 常量
DEFAULT_REQUEST_TIMEOUT = 30  # 默认请求超时（秒）
HEALTH_CHECK_TIMEOUT = 5  # 健康检查超时（秒）
MAX_LOG_LINES = 1000  # 最大日志行数

# 绑定相关错误码（见服务端模组协议 3.1 响应信封 / 绑定 API）
CODE_FEATURE_DISABLED = 4003  # 功能未启用
CODE_BINDING_NOT_FOUND = 4004  # 尚未绑定
CODE_BINDING_NAME_TAKEN = 4005  # 该游戏 ID 已被其他账号绑定
CODE_BINDING_INVALID_NAME = 4006  # 游戏 ID 格式不合法
CODE_WHITELIST_FAILED = 5004  # 白名单写入失败


class RestClient:
    """与 Minecraft 服务器通信的 REST API 客户端"""

    def __init__(
        self,
        server_id: str,
        host: str,
        port: int,
        token: str,
        request_timeout: int = DEFAULT_REQUEST_TIMEOUT,
    ):
        self.server_id = server_id
        self.host = host
        self.port = port
        self.token = token
        self._request_timeout = request_timeout
        self._session: aiohttp.ClientSession | None = None

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}/api/v1"

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    def _redact(self, text: str) -> str:
        """把异常文本里的认证 Token 替换成 ***，避免泄漏。"""
        if not self.token:
            return str(text)
        return str(text).replace(self.token, "***")

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self):
        """关闭 HTTP 会话"""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def _request(
        self,
        method: str,
        endpoint: str,
        params: dict | None = None,
        json_data: dict | None = None,
        timeout: int | None = None,
    ) -> ApiResponse:
        """向服务器发送 HTTP 请求"""
        url = f"{self.base_url}{endpoint}"

        try:
            session = await self._get_session()
            async with session.request(
                method,
                url,
                headers=self.headers,
                params=params,
                json=json_data,
                timeout=aiohttp.ClientTimeout(total=timeout or self._request_timeout),
            ) as resp:
                data = await resp.json()
                return ApiResponse.from_dict(data)

        except aiohttp.ClientConnectorError:
            logger.error(f"[MC-{self.server_id}] 无法连接到服务器")
            return ApiResponse(code=3002, message="服务器连接失败")
        except TimeoutError:
            logger.error(f"[MC-{self.server_id}] 请求超时")
            return ApiResponse(code=3002, message="请求超时")
        except Exception as e:
            logger.error(f"[MC-{self.server_id}] 请求错误: {self._redact(str(e))}")
            return ApiResponse(code=3001, message=str(e))

    async def _get(
        self,
        endpoint: str,
        params: dict | None = None,
        timeout: int | None = None,
    ) -> ApiResponse:
        return await self._request("GET", endpoint, params=params, timeout=timeout)

    async def _post(
        self,
        endpoint: str,
        json_data: dict | None = None,
        timeout: int | None = None,
    ) -> ApiResponse:
        return await self._request(
            "POST", endpoint, json_data=json_data, timeout=timeout
        )

    # 服务器 APIs

    async def get_server_info(self) -> tuple[ServerInfo | None, str]:
        """获取服务器信息"""
        resp = await self._get("/server/info")
        if resp.success and resp.data:
            info = ServerInfo.from_dict(resp.data)
            if info.is_proxy:
                logger.debug(
                    f"[MC-{self.server_id}] 代理模式: "
                    f"{info.backend_count}个后端, "
                    f"总在线{info.aggregate_online}/{info.aggregate_max}"
                )
            return info, ""
        return None, resp.message

    async def get_server_status(self) -> tuple[ServerStatus | None, str]:
        """获取服务器状态"""
        resp = await self._get("/server/status")
        if resp.success and resp.data:
            status = ServerStatus.from_dict(resp.data)
            if status.is_proxy:
                logger.debug(
                    f"[MC-{self.server_id}] 代理状态: "
                    f"{len(status.backends)}个后端已上报"
                )
            return status, ""
        return None, resp.message

    async def fetch_capabilities(self) -> ServerCapabilities:
        """探测服务端协议能力（`GET /api/v1/health`，无需认证）。

        协议文档要求客户端运行时探测能力而非假设版本：绑定类功能必须以此为准。
        探测失败（服务不可达、响应异常）返回 `probed=False` 的能力对象，
        由调用方决定是否优雅降级——本方法自身不抛异常。
        """
        try:
            resp = await self._get("/health", timeout=HEALTH_CHECK_TIMEOUT)
        except Exception as e:  # noqa: BLE001 - 探测失败按「不支持」处理
            logger.debug(
                f"[MC-{self.server_id}] 能力探测请求失败: {self._redact(str(e))}"
            )
            return ServerCapabilities.unprobed()

        if not resp.success:
            logger.debug(
                f"[MC-{self.server_id}] 能力探测失败: code={resp.code} "
                f"{self._redact(resp.message)}"
            )
            return ServerCapabilities.unprobed()

        return ServerCapabilities.from_health(resp.data)

    async def health_check(self) -> bool:
        """检查服务器是否健康（不需要认证）。

        保留布尔语义（供既有调用方使用），实现复用能力探测：
        只要 `GET /api/v1/health` 返回 `code == 0` 即视为健康，
        与 `features` 内容无关（旧版模组没有能力字段也算健康）。
        """
        try:
            resp = await self._get("/health", timeout=HEALTH_CHECK_TIMEOUT)
            return resp.success
        except Exception:
            return False

    # 玩家 APIs

    async def get_players(
        self, page: int = 1, size: int = 20
    ) -> tuple[list[PlayerInfo], int, str]:
        """获取在线玩家列表"""
        resp = await self._get("/players", params={"page": page, "size": size})
        if resp.success and resp.data:
            players = [PlayerInfo.from_dict(p) for p in resp.data.get("players", [])]
            total = resp.data.get("total", resp.data.get("count", len(players)))
            return players, total, ""
        return [], 0, resp.message

    async def get_player(self, identifier: str) -> tuple[PlayerDetail | None, str]:
        """通过 UUID 或名称获取玩家详细信息"""
        resp = await self._get(f"/players/{identifier}")
        if resp.success and resp.data:
            return PlayerDetail.from_dict(resp.data), ""
        return None, resp.message

    # Aliases for backward compatibility
    get_player_by_uuid = get_player
    get_player_by_name = get_player

    # 命令 APIs

    async def execute_command(
        self,
        command: str,
        executor: str = "CONSOLE",
        player_uuid: str | None = None,
        is_async: bool = False,
        target_server: str | None = None,
    ) -> tuple[bool, str, Any]:
        """在服务器上执行命令

        参数:
            command: 要执行的指令（不带/）
            executor: 执行者 CONSOLE/PLAYER
            player_uuid: executor为PLAYER时指定
            is_async: 是否异步执行
            target_server: 目标后端服务器名称（仅代理端有效）

        返回:
            tuple: (成功, 输出/错误消息, 原始数据)
        """
        json_data: dict[str, Any] = {
            "command": command,
            "executor": executor,
            "async": is_async,
        }
        if player_uuid:
            json_data["playerUuid"] = player_uuid
        if target_server:
            json_data["targetServer"] = target_server

        resp = await self._post("/command/execute", json_data=json_data)

        if resp.success and resp.data:
            if is_async:
                return True, resp.data.get("taskId", ""), resp.data
            return (
                resp.data.get("success", False),
                resp.data.get("output", ""),
                resp.data,
            )

        return False, resp.message, None

    # 日志 APIs

    async def get_logs(
        self,
        lines: int = 100,
        level: str | None = None,
        keyword: str | None = None,
        start_time: int | None = None,
        end_time: int | None = None,
    ) -> tuple[list[LogEntry], str]:
        """获取服务器日志"""
        params: dict[str, Any] = {"lines": min(lines, MAX_LOG_LINES)}
        if level:
            params["level"] = level
        if keyword:
            params["keyword"] = keyword
        if start_time:
            params["startTime"] = start_time
        if end_time:
            params["endTime"] = end_time

        resp = await self._get("/logs", params=params)
        if resp.success and resp.data:
            logs = [LogEntry.from_dict(log) for log in resp.data.get("logs", [])]
            return logs, ""
        return [], resp.message

    # 绑定 APIs（AstrBotAdapter_NeoForge v1.2.0+，需先探测 binding.v1 能力）

    def _binding_message(self, resp: ApiResponse, fallback: str) -> str:
        """整理绑定接口的错误文本（脱敏，避免把 Token 带进回复/日志）。"""
        text = (resp.message or "").strip()
        if not text:
            return fallback
        return self._redact(text)

    async def bind_player(
        self,
        platform: str,
        user_id: str,
        game_name: str,
        bedrock: bool = False,
    ) -> tuple[bool, dict, int, str]:
        """把外部平台账号绑定到游戏 ID，并写入服务器白名单。

        参数:
            platform: 外部平台名（如 qq）
            user_id: 外部平台用户 ID
            game_name: 游戏内 ID（Java 版为玩家名，基岩版为基岩 ID）
            bedrock: 是否基岩版（Floodgate 前缀处理）

        返回:
            tuple: (是否成功, 响应 data, 服务端错误码, 错误消息)
            错误码一并返回，由调用方映射成用户文案，避免在这里丢掉语义。
        """
        resp = await self._post(
            "/bindings",
            json_data={
                "platform": platform,
                "userId": user_id,
                "gameName": game_name,
                "bedrock": bedrock,
            },
        )
        if resp.success:
            return True, resp.data or {}, 0, ""

        logger.debug(
            f"[MC-{self.server_id}] 绑定失败: code={resp.code} "
            f"{self._binding_message(resp, '绑定失败')}"
        )
        return False, {}, resp.code, self._binding_message(resp, "绑定失败")

    async def unbind_player(
        self, platform: str, user_id: str, kind: str = "all"
    ) -> tuple[bool, dict, int, str]:
        """解除外部平台账号与游戏 ID 的绑定（并尝试移出白名单）。

        参数:
            kind: 只解某一类绑定（``java`` / ``geyser``）；默认 ``all`` 清空该账号全部绑定。

        返回:
            tuple: (是否成功, 响应 data, 服务端错误码, 错误消息)
        """
        resp = await self._post(
            "/bindings/unbind",
            json_data={"platform": platform, "userId": user_id, "kind": kind},
        )
        if resp.success:
            return True, resp.data or {}, 0, ""

        logger.debug(
            f"[MC-{self.server_id}] 解绑失败: code={resp.code} "
            f"{self._binding_message(resp, '解绑失败')}"
        )
        return False, {}, resp.code, self._binding_message(resp, "解绑失败")

    async def lookup_binding(
        self, platform: str, user_id: str
    ) -> tuple[bool, dict, int, str]:
        """查询外部平台账号的绑定状态。

        返回:
            tuple: (是否成功, 响应 data, 服务端错误码, 错误消息)
        """
        resp = await self._get(
            "/bindings/lookup",
            params={"platform": platform, "userId": user_id},
        )
        if resp.success:
            return True, resp.data or {}, 0, ""

        logger.debug(
            f"[MC-{self.server_id}] 绑定查询失败: code={resp.code} "
            f"{self._binding_message(resp, '查询失败')}"
        )
        return False, {}, resp.code, self._binding_message(resp, "查询失败")
