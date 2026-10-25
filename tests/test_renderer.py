"""services/renderer.py 纯逻辑（文本格式化/工具函数、头像缓存路径）测试。"""

import asyncio

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


# ── 头像缓存路径（键来自服务端下发的玩家名）────────────────


def test_avatar_cache_path_confines_slash_name_to_cache_dir(tmp_path):
    r = InfoRenderer(cache_dir=tmp_path)
    path = r._avatar_cache_path("../../evil", "", 50)
    assert path == tmp_path / "avatars" / "evil_50.png"


def test_avatar_cache_path_confines_backslash_name_to_cache_dir(tmp_path):
    r = InfoRenderer(cache_dir=tmp_path)
    path = r._avatar_cache_path(r"..\..\..\Windows\Temp\payload", "", 50)
    assert path is not None
    assert path.parent == tmp_path / "avatars"
    assert "\\" not in path.name
    assert "/" not in path.name


def test_avatar_cache_path_uses_uuid_when_name_is_unusable(tmp_path):
    r = InfoRenderer(cache_dir=tmp_path)
    path = r._avatar_cache_path("", "069a79f4-44e9-4726-a5be-fca90e38aaf5", 8)
    assert path == tmp_path / "avatars" / "069a79f444e94726a5befca90e38aaf5_8.png"


def test_avatar_cache_path_rejects_escaped_key(tmp_path, monkeypatch):
    """纵深防御：键过滤若被绕过（例如将来换键来源），realpath 校验仍拒绝落盘。"""
    r = InfoRenderer(cache_dir=tmp_path)
    monkeypatch.setattr(
        InfoRenderer, "_avatar_key", staticmethod(lambda name, uuid_: "../escape")
    )
    assert r._avatar_cache_path("Notch", "", 50) is None


def test_get_avatar_with_hostile_name_writes_only_inside_cache_dir(
    tmp_path, monkeypatch
):
    r = InfoRenderer(cache_dir=tmp_path)
    (tmp_path / "avatars").mkdir(parents=True)

    async def _no_face(player_name, player_uuid):
        return None

    monkeypatch.setattr(r, "_fetch_avatar_face", _no_face)
    avatar = asyncio.run(r._get_avatar("../../evil", "", 50))
    assert avatar.size == (50, 50)
    assert [p.name for p in (tmp_path / "avatars").iterdir()] == ["evil_50.png"]
    assert not (tmp_path / "evil_50.png").exists()
