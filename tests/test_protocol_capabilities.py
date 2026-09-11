"""core/protocol.py 能力协商（capability negotiation）测试。

服务端模组通过 `GET /api/v1/health` 的 `data.protocolVersion` / `data.features`
暴露能力；绑定类功能必须探测到 `binding.v1` 才可用，任何解析异常都必须
fail closed（probed=False → supports_binding=False）。
"""

from maibot_plugin_minecraft_adapter.core.protocol import (
    FEATURE_BINDING,
    ServerCapabilities,
    parse_feature_set,
    parse_protocol_version,
)

# 旧版模组 AstrBotAdapter_Forge v1.0.0 的真实 features（无 binding.v1）——
# 作为回归基准：绝不能因为「服务端可达」就误判为支持绑定。
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


def _health(features, protocol_version=2) -> dict:
    data = {
        "status": "ok",
        "apiVersion": "v1",
    }
    if protocol_version is not None:
        data["protocolVersion"] = protocol_version
    if features is not None:
        data["features"] = features
    return data


def test_from_health_full_supports_binding():
    caps = ServerCapabilities.from_health(
        _health([*OLD_MOD_FEATURES, FEATURE_BINDING], protocol_version=2)
    )
    assert caps.probed is True
    assert caps.protocol_version == 2
    assert FEATURE_BINDING in caps.features
    assert caps.supports_binding is True


def test_from_health_without_binding_degrades():
    """旧版模组特征列表：可达但无绑定能力 → 必须拒绝。"""
    caps = ServerCapabilities.from_health(_health(OLD_MOD_FEATURES))
    assert caps.probed is True
    assert caps.protocol_version == 2
    assert caps.features == set(OLD_MOD_FEATURES)
    assert caps.supports_binding is False


def test_from_health_features_missing():
    caps = ServerCapabilities.from_health(_health(None))
    assert caps.probed is True
    assert caps.features == set()
    assert caps.protocol_version == 2
    assert caps.supports_binding is False


def test_from_health_features_not_a_list():
    for value in ("binding.v1", 42, {"binding.v1": True}, object()):
        caps = ServerCapabilities.from_health(_health(value))
        assert caps.probed is True
        assert caps.features == set()
        assert caps.supports_binding is False


def test_from_health_features_with_non_strings():
    caps = ServerCapabilities.from_health(
        _health([FEATURE_BINDING, 7, None, b"bytes", ["nested"], {"a": 1}])
    )
    assert caps.probed is True
    assert caps.features == {FEATURE_BINDING}
    assert caps.supports_binding is True


def test_from_health_non_strings_only():
    caps = ServerCapabilities.from_health(_health([1, 2, None, True]))
    assert caps.probed is True
    assert caps.features == set()
    assert caps.supports_binding is False


def test_from_health_empty_dict():
    caps = ServerCapabilities.from_health({})
    assert caps.probed is True
    assert caps.protocol_version == 0
    assert caps.features == set()
    assert caps.supports_binding is False


def test_from_health_none_is_probed_but_empty():
    caps = ServerCapabilities.from_health(None)
    assert caps.probed is True
    assert caps.supports_binding is False


def test_unprobed_never_supports_binding():
    caps = ServerCapabilities.unprobed()
    assert caps.probed is False
    assert caps.supports_binding is False

    # 即使能力集合里有 binding.v1，未探测也一律视为不支持
    caps.features.add(FEATURE_BINDING)
    assert caps.probed is False
    assert caps.supports_binding is False


def test_supports_binding_requires_both():
    assert (
        ServerCapabilities(probed=False, features={FEATURE_BINDING}).supports_binding
        is False
    )
    assert ServerCapabilities(probed=True, features=set()).supports_binding is False
    assert (
        ServerCapabilities(probed=True, features={FEATURE_BINDING}).supports_binding
        is True
    )


def test_default_capabilities_are_unprobed():
    caps = ServerCapabilities()
    assert caps.protocol_version == 0
    assert caps.features == set()
    assert caps.probed is False
    assert caps.supports_binding is False


def test_parse_feature_set_tolerates_exotic_values():
    assert parse_feature_set(None) == set()
    assert parse_feature_set("binding.v1") == set()
    assert parse_feature_set({"binding.v1"}) == {"binding.v1"}
    assert parse_feature_set(("a", 1)) == {"a"}
    assert parse_feature_set([["x"]]) == set()


def test_parse_protocol_version_tolerates_exotic_values():
    assert parse_protocol_version(2) == 2
    assert parse_protocol_version("3") == 3
    assert parse_protocol_version(" 4 ") == 4
    assert parse_protocol_version(True) == 0  # bool 不是版本号
    assert parse_protocol_version("v1") == 0
    assert parse_protocol_version(None) == 0
    assert parse_protocol_version(1.5) == 0
