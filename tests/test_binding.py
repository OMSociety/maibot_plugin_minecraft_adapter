"""services/binding.py 用户绑定服务测试。"""

from maibot_plugin_minecraft_adapter.services.binding import BindingService, UserBinding


def test_bind_and_get(tmp_path):
    svc = BindingService(tmp_path)
    ok, _msg = svc.bind("qq", "123", "Steve", server_id="s1")
    assert ok is True
    b = svc.get_binding("qq", "123")
    assert b is not None
    assert b.mc_player_name == "Steve"
    assert b.server_id == "s1"


def test_duplicate_bind_rejected(tmp_path):
    svc = BindingService(tmp_path)
    svc.bind("qq", "123", "Steve")
    ok, msg = svc.bind("qq", "123", "Alex")
    assert ok is False
    assert "Steve" in msg


def test_unbind(tmp_path):
    svc = BindingService(tmp_path)
    svc.bind("qq", "123", "Steve")
    ok, _msg = svc.unbind("qq", "123")
    assert ok is True
    assert svc.get_binding("qq", "123") is None


def test_unbind_nonexistent(tmp_path):
    svc = BindingService(tmp_path)
    ok, _msg = svc.unbind("qq", "999")
    assert ok is False


def test_persistence(tmp_path):
    svc = BindingService(tmp_path)
    svc.bind("qq", "123", "Steve")
    # 重新加载
    svc2 = BindingService(tmp_path)
    b = svc2.get_binding("qq", "123")
    assert b is not None
    assert b.mc_player_name == "Steve"


def test_user_binding_roundtrip():
    ub = UserBinding(
        platform="qq", user_id="1", mc_player_name="Steve", mc_player_uuid="u1"
    )
    d = ub.to_dict()
    ub2 = UserBinding.from_dict(d)
    assert ub2.platform == "qq"
    assert ub2.mc_player_name == "Steve"
