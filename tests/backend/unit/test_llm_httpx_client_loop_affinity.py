"""
批次二 2.3 回归测试：LLM httpx AsyncClient 的 event loop 感知

根因：_get_httpx_client 全局缓存 httpx.AsyncClient，但 AsyncClient 绑定创建时的
event loop。当后台 task（_run_news_fetch / _run_daily_journal）用
new_event_loop → run_until_complete → loop.close() 三段式后，缓存的 client 仍指向
已关闭的 loop，下次复用报 "Event loop is closed"（日志 6 次）。

修复：缓存按 (cache_key, loop_id) 索引；取用时检查当前 running loop id，不一致则
关闭旧 client 重建。

本测试覆盖（不依赖真实 HTTP）：
1. 同一 loop 内复用同一 client（缓存命中）。
2. loop 变化（模拟 loop.close 后新 loop）时重建 client，不返回绑定旧 loop 的 client。
3. client 已关闭时重建。
4. _evict_oldest_client 兼容 (client, loop_id) 元组形态。
"""
import asyncio
import pytest
from unittest.mock import patch, MagicMock


pytestmark = pytest.mark.unit


def _close_async(client):
    """测试辅助：同步关闭 AsyncClient（aclose 是 async）。"""
    import asyncio

    try:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(client.aclose())
        finally:
            loop.close()
    except Exception:
        pass


def test_same_loop_reuses_client():
    """同一 running loop 下，两次取 client 应复用同一实例（缓存命中）。"""
    from backend.services import llm_config_service as svc

    with svc._httpx_clients_lock:
        svc._httpx_clients.clear()

    fake_loop = MagicMock()  # 同一个 loop 对象，id 一致
    with patch.object(asyncio, "get_running_loop", return_value=fake_loop):
        c1 = svc._get_httpx_client("https://api.a.com", "key1")
        c2 = svc._get_httpx_client("https://api.a.com", "key1")
    assert c1 is c2

    _close_async(c1)
    with svc._httpx_clients_lock:
        svc._httpx_clients.clear()


def test_loop_change_rebuilds_client():
    """loop 变化（loop.close 后新 loop）时，应重建 client 不返回绑定旧 loop 的。

    注意：AsyncClient 只有 async aclose()，同步上下文无法优雅关闭，旧 client 仅从
    缓存摘除交由 GC。因此不断言 c1.is_closed，只断言拿到的是新实例 + 缓存已更新。
    """
    from backend.services import llm_config_service as svc

    with svc._httpx_clients_lock:
        svc._httpx_clients.clear()

    loop_a = MagicMock()
    loop_b = MagicMock()  # 不同对象，id 不同，模拟新 loop
    with patch.object(asyncio, "get_running_loop", return_value=loop_a):
        c1 = svc._get_httpx_client("https://api.b.com", "key2")
    with patch.object(asyncio, "get_running_loop", return_value=loop_b):
        c2 = svc._get_httpx_client("https://api.b.com", "key2")

    # 必须是新实例，不能复用绑定旧 loop 的 c1
    assert c1 is not c2
    # 新 client 绑定的应是 loop_b 的 id
    with svc._httpx_clients_lock:
        cache_key = svc._safe_cache_key("https://api.b.com", "key2")
        cached_client, cached_loop_id = svc._httpx_clients[cache_key]
    assert cached_client is c2
    assert cached_loop_id == id(loop_b)

    _close_async(c1)
    _close_async(c2)
    with svc._httpx_clients_lock:
        svc._httpx_clients.clear()


def test_closed_client_rebuilds():
    """缓存的 client 已被外部关闭（is_closed=True）时，应重建。"""
    from backend.services import llm_config_service as svc

    with svc._httpx_clients_lock:
        svc._httpx_clients.clear()

    fake_loop = MagicMock()
    with patch.object(asyncio, "get_running_loop", return_value=fake_loop):
        c1 = svc._get_httpx_client("https://api.c.com", "key3")
        _close_async(c1)  # 外部关闭（aclose）
        assert c1.is_closed
        c2 = svc._get_httpx_client("https://api.c.com", "key3")
    assert c1 is not c2
    assert not c2.is_closed

    _close_async(c2)
    with svc._httpx_clients_lock:
        svc._httpx_clients.clear()


def test_evict_handles_tuple_entries():
    """_evict_oldest_client 兼容 (client, loop_id) 元组形态（async 缓存）。

    AsyncClient 无同步 close()，evict 仅摘除不主动关闭（不抛错即正确）。
    """
    from backend.services import llm_config_service as svc

    fake_a = MagicMock()
    fake_a.is_closed = False
    # AsyncClient 无 close 方法，模拟 hasattr 返回 False
    del fake_a.close
    fake_b = MagicMock()
    fake_b.is_closed = False

    clients = {"k1": (fake_a, 1), "k2": (fake_b, 2)}
    # 不应因 AsyncClient 无 close 而抛 AttributeError
    svc._evict_oldest_client(clients, max_size=1)
    assert "k1" not in clients
    assert "k2" in clients
