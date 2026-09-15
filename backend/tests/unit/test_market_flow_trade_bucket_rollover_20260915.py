# -*- coding: utf-8 -*-
"""[F192 2026-09-15] 成交桶"跨桶未归位"崩溃的回归测试（KeyError）。

现场（生产日志，非构造）：
    08:01:32 起每个币每轮都出现
        `[asterdex] poll_trades XRP/USDT:USDT 异常: 'XRP'，10s 后重试`
    5 小时累计 **3612 次**；加上 `exc_info=True` 后拿到栈：
        asterdex_collector.py:245 in _poll_trades_loop → self._on_trade(...)
        base_collector.py:279      in _on_trade        → self.trade_buckets.add(...)
        trade_buckets.py:103       in add              → buf = self.open[symbol]
        KeyError: 'SOL'

根因：`add()` 在"当前桶标签变了但还没有人调用 ready()/promote"的分支里，先调用
`_promote_symbol()`（它会把已结束的当前桶移入 `closed` 并从 `self.open` 中 **pop**），
紧接着又去**读** `self.open[symbol]` ⇒ 必然 KeyError ✗。

为什么这条必须用测试钉住（而不是"改完就算"）：
  1. 它是**链路断点**类缺陷：异常发生在 `_on_trade` 内 ⇒ 同一批成交里**之后的成交
     全部丢失** ✗（做市链路唯一的成交判据就是这张表，F112）；
  2. 触发条件是"新桶的第一笔成交早于 promote" ⇒ **间歇性**（实测约 60% 的轮询命中），
     肉眼看数据"还在动"就很容易漏掉 ✗（这次就是靠日志计数才发现的）；
  3. 它同时污染**参数搜索的数据**（我据此跑的 F189 扫描就建立在这份数据上）⇒
     修好后必须重跑复核 ✓。
"""
from __future__ import annotations

import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))))

from backend.services.market_flow.trade_buckets import TradeBucketStore  # noqa: E402

W = 15_000
T0 = 1_700_000_000_000 - (1_700_000_000_000 % W)   # 对齐到桶边界
L0 = T0
L1 = T0 + W
L2 = T0 + 2 * W


def _store() -> TradeBucketStore:
    return TradeBucketStore(window_ms=W, grace_buckets=2)


def test_rollover_without_promote_does_not_raise():
    """核心回归：跨桶后**没人调用 ready()**，直接来新桶的成交，不得抛异常。"""
    s = _store()
    s.add("SOL", price=Decimal("100"), size=Decimal("2"), is_taker_buy=True,
          ts_ms=L0 + 1000, now_ms=L0 + 1000)
    # 下一桶的成交（此刻 cur == L1，而 open_label 仍是 L0 ⇒ 走 promote 分支）
    s.add("SOL", price=Decimal("101"), size=Decimal("3"), is_taker_buy=False,
          ts_ms=L1 + 2000, now_ms=L1 + 2000)
    # 已结束的 L0 必须被归位到 closed（不能被丢掉）
    assert L0 in s.closed["SOL"], f"L0 未归位: {sorted(s.closed.get('SOL', {}))}"
    assert s.closed["SOL"][L0].total_volume == Decimal("2")
    assert s.closed["SOL"][L0].taker_buy_volume == Decimal("2")
    # 新桶 L1 必须从空开始并收下这笔
    assert s.open_label["SOL"] == L1
    assert s.open["SOL"].total_volume == Decimal("3")
    assert s.open["SOL"].taker_sell_volume == Decimal("3")


def test_rollover_keeps_subsequent_trades_in_same_batch():
    """异常发生在 `_on_trade` 内 ⇒ 会丢掉**同一批的后续成交**；这里钉住不丢。"""
    s = _store()
    s.add("BTC", price=Decimal("10"), size=Decimal("1"), is_taker_buy=True,
          ts_ms=L0 + 500, now_ms=L0 + 500)
    # 模拟一次轮询拿到"跨桶"的一批：第 1 笔跨桶（旧代码在此抛），后面 3 笔同批
    for i in range(3):
        s.add("BTC", price=Decimal("11"), size=Decimal("1"), is_taker_buy=True,
              ts_ms=L1 + 100 * (i + 1), now_ms=L1 + 100 * (i + 1))
    assert s.open["BTC"].total_volume == Decimal("3"), "跨桶那批的后续成交被丢了 ✗"
    assert s.open["BTC"].taker_buy_count == 3


def test_multi_step_rollover_is_consistent():
    """连续跨多桶（中间不 ready）：每一步都要把**上一个**桶归位、当前桶重建。"""
    s = _store()
    for lab, sz in ((L0, "1"), (L1, "2"), (L2, "3")):
        s.add("ETH", price=Decimal("50"), size=Decimal(sz), is_taker_buy=True,
              ts_ms=lab + 10, now_ms=lab + 10)
    assert s.closed["ETH"][L0].total_volume == Decimal("1")
    assert s.closed["ETH"][L1].total_volume == Decimal("2")
    assert s.open_label["ETH"] == L2
    assert s.open["ETH"].total_volume == Decimal("3")


def test_late_trade_still_goes_to_closed_bucket():
    """回归保护：迟到成交（早于当前桶）仍然直接进 closed 的对应桶 ✓。"""
    s = _store()
    s.add("XRP", price=Decimal("1"), size=Decimal("5"), is_taker_buy=False,
          ts_ms=L1 + 100, now_ms=L1 + 100)
    # 一笔属于 L0 的迟到成交（now 仍在 L1）
    s.add("XRP", price=Decimal("1"), size=Decimal("7"), is_taker_buy=False,
          ts_ms=L0 + 900, now_ms=L1 + 200)
    assert s.closed["XRP"][L0].total_volume == Decimal("7")
    assert s.open["XRP"].total_volume == Decimal("5")


def test_ready_after_rollover_returns_only_finished_buckets():
    """ready() 的契约不变：只返回**已结束**桶，且每个桶恰好一次。"""
    s = _store()
    s.add("BNB", price=Decimal("2"), size=Decimal("1"), is_taker_buy=True,
          ts_ms=L0 + 10, now_ms=L0 + 10)
    s.add("BNB", price=Decimal("2"), size=Decimal("2"), is_taker_buy=True,
          ts_ms=L1 + 10, now_ms=L1 + 10)          # 触发归位 L0
    out = s.ready(now_ms=L1 + 5000)             # cur = L1 ⇒ 只有 L0 可落库
    got = [(sym, lab, st.total_volume) for sym, lab, st in out]
    assert got == [("BNB", L0, Decimal("1"))], got
    # 关键契约（与 20260914 那组测试一致）：
    #   ① `ready()` **不会**丢掉"还没人 promote 的当前桶" —— 时间推进后它会先归位、
    #      再被返回（否则最后一段成交量永远不落库 ✗）；重复返回是**设计允许**的，
    #      因为写库是幂等的（写"累计值"）；
    #   ② `prune()` 超过宽限期后才真正释放 ⇒ 之后不再返回 ✓。
    out2 = s.ready(now_ms=L1 + 20_000)
    # L0 仍在宽限期内 ⇒ 会被**再次**返回（幂等写库允许 ✓）；L1 此时已被归位并返回 ✓
    assert [(sym, lab, st.total_volume) for sym, lab, st in out2] == [
        ("BNB", L0, Decimal("1")), ("BNB", L1, Decimal("2"))], out2
    s.prune(now_ms=L1 + 60_000)                 # 远超宽限期 ⇒ 释放
    assert s.ready(now_ms=L1 + 60_000) == [], "prune 之后仍返回陈旧桶 ✗"
