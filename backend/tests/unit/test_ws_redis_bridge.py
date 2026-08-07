# backend/tests/unit/test_ws_redis_bridge.py
"""ws_redis_bridge 单元测试。

全部基于 mock,不需要真实 Redis 服务。覆盖:
- 无 REDIS_URL:publish_* 直接本地投递(_local_dispatch 被调用)。
- 有 REDIS_URL:publish_* 调用 redis client.publish(模拟)。
- configure_local_dispatch 注册回调。
- start_subscriber 在无 REDIS_URL 时为 no-op(不启动线程)。
"""
import json
from unittest import mock

import pytest

from backend.services import ws_redis_bridge


@pytest.fixture(autouse=True)
def _reset_bridge_state():
    """每个测试前后重置模块级缓存状态,避免相互污染。"""
    ws_redis_bridge._reset_for_tests()
    yield
    ws_redis_bridge._reset_for_tests()


# ─────────────────────────────────────────────────────────────
# 无 REDIS_URL(单 worker dev):本地直发路径
# ─────────────────────────────────────────────────────────────

def test_publish_to_account_local_dispatch_when_no_redis():
    """无 REDIS_URL 时 publish_to_account 直接走本地投递。"""
    calls = []
    ws_redis_bridge.configure_local_dispatch(
        lambda kind, acct, msg: calls.append((kind, acct, msg))
    )
    msg = {"type": "trade_update", "trade": {"id": 1}}
    ws_redis_bridge.publish_to_account(42, msg)

    assert len(calls) == 1
    kind, acct, payload = calls[0]
    assert kind == "account"
    assert acct == 42
    assert payload == msg


def test_publish_broadcast_local_dispatch_when_no_redis():
    """无 REDIS_URL 时 publish_broadcast 直接走本地投递(account_id=None)。"""
    calls = []
    ws_redis_bridge.configure_local_dispatch(
        lambda kind, acct, msg: calls.append((kind, acct, msg))
    )
    msg = {"type": "arena_asset_update"}
    ws_redis_bridge.publish_broadcast(msg)

    assert len(calls) == 1
    kind, acct, payload = calls[0]
    assert kind == "broadcast"
    assert acct is None
    assert payload == msg


def test_publish_no_redis_no_dispatch_is_silent():
    """无 Redis 且未注册 dispatch 时,publish 不抛异常(静默)。"""
    # 不 configure_local_dispatch
    ws_redis_bridge.publish_to_account(1, {"x": 1})  # 不应抛
    ws_redis_bridge.publish_broadcast({"x": 1})  # 不应抛


# ─────────────────────────────────────────────────────────────
# 有 REDIS_URL:走 redis client.publish
# ─────────────────────────────────────────────────────────────

def test_publish_to_account_calls_redis_publish():
    """有 REDIS_URL 时 publish_to_account 调用 redis.Redis.publish。"""
    fake_client = mock.MagicMock()
    fake_client.ping.return_value = True

    with mock.patch.object(
        ws_redis_bridge, "_resolve_redis_url", return_value="redis://127.0.0.1:6379/0"
    ), mock.patch("redis.Redis.from_url", return_value=fake_client) as from_url:
        msg = {"type": "snapshot", "data": [1, 2, 3]}
        ws_redis_bridge.publish_to_account(7, msg)

        # lazy 客户端已建立
        from_url.assert_called_once()
        fake_client.ping.assert_called_once()
        # publish 到 ws:account:7,payload 为 JSON
        fake_client.publish.assert_called_once()
        channel, payload = fake_client.publish.call_args.args
        assert channel == "ws:account:7"
        assert json.loads(payload) == msg


def test_publish_broadcast_calls_redis_publish():
    """有 REDIS_URL 时 publish_broadcast 调用 redis.Redis.publish 到 ws:broadcast。"""
    fake_client = mock.MagicMock()
    fake_client.ping.return_value = True

    with mock.patch.object(
        ws_redis_bridge, "_resolve_redis_url", return_value="redis://127.0.0.1:6379/0"
    ), mock.patch("redis.Redis.from_url", return_value=fake_client):
        msg = {"type": "asset_curve_update"}
        ws_redis_bridge.publish_broadcast(msg)

        fake_client.publish.assert_called_once()
        channel, payload = fake_client.publish.call_args.args
        assert channel == "ws:broadcast"
        assert json.loads(payload) == msg


def test_publish_falls_back_to_local_on_redis_failure():
    """Redis publish 抛异常时,回退本地直发(不丢消息)。"""
    fake_client = mock.MagicMock()
    fake_client.ping.return_value = True
    fake_client.publish.side_effect = RuntimeError("redis down")

    calls = []
    ws_redis_bridge.configure_local_dispatch(
        lambda kind, acct, msg: calls.append((kind, acct, msg))
    )

    with mock.patch.object(
        ws_redis_bridge, "_resolve_redis_url", return_value="redis://127.0.0.1:6379/0"
    ), mock.patch("redis.Redis.from_url", return_value=fake_client):
        msg = {"type": "ping"}
        ws_redis_bridge.publish_to_account(9, msg)

    assert len(calls) == 1
    assert calls[0] == ("account", 9, msg)


def test_get_redis_returns_none_when_connect_fails():
    """Redis 连接 ping 失败时 _get_redis 返回 None(走本地兜底)。"""
    fake_client = mock.MagicMock()
    fake_client.ping.side_effect = RuntimeError("connection refused")

    with mock.patch.object(
        ws_redis_bridge, "_resolve_redis_url", return_value="redis://127.0.0.1:6379/0"
    ), mock.patch("redis.Redis.from_url", return_value=fake_client):
        assert ws_redis_bridge._get_redis() is None
        # 再次调用仍返回 None(缓存为 None,不重试)
        assert ws_redis_bridge._get_redis() is None


# ─────────────────────────────────────────────────────────────
# configure_local_dispatch
# ─────────────────────────────────────────────────────────────

def test_configure_local_dispatch_registers_callback():
    """configure_local_dispatch 注册回调,后续 publish 本地路径会调用它。"""
    received = []
    ws_redis_bridge.configure_local_dispatch(
        lambda kind, acct, msg: received.append((kind, acct))
    )
    assert ws_redis_bridge._local_dispatch is not None

    ws_redis_bridge.publish_to_account(3, {"a": 1})
    assert received == [("account", 3)]


# ─────────────────────────────────────────────────────────────
# start_subscriber
# ─────────────────────────────────────────────────────────────

def test_start_subscriber_noop_without_redis():
    """无 REDIS_URL 时 start_subscriber 是 no-op(不启动线程)。"""
    ws_redis_bridge.start_subscriber()
    assert ws_redis_bridge._pubsub_thread is None


def test_start_subscriber_starts_thread_with_redis():
    """有 REDIS_URL 时 start_subscriber 启动后台线程,且幂等。"""
    fake_client = mock.MagicMock()
    fake_client.ping.return_value = True
    fake_pubsub = mock.MagicMock()
    # listen() 返回空迭代,让线程很快自然结束(不会无限阻塞测试)
    fake_pubsub.listen.return_value = iter([])
    fake_client.pubsub.return_value = fake_pubsub

    with mock.patch.object(
        ws_redis_bridge, "_resolve_redis_url", return_value="redis://127.0.0.1:6379/0"
    ), mock.patch("redis.Redis.from_url", return_value=fake_client):
        ws_redis_bridge.start_subscriber()
        # 幂等:第二次调用不新建线程
        first_thread = ws_redis_bridge._pubsub_thread
        ws_redis_bridge.start_subscriber()
        assert ws_redis_bridge._pubsub_thread is first_thread

        # 线程已启动且为 daemon
        assert first_thread is not None
        assert first_thread.daemon is True

        # 确认订阅了正确的频道
        fake_pubsub.psubscribe.assert_called_once_with("ws:account:*", "ws:broadcast")


def test_subscriber_loop_dispatches_account_message():
    """模拟 Redis 订阅收到 ws:account:N 消息时,调用本地 dispatch。"""
    fake_client = mock.MagicMock()
    fake_client.ping.return_value = True
    fake_pubsub = mock.MagicMock()

    received_msg = {
        "type": "pmessage",
        "channel": "ws:account:15",
        "data": json.dumps({"type": "trade_update"}),
    }
    fake_pubsub.listen.return_value = iter([received_msg])
    fake_client.pubsub.return_value = fake_pubsub

    calls = []
    ws_redis_bridge.configure_local_dispatch(
        lambda kind, acct, msg: calls.append((kind, acct, msg))
    )

    with mock.patch.object(
        ws_redis_bridge, "_resolve_redis_url", return_value="redis://127.0.0.1:6379/0"
    ), mock.patch("redis.Redis.from_url", return_value=fake_client):
        ws_redis_bridge.start_subscriber()
        # 等待后台线程处理完(listen 迭代结束后线程退出)
        ws_redis_bridge._pubsub_thread.join(timeout=2)

    assert len(calls) == 1
    kind, acct, msg = calls[0]
    assert kind == "account"
    assert acct == 15
    assert msg == {"type": "trade_update"}


def test_subscriber_loop_dispatches_broadcast_message():
    """模拟 Redis 订阅收到 ws:broadcast 消息时,调用本地 dispatch(account_id=None)。"""
    fake_client = mock.MagicMock()
    fake_client.ping.return_value = True
    fake_pubsub = mock.MagicMock()

    received_msg = {
        "type": "pmessage",
        "channel": "ws:broadcast",
        "data": json.dumps({"type": "arena_asset_update"}),
    }
    fake_pubsub.listen.return_value = iter([received_msg])
    fake_client.pubsub.return_value = fake_pubsub

    calls = []
    ws_redis_bridge.configure_local_dispatch(
        lambda kind, acct, msg: calls.append((kind, acct, msg))
    )

    with mock.patch.object(
        ws_redis_bridge, "_resolve_redis_url", return_value="redis://127.0.0.1:6379/0"
    ), mock.patch("redis.Redis.from_url", return_value=fake_client):
        ws_redis_bridge.start_subscriber()
        ws_redis_bridge._pubsub_thread.join(timeout=2)

    assert len(calls) == 1
    kind, acct, msg = calls[0]
    assert kind == "broadcast"
    assert acct is None
    assert msg == {"type": "arena_asset_update"}
