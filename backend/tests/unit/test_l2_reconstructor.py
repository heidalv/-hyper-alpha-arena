"""L2 订单簿重建层单测（v6 阶段 2 第 7 项：l2_reconstructor + 因子自动切换钩子）。"""
import numpy as np
import pandas as pd
import pytest

from backend.services.market_flow.l2_reconstructor import (
    KlineDepthAggregator,
    L2Reconstructor,
    OrderBookFrame,
)
from backend.services.factor_engine.factors.derivatives.orderflow_crypto_factors import (
    L2DepthImbalanceFactor,
    _proxy_signed_volume,
)


def _book(bids, asks):
    return OrderBookFrame(exchange="hyperliquid", symbol="BTC",
                          bids=[(float(p), float(s)) for p, s in bids],
                          asks=[(float(p), float(s)) for p, s in asks],
                          ts=1_700_000_000.0)


# ─────────────────────────── 快照摄入与派生指标 ───────────────────────────

def test_ingest_hl_levels_format():
    """Hyperliquid l2Book levels 格式（{"px","sz"}）摄入正确。"""
    rec = L2Reconstructor()
    levels = [
        [{"px": "100.0", "sz": "2.0"}, {"px": "99.5", "sz": "1.5"}],
        [{"px": "100.5", "sz": "3.0"}, {"px": "101.0", "sz": "2.5"}],
    ]
    frame = rec.ingest_hl("hyperliquid", "BTC", levels, ts=123.0)
    assert frame.best_bid() == 100.0
    assert frame.best_ask() == 100.5
    assert len(frame.bids) == 2 and len(frame.asks) == 2
    assert rec.latest("hyperliquid", "BTC") is frame


def test_ingest_book_generic_format():
    """通用 [price, size] 格式（与 hub L2Snapshot 兼容）。"""
    rec = L2Reconstructor()
    frame = rec.ingest_book("hyperliquid", "ETH",
                            [[2000.0, 5.0], [1999.0, 3.0]],
                            [[2001.0, 4.0]], ts=456.0)
    assert frame.mid_price() == 2000.5
    assert frame.best_bid() == 2000.0


def test_derived_metrics():
    f = _book([(100.0, 2.0), (99.0, 1.0)], [(101.0, 3.0), (102.0, 2.0)])
    assert f.mid_price() == 100.5
    assert abs(f.spread_bps() - 10000 / 100.5) < 1e-6  # 1 元 / 100.5 元
    top = f.top_n(1)
    assert top.bids == [(100.0, 2.0)] and top.asks == [(101.0, 3.0)]
    bid_d, ask_d = f.notional_depth(2)
    assert bid_d == 100.0 * 2 + 99.0 * 1
    assert ask_d == 101.0 * 3 + 102.0 * 2
    # 深度失衡：bid 名义 299，ask 名义 507
    imb = f.depth_imbalance(2)
    assert abs(imb - (299 - 507) / (299 + 507)) < 1e-9


def test_ingest_filters_bad_rows():
    rec = L2Reconstructor()
    frame = rec.ingest_book("hyperliquid", "BTC",
                            [[100.0, 2.0], [0.0, 5.0], [-1.0, 3.0], ["x", "y"]],
                            [[101.0, 2.0]])
    assert frame.bids == [(100.0, 2.0)]  # 非法档位被过滤
    assert len(frame.asks) == 1


# ─────────────────────────── 插针防护 ───────────────────────────

def test_price_jump_guard_rejects():
    rec = L2Reconstructor(max_price_jump_pct=2.0)
    rec.ingest_book("hyperliquid", "BTC", [[100.0, 1.0]], [[100.1, 1.0]], ts=1.0)
    # 跳变 > 2% → 拒绝，返回上一帧
    rejected = rec.ingest_book("hyperliquid", "BTC", [[110.0, 1.0]], [[110.1, 1.0]], ts=2.0)
    assert rejected.mid_price() == pytest.approx(100.05)
    assert rec.latest("hyperliquid", "BTC").mid_price() == pytest.approx(100.05)
    assert rec.jump_rejected == 1


def test_price_jump_guard_allows_small():
    rec = L2Reconstructor(max_price_jump_pct=2.0)
    rec.ingest_book("hyperliquid", "BTC", [[100.0, 1.0]], [[100.1, 1.0]], ts=1.0)
    cur = rec.ingest_book("hyperliquid", "BTC", [[100.5, 1.0]], [[100.6, 1.0]], ts=2.0)
    assert cur.mid_price() == pytest.approx(100.55)
    assert rec.jump_rejected == 0


def test_no_guard_when_disabled():
    rec = L2Reconstructor()  # max_price_jump_pct=0 → 不拦截
    rec.ingest_book("hyperliquid", "BTC", [[100.0, 1.0]], [[100.1, 1.0]], ts=1.0)
    cur = rec.ingest_book("hyperliquid", "BTC", [[200.0, 1.0]], [[200.1, 1.0]], ts=2.0)
    assert cur.mid_price() == pytest.approx(200.05)
    assert rec.jump_rejected == 0


# ─────────────────────────── OFI / CVD ───────────────────────────

def test_compute_ofi_standard_formula():
    """手工构造：100 档买盘 +1 → OFI 应 +1；101 档卖盘 +2 → OFI 应 −2。"""
    prev = _book([(100.0, 2.0)], [(101.0, 3.0)])
    cur = _book([(100.0, 3.0)], [(101.0, 5.0)])
    assert L2Reconstructor.compute_ofi(prev, cur, levels=5) == pytest.approx(1.0 - 2.0)


def test_compute_ofi_none_prev_zero():
    cur = _book([(100.0, 2.0)], [(101.0, 3.0)])
    assert L2Reconstructor.compute_ofi(None, cur) == 0.0


def test_compute_cvd_sides():
    trades = [
        (100.0, 2.0, "buy"),
        (101.0, 1.5, "sell"),
        (102.0, 0.5, "b"),   # 别名 buy
        (103.0, 1.0, "s"),   # 别名 sell
    ]
    assert L2Reconstructor.compute_cvd(trades) == pytest.approx(2.0 - 1.5 + 0.5 - 1.0)


def test_compute_cvd_empty():
    assert L2Reconstructor.compute_cvd([]) == 0.0


# ─────────────────────────── K 线聚合与补列 ───────────────────────────

def test_aggregate_buckets_and_taker():
    """5 分钟桶聚合：帧入桶 + 逐笔 taker buy 累计。"""
    t0 = 1_700_000_000.0
    agg = KlineDepthAggregator(bar_seconds=300)
    frames = [
        OrderBookFrame("hyperliquid", "BTC", [(100.0, 2.0)], [(101.0, 3.0)], ts=t0),
        OrderBookFrame("hyperliquid", "BTC", [(100.5, 4.0)], [(101.0, 2.0)], ts=t0 + 60),
        OrderBookFrame("hyperliquid", "BTC", [(101.0, 5.0)], [(101.5, 6.0)], ts=t0 + 400),  # 下个桶
    ]
    trades = [
        (t0 + 10, 100.2, 1.5, "buy"),
        (t0 + 20, 100.3, 0.5, "sell"),
        (t0 + 500, 101.2, 3.0, "buy"),
    ]
    out = agg.aggregate(frames, trades)
    b0 = int(t0 // 300) * 300
    b1 = int((t0 + 400) // 300) * 300
    assert set(out) == {b0, b1}
    # 桶 0：taker buy 只计 1.5（sell 不计）
    assert out[b0]["taker_buy_volume"] == pytest.approx(1.5)
    assert out[b1]["taker_buy_volume"] == pytest.approx(3.0)
    # 桶 0 末帧深度（100.5/101.0 档）
    assert out[b0]["bid_depth_top5"] == pytest.approx(100.5 * 4.0)
    assert out[b0]["ask_depth_top5"] == pytest.approx(101.0 * 2.0)
    # 桶 0 spread 均值（两帧）
    assert out[b0]["spread_bps"] > 0
    assert out[b0]["depth_imbalance"] == pytest.approx(
        (100.5 * 4 - 101.0 * 2) / (100.5 * 4 + 101.0 * 2)
    )


def test_attach_depth_columns_to_kline_df():
    t0 = 1_700_000_000.0
    df = pd.DataFrame({
        "timestamp": [t0, t0 + 60, t0 + 300, t0 + 360],
        "close": [100.0, 100.5, 101.0, 101.2],
        "volume": [10.0, 12.0, 8.0, 9.0],
    })
    frames = [
        OrderBookFrame("hyperliquid", "BTC", [(100.0, 2.0)], [(101.0, 3.0)], ts=t0),
        OrderBookFrame("hyperliquid", "BTC", [(101.0, 5.0)], [(101.5, 6.0)], ts=t0 + 300),
    ]
    trades = [(t0 + 10, 100.2, 1.5, "buy")]
    agg = KlineDepthAggregator(bar_seconds=300)
    out = agg.attach_depth_columns(df, frames, trades)
    for c in KlineDepthAggregator.COLUMNS:
        assert c in out.columns
    # 前两根 K 线在桶 0 → taker 1.5；后两根在桶 1 → NaN
    assert out["taker_buy_volume"].iloc[0] == pytest.approx(1.5)
    assert out["taker_buy_volume"].iloc[1] == pytest.approx(1.5)
    assert np.isnan(out["taker_buy_volume"].iloc[2])
    assert out["bid_depth_top5"].iloc[0] == pytest.approx(100.0 * 2.0)
    assert out["bid_depth_top5"].iloc[2] == pytest.approx(101.0 * 5.0)


def test_attach_empty_frames_creates_nan_columns():
    df = pd.DataFrame({"timestamp": [1.0, 2.0], "close": [1.0, 2.0]})
    agg = KlineDepthAggregator()
    out = agg.attach_depth_columns(df, [])
    for c in KlineDepthAggregator.COLUMNS:
        assert c in out.columns
        assert out[c].isna().all()


# ─────────────────────────── 因子自动切换钩子 ───────────────────────────

def test_proxy_signed_volume_switches_to_real_taker():
    """K 线含 taker_buy_volume 列时，CVD/OFI 因子自动走真实数据。"""
    df = pd.DataFrame({
        "high": [102.0, 103.0], "low": [98.0, 99.0],
        "close": [100.0, 101.0], "volume": [10.0, 10.0],
        "taker_buy_volume": [7.0, 6.0],
    })
    signed = _proxy_signed_volume(df)
    # 真实路径：buy − (volume − buy) = 7−3=4, 6−4=2
    assert list(signed) == pytest.approx([4.0, 2.0])


def test_l2_depth_imbalance_factor_degrade_and_value():
    """L2 深度失衡因子：列缺失降级 0；列就绪输出滚动均值。"""
    f = L2DepthImbalanceFactor()
    f.params = {"window": 3}
    # 缺失 → 全 0
    df = pd.DataFrame({"close": [1.0, 2.0, 3.0]})
    out = f.calculate(df)
    assert (out == 0.0).all()
    # 就绪 → 滚动均值
    df2 = pd.DataFrame({"close": [1.0] * 3, "depth_imbalance": [0.5, -0.5, 1.0]})
    out2 = f.calculate(df2)
    assert out2.iloc[2] == pytest.approx((0.5 - 0.5 + 1.0) / 3)


def test_l2_factor_registered():
    """l2_depth_imbalance 因子已在注册表（元数据完整）。"""
    assert L2DepthImbalanceFactor().get_metadata().factor_id == "l2_depth_imbalance"
    md = L2DepthImbalanceFactor().get_metadata()
    assert md.category == "derivatives" and md.subcategory == "orderflow"
