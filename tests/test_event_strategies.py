# -*- coding: utf-8 -*-
"""p2-event-strategies：E5-2 / E5-3 / E5-5 影子策略的纯逻辑单测（不连库）。

覆盖：
  基础工具      base_symbol 归一、signal_id 确定性（幂等的前提）、_percentile 线性插值
  E5-2          滚动 z 因果性（只用事件前的历史）、拥挤反转方向、OI 门、历史不足不产信号
  E5-3          桶不足 → insufficient_history 不降级、方向判定、单边率与冷却
  E5-5          强度/方向双门、负面开避险窗、同币冷却、避险窗口只读不生效
  晋升门        N < 30 或净期望下界 ≤ 成本 → promotion_ready=False；live() 必须抛错
  新闻数据修复  RFC-2822 时间解析回归、启发式 strength 分级、桥接阈值与 1–5 量纲对齐
"""
from __future__ import annotations

from typing import Any, Dict, List

import pytest

from backend.services.strategies.event import base as ev_base
from backend.services.strategies.event.base import (
    COST_BP,
    PROMOTION_MIN_N,
    EventShadowStrategy,
    EventSignal,
    base_symbol,
)
from backend.services.strategies.event import e5_2_funding_shock as e52
from backend.services.strategies.event import e5_3_liquidation_cascade as e53
from backend.services.strategies.event import e5_5_news_hedge as e55

H = 3600 * 1000


# ─────────────────────────── 基础工具 ───────────────────────────
@pytest.mark.parametrize("raw,want", [
    ("BTCUSDT", "BTC"), ("ETH/USDT:USDT", "ETH"), ("SOL-PERP", "SOL"),
    ("XRPUSDC", "XRP"), ("btcusdt", "BTC"), ("DOGE", "DOGE"), ("", ""),
])
def test_base_symbol_normalization(raw, want):
    assert base_symbol(raw) == want


def test_signal_id_is_deterministic_and_distinct():
    """幂等入账的基础：同一 (策略, 币, 时刻) 必须得到同一个 id。"""
    a = EventSignal(ts_ms=1_700_000_000_000, symbol="BTC", direction=1, horizon_h=4)
    b = EventSignal(ts_ms=1_700_000_000_000, symbol="BTC", direction=-1, horizon_h=9)
    assert a.signal_id("s1") == b.signal_id("s1")      # 方向/时长不参与 id
    assert a.signal_id("s1") != a.signal_id("s2")      # 策略不同则不同
    c = EventSignal(ts_ms=1_700_000_003_600, symbol="BTC", direction=1, horizon_h=4)
    assert a.signal_id("s1") != c.signal_id("s1")


def test_percentile_linear_interpolation():
    xs = [float(i) for i in range(101)]                # 0..100
    assert e53._percentile(xs, 0.99) == pytest.approx(99.0)
    assert e53._percentile(xs, 0.5) == pytest.approx(50.0)
    assert e53._percentile([], 0.99) == 0.0
    assert e53._percentile([7.0], 0.99) == 7.0


# ─────────────────────────── E5-2 资金费突变 ───────────────────────────
def _funding_series(n: int, *, shock_at: int, shock: float, base: float = 0.0001) -> List[Dict[str, Any]]:
    """构造 8h 边界上的费率序列，在 shock_at 处注入一次跳变。"""
    out = []
    for i in range(n):
        rate = base + (0.000001 * (i % 3))            # 极小噪声，保证 sd > 0
        if i == shock_at:
            rate = base + shock
        out.append({"ts_ms": i * 8 * H, "rate8h": rate})
    return out


def _run_e52(monkeypatch, series, *, require_oi=False, oi=None, z=3.0, min_hist=20):
    monkeypatch.setattr(e52, "load_funding_series", lambda *a, **k: {"BTC": series})
    monkeypatch.setattr(e52, "load_oi_series", lambda *a, **k: (oi or {}))
    s = e52.FundingShockStrategy(require_oi=require_oi)
    s.z_threshold = z
    s.min_history = min_hist
    s.roll_window = min_hist
    s.min_abs_rate = 0.0
    notes: List[str] = []
    sigs = s.detect(since_ms=0, until_ms=10**15, limit=100, notes=notes)
    return sigs, notes


def test_e52_funding_spike_triggers_short(monkeypatch):
    """费率暴涨 = 多头拥挤付费 → 拥挤反转做空。"""
    series = _funding_series(40, shock_at=30, shock=+0.01)
    sigs, _ = _run_e52(monkeypatch, series)
    hits = [s for s in sigs if s.ts_ms == 30 * 8 * H]
    assert len(hits) == 1
    assert hits[0].direction == -1
    assert hits[0].payload["z"] > 3
    assert hits[0].horizon_h == 4.0


def test_e52_funding_crash_triggers_long(monkeypatch):
    series = _funding_series(40, shock_at=30, shock=-0.01)
    sigs, _ = _run_e52(monkeypatch, series)
    hits = [s for s in sigs if s.ts_ms == 30 * 8 * H]
    assert len(hits) == 1 and hits[0].direction == 1


def test_e52_requires_minimum_history(monkeypatch):
    """历史不足滚动窗时不产信号——宁可不出，也不用短窗把 sd 压小凑 z。"""
    series = _funding_series(10, shock_at=8, shock=+0.01)
    sigs, notes = _run_e52(monkeypatch, series, min_hist=20)
    assert sigs == []
    assert any("历史不足" in n for n in notes)


def test_e52_rolling_window_is_causal(monkeypatch):
    """事件之后的剧烈波动不得影响该事件的 z（用未来数据算基线就是前视）。"""
    early = _funding_series(40, shock_at=30, shock=+0.01)
    later = list(early) + [{"ts_ms": i * 8 * H, "rate8h": 0.05 * (i % 2)} for i in range(40, 60)]
    z_early = [s for s in _run_e52(monkeypatch, early)[0] if s.ts_ms == 30 * 8 * H][0].payload["z"]
    z_later = [s for s in _run_e52(monkeypatch, later)[0] if s.ts_ms == 30 * 8 * H][0].payload["z"]
    assert z_early == pytest.approx(z_later)


def test_e52_oi_gate_drops_events_without_oi(monkeypatch):
    """require_oi=true 且拿不到 OI 时必须丢弃，不能"无 OI 也算"。"""
    series = _funding_series(40, shock_at=30, shock=+0.01)
    sigs, notes = _run_e52(monkeypatch, series, require_oi=True, oi={"ETH": [{"ts_ms": 0, "oi_usd": 1.0}]})
    assert sigs == []
    assert any("OI" in n for n in notes)


def test_e52_oi_gate_passes_when_oi_rises(monkeypatch):
    series = _funding_series(40, shock_at=30, shock=+0.01)
    t = 30 * 8 * H
    oi = {"BTC": [{"ts_ms": t - 9 * H, "oi_usd": 1000.0}, {"ts_ms": t, "oi_usd": 1200.0}]}
    sigs, _ = _run_e52(monkeypatch, series, require_oi=True, oi=oi)
    hits = [s for s in sigs if s.ts_ms == t]
    assert len(hits) == 1 and hits[0].payload["oi_change_pct"] == pytest.approx(20.0)


def test_e52_oi_change_pct_needs_both_ends():
    assert e52._oi_change_pct([], 1000, 8 * H) is None
    only_recent = [{"ts_ms": 1000, "oi_usd": 5.0}]
    assert e52._oi_change_pct(only_recent, 1000, 8 * H) is None      # 没有窗口前的点


# ─────────────────────────── E5-3 清算级联 ───────────────────────────
def _liq_book(n_quiet: int, *, spike_long: float = 0.0, spike_short: float = 0.0,
              quiet: float = 10_000.0) -> Dict[int, tuple]:
    book = {i * H: (quiet, quiet) for i in range(n_quiet)}
    if spike_long or spike_short:
        book[n_quiet * H] = (spike_long, spike_short)
    return book


def _run_e53(monkeypatch, book, *, min_hist=20, side_ratio=0.7, min_notional=0.0):
    monkeypatch.setattr(e53, "ticks_coverage_ms", lambda: (0, 0))
    monkeypatch.setattr(e53, "load_buckets_from_events", lambda *a, **k: {"BTC": book})
    s = e53.LiquidationCascadeStrategy()
    s.min_history_buckets = min_hist
    s.side_ratio = side_ratio
    s.min_notional = min_notional
    s.cooldown_buckets = 0
    notes: List[str] = []
    return s.detect(since_ms=0, until_ms=10**15, limit=100, notes=notes), notes


def test_e53_long_liquidation_triggers_long(monkeypatch):
    """多头被强平（long_usd 占优）→ 超跌 → 反向做多。"""
    sigs, _ = _run_e53(monkeypatch, _liq_book(30, spike_long=5_000_000.0, spike_short=1000.0))
    assert len(sigs) == 1
    assert sigs[0].direction == 1
    assert sigs[0].payload["side_ratio"] > 0.9


def test_e53_short_liquidation_triggers_short(monkeypatch):
    sigs, _ = _run_e53(monkeypatch, _liq_book(30, spike_long=1000.0, spike_short=5_000_000.0))
    assert len(sigs) == 1 and sigs[0].direction == -1


def test_e53_two_sided_cascade_is_ignored(monkeypatch):
    """双边对砍不是单边级联，没有明确的均值回归方向。"""
    sigs, _ = _run_e53(monkeypatch, _liq_book(30, spike_long=2_500_000.0, spike_short=2_500_000.0))
    assert sigs == []


def test_e53_insufficient_history_yields_nothing(monkeypatch):
    """桶数不足时返回空并说明原因，绝不降低分位阈值硬凑。"""
    sigs, notes = _run_e53(monkeypatch, _liq_book(5, spike_long=9_000_000.0), min_hist=336)
    assert sigs == []
    assert any("insufficient_history" in n for n in notes)


def test_e53_signal_timestamp_is_bucket_close(monkeypatch):
    """信号时刻必须是桶收盘，用桶内任意时刻都等于偷看桶内未来成交。"""
    sigs, _ = _run_e53(monkeypatch, _liq_book(30, spike_long=5_000_000.0, spike_short=1000.0))
    assert sigs[0].ts_ms == 30 * H + H


def test_e53_falls_back_to_hourly_when_ticks_are_shallow(monkeypatch):
    """ticks 只有几小时 → 回退小时桶，并在 notes 里写清楚换了源。"""
    monkeypatch.setattr(e53, "ticks_coverage_ms", lambda: (0, 5 * H))
    s = e53.LiquidationCascadeStrategy()
    notes: List[str] = []
    src, bucket = s.choose_source(0, 10 * H, notes)
    assert src == "events" and bucket == H
    assert any("回退" in n for n in notes)


def test_e53_uses_ticks_when_history_is_deep(monkeypatch):
    monkeypatch.setattr(e53, "ticks_coverage_ms", lambda: (0, 400 * 24 * H))
    s = e53.LiquidationCascadeStrategy()
    src, bucket = s.choose_source(0, 10 * H, [])
    assert src == "ticks" and bucket == s.bucket_min * 60 * 1000


# ─────────────────────────── E5-5 新闻避险 ───────────────────────────
def _news(nid: int, ts_ms: int, *, direction: float, strength: int, symbols=None) -> Dict[str, Any]:
    import datetime as dt

    return {
        "id": nid, "source": "test", "title": f"news {nid}", "url": None,
        "published_at": dt.datetime.fromtimestamp(ts_ms / 1000),
        "created_at": None, "impact_direction": direction, "impact_strength": strength,
        "impact_duration": "short", "affected_symbols": symbols or ["BTC"],
        "event_category": "general", "confidence": 0.3, "ai_summary": "[kw] x",
    }


def _run_e55(monkeypatch, rows, **over):
    monkeypatch.setattr(e55, "load_news", lambda *a, **k: rows)
    monkeypatch.setattr(e55, "annotation_quality", lambda *a, **k: {"keyword_share": 1.0})
    s = e55.NewsHedgeStrategy()
    for k, v in over.items():
        setattr(s, k, v)
    notes: List[str] = []
    return s, s.detect(since_ms=0, until_ms=10**15, limit=100, notes=notes), notes


def test_e55_negative_news_opens_hedge_window(monkeypatch):
    s, sigs, _ = _run_e55(monkeypatch, [_news(1, 1_700_000_000_000, direction=-0.6, strength=5)])
    assert len(sigs) == 1
    assert sigs[0].direction == -1
    assert sigs[0].horizon_h == s.hedge_hours
    assert sigs[0].payload["hedge_window_h"] == s.hedge_hours
    assert sigs[0].payload["leverage_mult"] == s.hedge_leverage_mult


def test_e55_positive_news_does_not_open_hedge(monkeypatch):
    _, sigs, _ = _run_e55(monkeypatch, [_news(1, 1_700_000_000_000, direction=0.6, strength=5)])
    assert sigs[0].direction == 1
    assert sigs[0].payload["hedge_window_h"] == 0.0
    assert sigs[0].payload["leverage_mult"] == 1.0


def test_e55_weak_direction_is_filtered(monkeypatch):
    """强度够但方向不明确 → 不产信号（词表打出的 ±0.3 属于噪声区）。"""
    _, sigs, notes = _run_e55(monkeypatch, [_news(1, 1_700_000_000_000, direction=-0.3, strength=5)])
    assert sigs == []
    assert any("direction" in n for n in notes)


def test_e55_cooldown_dedupes_same_story(monkeypatch):
    """同一事件被多家媒体转载 → 冷却窗内只留一条。"""
    t = 1_700_000_000_000
    rows = [_news(i, t + i * 60_000, direction=-0.6, strength=5) for i in range(5)]
    _, sigs, _ = _run_e55(monkeypatch, rows, cooldown_min=60)
    assert len(sigs) == 1


def test_e55_cooldown_is_per_symbol(monkeypatch):
    t = 1_700_000_000_000
    rows = [_news(1, t, direction=-0.6, strength=5, symbols=["BTC", "ETH"])]
    _, sigs, _ = _run_e55(monkeypatch, rows, cooldown_min=60)
    assert {s.symbol for s in sigs} == {"BTC", "ETH"}


def test_e55_annotation_source_is_recorded(monkeypatch):
    """标注来源必须落进 payload：关键词标注和 LLM 标注的可信度完全不同。"""
    _, sigs, _ = _run_e55(monkeypatch, [_news(1, 1_700_000_000_000, direction=-0.6, strength=5)])
    assert sigs[0].payload["annotation"] == "keyword"


def test_e55_symbols_fallback_to_btc(monkeypatch):
    row = _news(1, 1_700_000_000_000, direction=-0.6, strength=5, symbols=[])
    _, sigs, _ = _run_e55(monkeypatch, [row])
    assert sigs[0].symbol == "BTC"


# ─────────────────────────── 晋升门与三模式 ───────────────────────────
class _Stub(EventShadowStrategy):
    strategy_id = "e5_stub"
    universe_filter = False

    def __init__(self, rows):
        self._rows = rows

    def detect(self, **kw):
        return []


def _scored(n: int, excess_bp: float, hit: int = 1) -> List[Dict[str, Any]]:
    return [{"status": "scored", "excess_bp": excess_bp, "hit": hit, "brier": 0.2} for _ in range(n)]


def test_promotion_gate_blocks_small_sample(monkeypatch):
    """样本不足 30 时，哪怕超额很漂亮也不许晋升。"""
    from backend.services.analysis import ledgers

    monkeypatch.setattr(ledgers, "list_signals", lambda **k: _scored(5, 500.0))
    k = _Stub([]).kpi()
    assert k["n_scored"] == 5
    assert k["promotion_ready"] is False
    assert f"N=5/{PROMOTION_MIN_N}" in k["promotion_reason"]


def test_promotion_gate_blocks_when_ci_crosses_cost(monkeypatch):
    """超额均值为正但 95% 下界压不过 14bp 成本 → 仍不许晋升。"""
    from backend.services.analysis import ledgers

    rows = _scored(20, 20.0) + _scored(20, -10.0, hit=0)
    monkeypatch.setattr(ledgers, "list_signals", lambda **k: rows)
    k = _Stub([]).kpi()
    assert k["n_scored"] == 40
    assert k["excess_ci_bp"][0] <= COST_BP
    assert k["promotion_ready"] is False


def test_promotion_gate_opens_on_strong_consistent_edge(monkeypatch):
    from backend.services.analysis import ledgers

    monkeypatch.setattr(ledgers, "list_signals", lambda **k: _scored(50, 60.0))
    k = _Stub([]).kpi()
    assert k["promotion_ready"] is True
    assert k["net_lower_bp"] > 0


def test_live_is_blocked_before_promotion(monkeypatch):
    """未过影子门时 live() 必须抛错——这是"没有下单路径"的硬保证。"""
    from backend.services.analysis import ledgers

    monkeypatch.setattr(ledgers, "list_signals", lambda **k: _scored(3, 10.0))
    with pytest.raises(NotImplementedError) as exc:
        _Stub([]).live()
    assert "影子" in str(exc.value)


def test_all_three_strategies_reject_live():
    from backend.services.strategies.event import get_strategy, registered_strategies

    assert set(registered_strategies()) == {
        "e5_2_funding_shock", "e5_3_liq_cascade", "e5_5_news_hedge"}
    for sid in registered_strategies():
        assert not hasattr(get_strategy(sid), "_live_impl")


def test_detect_scorable_drops_untradable_symbols(monkeypatch):
    """不在可评分币池的标的必须在入账前剔除，否则 score_due 只会攒一堆 void。"""
    class _S(EventShadowStrategy):
        strategy_id = "e5_stub2"

        def detect(self, **kw):
            return [EventSignal(ts_ms=1, symbol="BTC", direction=1, horizon_h=1),
                    EventSignal(ts_ms=2, symbol="NOCOIN", direction=1, horizon_h=1)]

    monkeypatch.setattr(ev_base, "tradable_universe", lambda **k: {"BTC", "ETH"})
    notes: List[str] = []
    out = _S().detect_scorable(since_ms=0, until_ms=10, notes=notes)
    assert [s.symbol for s in out] == ["BTC"]
    assert any("可评分币池" in n for n in notes)


# ─────────────────────────── 新闻数据修复回归 ───────────────────────────
def test_rfc2822_published_at_is_parsed():
    """RSS 给的是 RFC-2822；旧实现只试 fromisoformat → published_at 全 NULL。"""
    from backend.services.news_intelligence_service import parse_pub_time

    dt = parse_pub_time("Fri, 14 Aug 2026 21:19:39 +0000")
    assert dt is not None and dt.year == 2026 and dt.month == 8 and dt.day == 14
    assert parse_pub_time("2026-08-14T21:19:39Z") is not None
    assert parse_pub_time("") is None
    assert parse_pub_time(None) is None
    assert parse_pub_time("not a date") is None


def test_heuristic_strength_is_graded_not_constant():
    """旧实现从不给 strength 赋值 → 5040 行全是 1，这一维毫无区分度。"""
    from backend.services.news_intelligence_service import news_intelligence as ni

    plain = ni._heuristic_analyze({"title": "Company announces quarterly earnings call"})
    assert plain.strength == 1

    heavy = ni._heuristic_analyze({"title": "Major exchange hacked, exploit drains funds, trading halt"})
    assert heavy.strength >= 4
    assert heavy.direction < 0
    assert heavy.summary.startswith("[kw]")            # 来源可辨识


def test_heuristic_extracts_symbols():
    from backend.services.news_intelligence_service import news_intelligence as ni

    got = ni._heuristic_analyze({"title": "Ethereum upgrade boosts Solana rivalry"})
    assert "ETH" in got.symbols and "SOL" in got.symbols


def test_news_bridge_threshold_matches_1_to_5_scale():
    """桥接阈值曾是 7，而强度量纲是 1–5 → news.high_impact 恒为 0 条。"""
    from backend.services.events.bridge import news_to_events

    row = {"id": 1, "title": "t", "impact_strength": 4, "impact_direction": -0.6,
           "affected_symbols": ["BTC"], "created_at": None, "published_at": None}
    assert len(news_to_events(row)) == 1               # 默认阈值下 4 分能过
    row_weak = dict(row, impact_strength=2)
    assert news_to_events(row_weak) == []
