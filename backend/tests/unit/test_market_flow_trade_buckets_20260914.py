# -*- coding: utf-8 -*-
"""[2026-09-14 F171] 成交桶必须按**成交自身时间戳**分桶（数据层根因修复）。

缺陷（实测）：`base_collector` 把整批成交塞进"**落库时刻**所在的 15s 桶"，而 trades
轮询是 30s 一次 ⇒ 一次轮询的 ~30s 成交全进同一个桶、相邻桶整片为空 ✗。
后果：15s 网格覆盖率仅 **47.5%**（快照 90.8%）；桶时间戳最多偏差 **15~30s** ⇒
"哪笔成交打到哪张单"不确定 ⇒ 回放高出实盘 ~2.5bp（F148），实盘账本判据本身不可信 ✗。

契约：
  ① 成交进入**它自己时间戳**的桶（不是落库时刻）；
  ② **未结束的桶不落库**（避免半成品被做市判定读走）；
  ③ 落库是**幂等的**（写累计值）⇒ 迟到成交可补写进已写过的桶；
  ④ 宽限期内保留缓冲以吸收迟到成交，之后释放（内存有界）；
  ⑤ 异常时间戳被钳制，不会造成无界内存。

注意：桶标签是 **15s 网格对齐**的（`floor(ts/15000)*15000`）⇒ 测试常量必须落在网格上，
否则会误判成两个不同的桶 ✗（第一版本就踩了这个坑）。
"""
from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.market_flow.trade_buckets import TradeBucketStore  # noqa: E402

W = 15_000
G0 = 100 * W          # 1,500,000：网格对齐的基准标签


def _store() -> TradeBucketStore:
    return TradeBucketStore(window_ms=W, grace_buckets=2)


def test_trade_goes_to_its_own_timestamp_bucket_not_flush_time():
    """①：核心契约——桶标签来自**成交时间戳**，不是落库时刻。"""
    s = _store()
    now = G0 + 2 * W + 3_000           # 落库时刻落在后面的桶
    lab = s.add("BTC", price=Decimal("100"), size=Decimal("1"),
                is_taker_buy=True, ts_ms=G0, now_ms=now)
    assert lab == G0, "成交必须进入它自己的桶，而不是落库时刻的桶"
    assert s.open_volume("BTC") == Decimal("0"), "当前桶应仍为空"


def test_open_bucket_is_not_ready_for_flush():
    """②：未结束的桶不得出现在 ready() 里（否则做市会读到半成品 ✗）。"""
    s = _store()
    now = G0 + 3_000                   # 仍在同一桶内
    s.add("BTC", price=Decimal("100"), size=Decimal("1"),
          is_taker_buy=True, ts_ms=G0, now_ms=now)
    assert s.ready(now_ms=now) == [], "桶未结束就不该可落库"
    later = G0 + W + 1_000
    ready = s.ready(now_ms=later)
    assert [(sym, lab) for sym, lab, _ in ready] == [("BTC", G0)]


def test_late_trade_accumulates_into_already_closed_bucket():
    """③④：迟到成交累加进已关闭的桶（幂等 upsert 的输入 = 累计值 ✓）。"""
    s = _store()
    s.add("BTC", price=Decimal("100"), size=Decimal("1"),
          is_taker_buy=True, ts_ms=G0, now_ms=G0 + 1_000)
    later = G0 + W + 1_000
    ready = s.ready(now_ms=later)
    assert len(ready) == 1 and ready[0][2].total_volume == Decimal("1")
    # 迟到的第 2 笔：时间戳仍属同一个桶（G0 + 5s）
    s.add("BTC", price=Decimal("101"), size=Decimal("2"),
          is_taker_buy=False, ts_ms=G0 + 5_000, now_ms=later)
    ready2 = s.ready(now_ms=later)
    vol = ready2[0][2]
    assert vol.total_volume == Decimal("3"), "迟到成交必须累加（否则丢失 ✗）"
    assert vol.high_price == Decimal("101") and vol.low_price == Decimal("100")


def test_grace_prune_bounds_memory():
    """④：宽限期过后释放已关闭桶（内存有界）。"""
    s = _store()
    s.add("BTC", price=Decimal("100"), size=Decimal("1"),
          is_taker_buy=True, ts_ms=G0, now_ms=G0 + 1_000)
    far = G0 + 10 * W
    s.ready(now_ms=far)
    n = s.prune(now_ms=far)
    assert n >= 1 and s.closed == {}, "超过宽限期的桶必须释放"


def test_absurd_timestamp_is_clamped():
    """⑤：异常时间戳被钳制（不会造成无界内存或未来标签）。"""
    s = _store()
    now = G0 + 4 * W
    lab = s.add("BTC", price=Decimal("100"), size=Decimal("1"),
                is_taker_buy=True, ts_ms=0, now_ms=now)
    assert now - 10 * W <= lab <= now
    lab2 = s.add("BTC", price=Decimal("100"), size=Decimal("1"),
                 is_taker_buy=True, ts_ms=now + 10 * W, now_ms=now)
    assert lab2 <= now


def test_vwap_and_sides_are_accumulated():
    s = _store()
    s.add("BTC", price=Decimal("10"), size=Decimal("1"),
          is_taker_buy=True, ts_ms=G0, now_ms=G0 + 100)
    s.add("BTC", price=Decimal("30"), size=Decimal("1"),
          is_taker_buy=False, ts_ms=G0 + 100, now_ms=G0 + 200)
    ready = s.ready(now_ms=G0 + W + 100)
    b = ready[0][2]
    assert b.taker_buy_volume == Decimal("1") and b.taker_sell_volume == Decimal("1")
    assert b.vwap == Decimal("20")
    assert b.high_price == Decimal("30") and b.low_price == Decimal("10")


def test_base_collector_is_wired_to_the_store():
    """源码契约：采集器必须用新分桶（否则又回到"按落库时刻分桶" ✗）。"""
    import inspect
    from backend.services.market_flow import base_collector as bc
    src = inspect.getsource(bc)
    assert "TradeBucketStore" in src, "必须使用按成交时间戳分桶的存储"
    on_trade = inspect.getsource(bc.BaseMarketFlowCollector._on_trade)
    assert "trade_buckets.add(" in on_trade, "_on_trade 必须把成交交给分桶存储"
    flush = inspect.getsource(bc.BaseMarketFlowCollector._flush_to_database)
    assert "ready(" in flush and "prune(" in flush, "落库必须先取已结束桶再释放宽限期桶"
