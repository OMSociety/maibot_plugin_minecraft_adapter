"""core/ws_client.py 与 core/rest_client.py 的认证 Token 脱敏测试。"""


def test_ws_redact_hides_token():
    from maibot_plugin_minecraft_adapter.core.ws_client import WebSocketClient

    c = WebSocketClient("sv1", "127.0.0.1", 8765, "SECRETTOKEN")
    assert (
        c._redact("ws://127.0.0.1:8765/ws?token=SECRETTOKEN")
        == "ws://127.0.0.1:8765/ws?token=***"
    )
    assert c._redact("plain message") == "plain message"
    assert c._redact("SECRETTOKEN") == "***"


def test_ws_redact_empty_token_noop():
    from maibot_plugin_minecraft_adapter.core.ws_client import WebSocketClient

    c = WebSocketClient("sv1", "127.0.0.1", 8765, "")
    assert c._redact("ws://h/ws?token=abc") == "ws://h/ws?token=abc"


def test_rest_redact_hides_token():
    from maibot_plugin_minecraft_adapter.core.rest_client import RestClient

    c = RestClient("sv1", "127.0.0.1", 8765, "SECRETTOKEN")
    assert c._redact("Connection failed SECRETTOKEN") == "Connection failed ***"


def test_rest_redact_empty_token():
    from maibot_plugin_minecraft_adapter.core.rest_client import RestClient

    c = RestClient("sv1", "127.0.0.1", 8765, "")
    assert c._redact("x token SECRET") == "x token SECRET"
