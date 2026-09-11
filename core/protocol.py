"""协议能力协商（capability negotiation）。

服务端（AstrBotAdapter_Forge / AstrBotAdapter_Forge_Forward）通过
`GET /api/v1/health` 的 `data.protocolVersion` 与 `data.features` 暴露自身能力；
协议文档要求客户端**运行时探测能力**，而不是假设插件版本号。

本模块只做纯解析，不发起网络请求，且**失败关闭**（fail closed）：
任何异常/异常值都不得抛出，最坏情况是 `probed=False`（即不支持绑定）。
"""

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# 绑定与白名单写入能力（AstrBotAdapter_Forge_Forward v1.1.0+）
FEATURE_BINDING = "binding.v1"


def _as_mapping(value: object) -> dict:
    """把疑似 dict 的值安全地变成 dict（None / 非 dict → 空 dict）。"""
    if isinstance(value, dict):
        return value
    return {}


def parse_feature_set(value: object) -> set[str]:
    """把 health 响应里的 `features` 解析成字符串集合。

    容错口径：只接受 list/tuple/set，且只保留字符串项；其余一律丢弃，
    绝不抛异常（协议可能被旧版本或第三方实现以意外形态返回）。
    """
    if not isinstance(value, (list, tuple, set, frozenset)):
        return set()
    return {item for item in value if isinstance(item, str)}


def parse_protocol_version(value: object) -> int:
    """解析 `protocolVersion`：非整数（含 bool）一律退化为 0。"""
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip())
        except (TypeError, ValueError):
            return 0
    return 0


@dataclass
class ServerCapabilities:
    """服务端在握手/健康检查中自报的能力集合。

    `probed` 只在**成功解析**一次健康检查响应后为 True；
    绑定类功能必须同时满足 `probed` 且 `FEATURE_BINDING` 在 `features` 中。
    """

    protocol_version: int = 0
    features: set[str] = field(default_factory=set)
    probed: bool = False  # True only after a successful probe

    @property
    def supports_binding(self) -> bool:
        return self.probed and FEATURE_BINDING in self.features

    @classmethod
    def from_health(cls, data: dict) -> "ServerCapabilities":
        """从 `GET /api/v1/health` 响应的 `data` 对象解析能力。

        参数:
            data: 健康检查响应的 `data` 对象（非 dict 时按「空响应」处理，
                仍然返回 `probed=True` 的空能力集——即服务端可达但无能力声明）。

        返回:
            ServerCapabilities: 解析成功（probed=True）；解析过程中出现
            任何异常则返回 `probed=False`（不支持绑定，fail closed）。
        """
        try:
            payload = _as_mapping(data)
            return cls(
                protocol_version=parse_protocol_version(payload.get("protocolVersion")),
                features=parse_feature_set(payload.get("features")),
                probed=True,
            )
        except Exception as e:  # noqa: BLE001 - 能力探测绝不因解析失败而中断连接
            logger.debug(f"[protocol] 能力解析失败（按不支持处理）: {e}")
            return cls(protocol_version=0, features=set(), probed=False)

    @classmethod
    def unprobed(cls) -> "ServerCapabilities":
        """尚未探测（或探测失败）的能力对象——等价于「什么都不支持」。"""
        return cls(protocol_version=0, features=set(), probed=False)
