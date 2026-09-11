# -*- coding: utf-8 -*-
"""e5_2 / e5_3 停摆修复回归（2026-09-04）。"""
from __future__ import annotations

from backend.services.strategies.event.e5_2_funding_shock import FundingShockStrategy
from backend.services.strategies.event.e5_3_liquidation_cascade import LiquidationCascadeStrategy


def test_e5_3_小时源历史门槛按墙钟缩放():
    st = LiquidationCascadeStrategy()
    st.bucket_min = 30
    st.min_history_buckets = 336
    assert st.history_buckets_needed(30 * 60 * 1000) == 336
    assert st.history_buckets_needed(60 * 60 * 1000) == 168, "小时源应为 7 天 = 168 桶，不是 14 天"


def test_e5_3_稀疏序列按时间跨度而非桶个数判定(monkeypatch):
    """DOGE 填充率 ~82% 时，纯 len(buckets) 会误杀真实级联。"""
    st = LiquidationCascadeStrategy()
    st.min_history_buckets = 336
    st.pct = 0.99
    st.side_ratio = 0.70
    st.min_notional = 1_000_000.0
    st.cooldown_buckets = 4

    # 构造：7 天跨度但只有 280 个非空小时桶（稀疏），末尾一笔巨额清算
    hour = 3600 * 1000
    until = 1_000_000_000_000
    since = until - 24 * hour
    book = {}
    # 从 since-8d 起，每隔 ~1.2h 一桶（稀疏）
    t0 = since - 8 * 24 * hour
    t = t0
    while t <= until - hour:
        book[t] = (100_000.0, 50_000.0)  # 常态小额
        t += int(1.2 * hour)
    # 触发桶：远超历史
    trigger = until - hour
    book[trigger] = (5_000_000.0, 200_000.0)

    monkeypatch.setattr(
        "backend.services.strategies.event.e5_3_liquidation_cascade.ticks_coverage_ms",
        lambda: (0, 0),
    )
    monkeypatch.setattr(
        "backend.services.strategies.event.e5_3_liquidation_cascade.load_buckets_from_events",
        lambda *a, **k: {"DOGE": book},
    )
    notes = []
    sigs = st.detect(since_ms=since, until_ms=until, notes=notes)
    assert len(sigs) >= 1, f"稀疏但跨度够应检出，notes={notes}"
    assert sigs[0].symbol == "DOGE"
    assert sigs[0].direction == 1  # 多头被强平 → 做多


def test_e5_2_无OI时降置信入账而非丢弃():
    st = FundingShockStrategy(require_oi=True)
    st.z_threshold = 2.0
    st.min_history = 5
    st.roll_window = 10
    st.min_abs_rate = 0.0
    st.oi_min_pct = 3.0

    # 历史费率有小波动（否则 pstdev≈0 会被跳过），末尾大跳变拉高 z
    base_ts = 1_700_000_000_000
    eight = 8 * 3600 * 1000
    seq = []
    for i in range(20):
        seq.append({"ts_ms": base_ts + i * eight, "rate8h": 0.0001 + (0.00002 if i % 2 else -0.00002)})
    seq.append({"ts_ms": base_ts + 20 * eight, "rate8h": 0.0030})  # 大跳变

    import backend.services.strategies.event.e5_2_funding_shock as m

    notes = []
    orig_f, orig_o = m.load_funding_series, m.load_oi_series
    m.load_funding_series = lambda *a, **k: {"AAA": seq}
    m.load_oi_series = lambda *a, **k: {}
    try:
        sigs = st.detect(since_ms=base_ts + 19 * eight, until_ms=base_ts + 21 * eight, notes=notes)
    finally:
        m.load_funding_series, m.load_oi_series = orig_f, orig_o

    assert len(sigs) == 1, f"无 OI 应降置信入账，notes={notes}"
    assert sigs[0].payload.get("oi_confirmed") is False
    assert sigs[0].confidence <= 0.55
    assert any("无 OI" in n for n in notes)


def test_e5_2_有OI但未达阈值仍丢弃():
    st = FundingShockStrategy(require_oi=True)
    st.z_threshold = 2.0
    st.min_history = 5
    st.roll_window = 10
    st.min_abs_rate = 0.0
    st.oi_min_pct = 3.0
    st.oi_window_h = 8.0

    base_ts = 1_700_000_000_000
    eight = 8 * 3600 * 1000
    seq = []
    for i in range(20):
        seq.append({"ts_ms": base_ts + i * eight, "rate8h": 0.0001 + (0.00002 if i % 2 else -0.00002)})
    hit_ts = base_ts + 20 * eight
    seq.append({"ts_ms": hit_ts, "rate8h": 0.0030})

    import backend.services.strategies.event.e5_2_funding_shock as m

    orig_f, orig_o = m.load_funding_series, m.load_oi_series
    m.load_funding_series = lambda *a, **k: {"BBB": seq}
    m.load_oi_series = lambda *a, **k: {
        "BBB": [
            {"ts_ms": hit_ts - eight, "oi_usd": 100.0},
            {"ts_ms": hit_ts, "oi_usd": 101.0},  # +1% < 3%
        ]
    }
    notes = []
    try:
        sigs = st.detect(since_ms=base_ts + 19 * eight, until_ms=hit_ts + 1, notes=notes)
    finally:
        m.load_funding_series, m.load_oi_series = orig_f, orig_o
    assert sigs == [], f"有 OI 但未确认应丢弃，got={sigs} notes={notes}"
    assert any("OI 增幅" in n for n in notes)
