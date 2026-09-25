"""WebSocket 客户端握手失败路径的连接释放测试。

回归背景：握手在收到 CONNECTION_ACK 前失败时，旧实现只 log + ``return False``，
把已升级的连接留在 ``self._ws`` 上；重连循环下一轮直接覆盖 ``self._ws``，
被覆盖的连接继续被 connector 持有（只能等 GC），退避封顶 60s 时约每分钟净泄漏一条。

断言口径与实现解耦：只看
1. ``client._ws is None``（客户端不再持有连接句柄）；
2. ``client._session.connector._acquired`` 归零（connector 没留下 transport）；
3. **服务端**观察到这条连接被关闭（``server.closed``）—— 协议层证据，
   不依赖客户端内部有没有某个 helper。
"""

import asyncio
import contextlib
import json
import socket

import pytest
from aiohttp import web

from maibot_plugin_minecraft_adapter.core import ws_client as ws_module
from maibot_plugin_minecraft_adapter.core.ws_client import WebSocketClient

TOKEN = "test-token"

# 服务端行为：silent=只 upgrade 什么都不发；non_ack=JSON 但不是 CONNECTION_ACK；
# bad_json=不是 JSON（触发 msg.json() 解析失败）
MODES = ["silent", "non_ack", "bad_json"]


class _WSServer:
    """本地「不完成握手」的 WS 服务端，并记录连接数与已关闭连接数。"""

    def __init__(self, mode: str):
        self.mode = mode
        self.connections = 0
        self.closed = 0
        self.runner: web.AppRunner | None = None
        self.port = 0

    async def _handler(self, request):
        self.connections += 1
        ws = web.WebSocketResponse(heartbeat=None)
        await ws.prepare(request)
        if self.mode == "non_ack":
            await ws.send_str(json.dumps({"type": "server.hello"}))
        elif self.mode == "bad_json":
            await ws.send_str("not-json")
        # silent 模式不发任何东西，让客户端等到超时；下面这轮循环同时充当
        # 「这条连接是否被对端关闭」的观测点
        async for _ in ws:
            pass
        self.closed += 1
        return ws

    async def start(self) -> "_WSServer":
        app = web.Application()
        app.router.add_get("/ws", self._handler)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]
        await web.TCPSite(self.runner, "127.0.0.1", self.port).start()
        return self

    async def wait_closed(self, expected: int, timeout: float = 2.0) -> bool:
        """等服务端观察到至少 expected 条连接被关闭（跨事件循环 tick，容许一点延迟）。"""
        waited = 0.0
        while self.closed < expected and waited < timeout:
            await asyncio.sleep(0.01)
            waited += 0.01
        return self.closed >= expected

    async def cleanup(self) -> None:
        if self.runner is not None:
            await self.runner.cleanup()


def _client(port: int) -> WebSocketClient:
    return WebSocketClient(
        server_id="sv1", host="127.0.0.1", port=port, token=TOKEN
    )


@pytest.mark.parametrize("mode", MODES)
def test_failed_handshake_releases_connection(mode, monkeypatch):
    """连续两轮握手失败后：连接被关闭、不残留在 connector、可继续重连。"""
    # silent 模式靠超时退出，缩短等待
    monkeypatch.setattr(ws_module, "CONNECTION_TIMEOUT", 0.2)

    async def main():
        server = await _WSServer(mode).start()
        client = _client(server.port)
        try:
            for round_no in range(1, 3):
                assert await client.connect() is False
                assert client._ws is None
                assert client._connected is False
                # connector._acquired 里残留 transport 就是泄漏
                assert len(client._session.connector._acquired) == 0
                # 服务端侧确认这条连接确实被关闭了（不是被丢掉等 GC）
                assert await server.wait_closed(round_no) is True
            assert server.connections == 2  # 确实重连了两次，不是复用同一连接
        finally:
            await client.disconnect()
            await server.cleanup()

    asyncio.run(main())


def test_start_closes_connection_after_receive_loop_ends():
    """接收循环结束后，start() 的收尾必须关掉连接（旧实现留着等 GC）。"""

    class _FakeWS:
        closed = False

        async def close(self):
            self.closed = True

    async def main():
        client = _client(1)
        client._connected = True
        stale = _FakeWS()
        client._ws = stale

        async def fake_receive_loop():
            # 模拟连接丢失：接收循环退出，且不再进入下一轮重连
            client._connected = False
            client._running = False

        client._receive_loop = fake_receive_loop
        await client.start()

        assert stale.closed is True
        assert client._ws is None
        await client.disconnect()

    asyncio.run(main())


def test_cancelled_handshake_releases_connection(monkeypatch):
    """握手期间被取消（停机 / 任务 cancel）也必须释放已升级的连接。

    CancelledError 继承 BaseException，不走 ``except Exception``；旧实现在这条路径上
    会留下 ``self._ws`` 与 connector 里的 transport（实测 acquired=1）。
    """
    # 放大 ACK 等待窗口，保证取消确实落在「已升级、还没收到 ACK」的区间内
    monkeypatch.setattr(ws_module, "CONNECTION_TIMEOUT", 30)

    async def main():
        server = await _WSServer("silent").start()
        client = _client(server.port)
        task = asyncio.create_task(client.start())
        for _ in range(200):
            if server.connections >= 1 and client._ws is not None:
                break
            await asyncio.sleep(0.01)
        assert client._ws is not None, "取消前必须先完成 WS 升级"

        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        try:
            assert task.cancelled() is True
            assert client._ws is None
            assert client._connected is False
            assert len(client._session.connector._acquired) == 0
            assert await server.wait_closed(1) is True
        finally:
            await client.disconnect()
            await server.cleanup()

    asyncio.run(main())


def test_connect_closes_stale_ws_before_redial():
    """connect() 开头必须关掉残留连接，而不是直接覆盖 self._ws。"""

    class _FakeWS:
        closed = False

        async def close(self):
            self.closed = True

    async def main():
        server = await _WSServer("non_ack").start()
        client = _client(server.port)
        stale = _FakeWS()
        client._ws = stale
        try:
            assert await client.connect() is False
            assert stale.closed is True  # 残留连接被显式关闭
            assert client._ws is None
        finally:
            await client.disconnect()
            await server.cleanup()

    asyncio.run(main())
