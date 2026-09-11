# -*- coding: utf-8 -*-
"""signal_ledger 超额收益基准口径回归测试（2026-09-04）。

背景：`_score_direction` 对 symbol=BTC 的信号也扣减 BTC 基准，而
`ret_bp = direction × ret_btc`、`btc_ret_bp = ret_btc`，相减**恒等于 0**。
这不只是少了个指标 —— `signal_review` 的 verdict / IC / 半衰期 / regime 分层
全部基于 excess_bp，excess 恒 0 会让 _verdict 三个分支全不成立，
BTC 信号永远返回 keep，再差也停不掉。

实测 e5_5_news_hedge：7 笔里 6 笔 BTC，命中率 28.6%、平均 -115bp，
avg_excess 却被稀释到 -2.5bp，长期停在 observe。
"""
from __future__ import annotations

import pytest

from backend.services.analysis.ledgers import _is_benchmark_symbol, _score_direction


@pytest.mark.parametrize("sym,expected", [
    ("BTC", True), ("btc", True), ("BTCUSDT", True), ("BTC-USDT", True),
    ("BTC/USDT", True), ("BTCUSD", True), ("XBT", True), ("BTCPERP", True),
    ("ETH", False), ("ETHUSDT", False), ("SOL", False), ("", False), (None, False),
])
def test_基准标的识别(sym, expected):
    assert _is_benchmark_symbol(sym) is expected


def test_btc信号的超额不再恒为零():
    """BTC 看跌、BTC 同期下跌 1% → 方向正确，超额应等于方向调整后收益。"""
    p0, p1 = 100.0, 99.0          # 标的跌 1%
    b0, b1 = 100.0, 99.0          # 基准同为 BTC，同一条价格
    ret_bp, btc_ret_bp, excess_bp, hit, _ = _score_direction(
        -1, p0, p1, b0, b1, 0.6, symbol="BTC",
    )
    assert ret_bp == pytest.approx(100.0)      # 看跌且跌 → +100bp
    assert hit == 1
    assert excess_bp == pytest.approx(ret_bp), "标的即基准时不应扣减基准"
    assert excess_bp != 0.0, "旧实现在此恒为 0，导致 BTC 信号永远判不出好坏"


def test_btc信号方向错误时超额显著为负():
    """这是修复的意义所在：错的 BTC 信号必须能在 excess 上体现出来。"""
    ret_bp, _, excess_bp, hit, _ = _score_direction(
        -1, 100.0, 104.72, 100.0, 104.72, 0.65, symbol="BTC",
    )
    assert hit == 0
    assert ret_bp < 0
    assert excess_bp == pytest.approx(ret_bp)
    assert excess_bp < -400, "看跌却涨 4.7%，超额须显著为负才可能触发停用"


def test_非基准标的仍然扣减基准():
    """SOL 涨 2% 而 BTC 涨 1%，看涨 SOL 的超额应是 +100bp 而非 +200bp。"""
    ret_bp, btc_ret_bp, excess_bp, hit, _ = _score_direction(
        1, 100.0, 102.0, 50000.0, 50500.0, 0.6, symbol="SOL",
    )
    assert ret_bp == pytest.approx(200.0)
    assert btc_ret_bp == pytest.approx(100.0)
    assert excess_bp == pytest.approx(100.0), "非基准标的必须保留相对基准的超额口径"


def test_做空非基准标的的超额方向正确():
    """看跌 SOL：SOL 跌 2%、BTC 涨 1% → 相对基准跑赢，超额应为正且大于绝对收益。"""
    ret_bp, _, excess_bp, _, _ = _score_direction(
        -1, 100.0, 98.0, 50000.0, 50500.0, 0.6, symbol="SOL",
    )
    assert ret_bp == pytest.approx(200.0)
    assert excess_bp == pytest.approx(300.0)


def test_无基准价时退回绝对收益():
    _, btc_ret_bp, excess_bp, _, _ = _score_direction(
        1, 100.0, 101.0, None, None, 0.5, symbol="SOL",
    )
    assert btc_ret_bp is None
    assert excess_bp == pytest.approx(100.0)


def test_中性信号不受影响():
    """direction=0 的语义是「越不动越好」，与基准无关。"""
    ret_bp, _, excess_bp, hit, _ = _score_direction(
        0, 100.0, 100.2, 50000.0, 50500.0, 0.5, symbol="BTC",
    )
    assert ret_bp == pytest.approx(-20.0)
    assert hit == 1                      # 波动 20bp < 50bp
    assert excess_bp == pytest.approx(ret_bp)
