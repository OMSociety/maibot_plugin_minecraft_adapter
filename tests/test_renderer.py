"""services/renderer.py 纯逻辑（文本格式化/工具函数）测试。"""

from maibot_plugin_minecraft_adapter.core.models import (
    PlayerDetail,
    PlayerInfo,
    ServerInfo,
    ServerStatus,
)
from maibot_plugin_minecraft_adapter.services.renderer import InfoRenderer


def _renderer() -> InfoRenderer:
    return InfoRenderer(text2image_enabled=False)


def test_safe_percent_bounds():
    r = _renderer()
    assert r._safe_percent(50) == 50
    assert r._safe_percent(-10) == 0
    assert r._safe_percent(150) == 100
    assert r._safe_percent("bad") == 0


def test_mode_cn():
    r = _renderer()
    assert r._mode_cn("SURVIVAL") == "生存"
    assert r._mode_cn("CREATIVE") == "创造"
    assert r._mode_cn("UNKNOWN_MODE") == "UNKNOWN_MODE"
    assert r._mode_cn("") == "未知"


def test_format_server_status_text():
    r = _renderer()
    info = ServerInfo(
        name="s1",
        platform="paper",
        minecraft_version="1.20",
        online_count=3,
        max_players=20,
        uptime_formatted="1h",
    )
    status = ServerStatus(
        online=True,
        tps_1m=20.0,
        memory_used=512,
        memory_max=1024,
        online_players=3,
        max_players=20,
    )
    text = r._format_server_status_text(info, status)
    assert "s1" in text
    assert "3/20" in text
    assert "TPS" in text


def test_format_player_detail_text():
    r = _renderer()
    p = PlayerDetail(
        name="Steve",
        uuid="u1",
        health=20.0,
        max_health=20.0,
        food_level=18,
        level=5,
        world="world",
    )
    text = r._format_player_detail_text(p)
    assert "Steve" in text
    assert "u1" in text
    assert "生命值" in text


def test_flatten_player_cards_single():
    r = _renderer()
    p = PlayerInfo(name="Steve", uuid="u1", world="world")
    cards = [("s1", [p], 1, "s1")]
    flattened = r._flatten_player_cards(cards)
    assert len(flattened) == 1
    assert flattened[0][1] == [p]


def test_format_multi_player_list_text():
    r = _renderer()
    p = PlayerInfo(
        name="Steve", uuid="u1", game_mode="SURVIVAL", world="world", ping=10
    )
    cards = [("s1", [p], 1, "s1")]
    text = r._format_multi_player_list_text(cards)
    assert "Steve" in text
    assert "1人" in text
