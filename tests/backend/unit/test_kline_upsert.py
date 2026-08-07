"""
批次一 1.1 回归测试：kline_repo upsert 修复 UniqueViolation

根因：旧 save_kline_data 用「SELECT 查重 + add() + commit()」的 check-then-insert，
并发写同一根 K 线时在 commit 撞 crypto_klines 唯一约束抛 UniqueViolation（日志 200 次）。
修复：改用 dialect.insert_on_conflict_do_update 的原子 upsert。

本测试覆盖：
1. 重复写入同一根 K 线不抛异常，且 OHLCV 被更新（保留"存在则更新"语义）。
2. 同一批次内含重复 timestamp 也不崩（模拟并发/重试场景）。
3. 重复调用 save_kline_data 两次（串行，模拟两个 worker 各自落库）不崩。
"""
import pytest
from sqlalchemy.exc import IntegrityError

from backend.repositories.kline_repo import KlineRepository


pytestmark = pytest.mark.unit


def _make_kline(ts, o=100.0, h=110.0, l=95.0, c=105.0, v=1000.0):
    return {
        "timestamp": ts,
        "datetime": "2026-06-17 00:00:00",
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "volume": v,
        "amount": 100000.0,
        "chg": 5.0,
        "percent": 5.0,
    }


def test_save_then_update_same_kline_no_violation(db_session):
    """重复写同一根 K 线：第二次应更新而非抛 UniqueViolation。"""
    repo = KlineRepository(db_session)

    # 第一次写入
    result1 = repo.save_kline_data(
        "BTC", "CRYPTO", "5m", [_make_kline(1781629500)], exchange="hyperliquid"
    )
    assert result1["total"] == 1

    # 第二次写同一根（不同 OHLCV），不应抛异常，且值被更新
    result2 = repo.save_kline_data(
        "BTC", "CRYPTO", "5m",
        [_make_kline(1781629500, o=200.0, c=205.0)],
        exchange="hyperliquid",
    )
    assert result2["total"] == 1  # upsert 仍计入 total

    # 验证库里只有一条，且 close 已更新为 205
    from backend.database.models import CryptoKline
    rows = (
        db_session.query(CryptoKline)
        .filter(CryptoKline.symbol == "BTC", CryptoKline.period == "5m")
        .all()
    )
    assert len(rows) == 1
    assert float(rows[0].close_price) == 205.0


def test_batch_with_duplicate_timestamp_in_same_call(db_session):
    """单次调用内含重复 timestamp（模拟并发 tick 投递）不应崩。"""
    repo = KlineRepository(db_session)
    klines = [
        _make_kline(1781629500, c=100.0),
        _make_kline(1781629500, c=300.0),  # 同 timestamp，重复
        _make_kline(1781629800, c=110.0),  # 不同 timestamp
    ]
    # 不应抛 UniqueViolation / IntegrityError
    result = repo.save_kline_data("ETH", "CRYPTO", "5m", klines, exchange="hyperliquid")
    assert result["total"] == 3  # 3 条都进入 upsert

    from backend.database.models import CryptoKline
    rows = (
        db_session.query(CryptoKline)
        .filter(CryptoKline.symbol == "ETH", CryptoKline.period == "5m")
        .all()
    )
    # 2 个不同 timestamp = 2 行（重复的合并）
    assert len(rows) == 2


def test_two_workers_serial_insert_no_violation(db_session):
    """两个独立 repo 实例（模拟两个 worker）先后写同一根 K 线，都不抛异常。"""
    repo_a = KlineRepository(db_session)
    repo_b = KlineRepository(db_session)

    repo_a.save_kline_data("SOL", "CRYPTO", "15m", [_make_kline(1781630000)], exchange="hyperliquid")
    # 第二个 worker 写同一根 — 旧实现这里会抛 UniqueViolation
    repo_b.save_kline_data("SOL", "CRYPTO", "15m", [_make_kline(1781630000, c=999.0)], exchange="hyperliquid")

    from backend.database.models import CryptoKline
    rows = (
        db_session.query(CryptoKline)
        .filter(CryptoKline.symbol == "SOL", CryptoKline.period == "15m")
        .all()
    )
    assert len(rows) == 1
    assert float(rows[0].close_price) == 999.0


def test_empty_input_returns_zero(db_session):
    """空输入返回 total=0，不触发任何 SQL。"""
    repo = KlineRepository(db_session)
    assert repo.save_kline_data("BTC", "CRYPTO", "5m", [], exchange="hyperliquid")["total"] == 0
    # 缺 timestamp 的记录被过滤
    assert repo.save_kline_data(
        "BTC", "CRYPTO", "5m", [{"open": 1}], exchange="hyperliquid"
    )["total"] == 0
