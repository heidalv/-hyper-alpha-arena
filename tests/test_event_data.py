# -*- coding: utf-8 -*-
"""v3 方向 4（p0-event-data）：事件数据层纯逻辑单测（不连库、不出网）。

覆盖：公告分类/币种提取/三源解析、forceOrder 解析与级联检测、持仓结构合并与 OI 突变、
结算费率解析与极端费率分类、news/whale/macro 桥接映射、RiskEngine 事件避险窗口规则、
MarketEvent 归一化、采集宿主选择。
"""
from __future__ import annotations

import time

import pytest


# ───────────────────────────── market_events_store ─────────────────────────────
def test_market_event_finalize_clamps_and_hashes():
    from backend.services.events.market_events_store import MarketEvent, make_dedupe_hash

    e = MarketEvent(event_type="x.y", ts_ms=1000, source="s", symbol="btcusdt", severity=9, direction=3.0).finalize()
    assert e.severity == 5 and e.direction == 1.0 and e.symbol == "BTCUSDT"
    assert e.dedupe_hash == make_dedupe_hash("s", "x.y", "BTCUSDT", 1000)
    e2 = MarketEvent(event_type="x.y", ts_ms=1000, source="s", symbol=None, severity=0, direction=-7).finalize()
    assert e2.severity == 1 and e2.direction == -1.0 and e2.symbol is None
    assert make_dedupe_hash("a", 1, None) == make_dedupe_hash("a", "1", "")


# ───────────────────────────── exchange_announcements ─────────────────────────────
def test_classify_title_rules():
    from backend.services.events.exchange_announcements import classify_title

    assert classify_title("Binance Will Delist ALPACA, PDA, VIB, WING on 2026-09-10")[0] == "delisting"
    assert classify_title("Notice of Removal of Spot Trading Pairs - 2026-09-01")[0] == "delisting"
    assert classify_title("Binance Will Add Monitoring Tag to XYZ (XYZ)")[0] == "monitoring_tag"
    assert classify_title("Binance Futures Will Launch USDⓈ-Margined FOO USDT Perpetual Contract")[0] == "futures_listing"
    assert classify_title("OKX to list perpetual futures for PYPL, DDOG and IONQ equities")[0] == "futures_listing"
    assert classify_title("Binance Will List Foo (FOO) with Seed Tag Applied")[0] == "monitoring_tag"
    assert classify_title("Binance Will List Foo (FOO)")[0] == "listing"
    assert classify_title("Introducing Bar (BAR) on Binance HODLer Airdrops")[0] == "airdrop"
    assert classify_title("Introducing Baz (BAZ) on Binance Launchpool")[0] == "launchpool"
    assert classify_title("Binance Will Support the Ethereum Network Upgrade")[0] == "maintenance"
    # 抵押/借贷资产调整不是交易下架：单列 collateral（严重度 2），不触发 72h 禁开
    assert classify_title("Discontinuation of ICX as Collateral and Lending Asset") == ("collateral", 2, -0.3)
    assert classify_title("Binance Will Add 4 bStocks Tokenized Securities as Collateral Asset - 2026-09-02") == ("collateral", 2, 0.2)
    # 无关键词：按目录提示兜底
    assert classify_title("Something unusual", "delisting")[0] == "delisting"
    assert classify_title("Something unusual", "listing")[0] == "listing"
    assert classify_title("Something unusual")[0] == "other"
    t, sev, d = classify_title("Binance Will Delist FOO")
    assert sev == 4 and d == -1.0


def test_extract_symbols():
    from backend.services.events.exchange_announcements import extract_symbols

    assert extract_symbols("Binance Will List Foo (FOO) and Bar (BAR)") == ["FOO", "BAR"]
    assert extract_symbols("Binance Futures Will Launch USDⓈ-Margined 1000PEPEUSDT Perpetual Contract") == ["1000PEPE"]
    assert extract_symbols("OKX to list perpetual futures for PYPL, DDOG and IONQ equities") == ["PYPL", "DDOG", "IONQ"]
    # 停用词不会被当成币
    assert "USDT" not in extract_symbols("Binance Will Add USDT Trading Pairs for FOO (FOO)")
    assert extract_symbols("") == []


def test_parse_three_sources():
    from backend.services.events.exchange_announcements import parse_binance, parse_okx, parse_bybit, to_market_events

    b = parse_binance({"data": {"catalogs": [{"catalogId": 161, "articles": [
        {"id": 1, "code": "abc", "title": "Binance Will Delist FOO (FOO)", "releaseDate": 1788000000000},
        {"id": 2, "code": "", "title": "", "releaseDate": 1788000000000},
    ]}]}}, 161)
    assert len(b) == 1 and b[0].exchange == "binance" and b[0].ann_type == "delisting" and b[0].symbols == ["FOO"]
    assert b[0].url.endswith("/abc") and b[0].dedupe_hash
    o = parse_okx({"data": [{"details": [
        {"annType": "announcements-new-listings", "title": "OKX to list BAR (BAR) for spot trading",
         "url": "https://okx.com/x", "pTime": "1788000000000", "businessPTime": "1788003600000"}]}]}, "announcements-new-listings")
    assert len(o) == 1 and o[0].ann_type == "listing" and o[0].effective_at_ms == 1788003600000
    y = parse_bybit({"result": {"list": [
        {"title": "New Listing: BAZ/USDT Perpetual", "url": "u", "publishTime": 1788000000000,
         "tags": ["Derivatives"], "type": {"key": "new_crypto"}}]}}, "new_crypto")
    assert len(y) == 1 and y[0].ann_type == "futures_listing" and "BAZ" in y[0].symbols
    evs = to_market_events(b + o + y)
    assert {e.event_type for e in evs} == {"announcement.delisting", "announcement.listing", "announcement.futures_listing"}
    assert len({e.dedupe_hash for e in evs}) == len(evs)
    # 同一公告重复解析 → 同一 dedupe_hash（幂等）
    assert parse_binance({"data": {"catalogs": [{"catalogId": 161, "articles": [
        {"id": 1, "code": "abc", "title": "Binance Will Delist FOO (FOO)", "releaseDate": 1788000000000}]}]}}, 161)[0].dedupe_hash == b[0].dedupe_hash


# ───────────────────────────── liquidation_stream ─────────────────────────────
_FO = {"e": "forceOrder", "E": 1568014460893,
       "o": {"s": "BTCUSDT", "S": "SELL", "o": "LIMIT", "f": "IOC", "q": "0.014", "p": "9910", "ap": "9910",
             "X": "FILLED", "l": "0.014", "z": "0.014", "T": 1568014460893}}


def test_parse_force_order():
    import json
    from backend.services.events.liquidation_stream import parse_force_order, base_of

    t = parse_force_order(json.dumps(_FO))
    assert t is not None and t.pair == "BTCUSDT" and t.symbol == "BTC" and t.side == "SELL"
    assert t.long_liquidated is True and t.notional_usd == pytest.approx(138.74) and t.ts_ms == 1568014460893
    assert parse_force_order({"e": "aggTrade"}) is None
    assert parse_force_order("not json") is None
    bad = {"o": dict(_FO["o"], S="HOLD")}
    assert parse_force_order(bad) is None
    assert base_of("1000PEPEUSDT") == "1000PEPE" and base_of("ETHUSDC") == "ETH" and base_of("XYZ") == "XYZ"
    # Aster 同构：exchange 标签跟随来源
    ta = parse_force_order(json.dumps(_FO), exchange="asterdex")
    assert ta is not None and ta.exchange == "asterdex"


def test_parse_okx_and_bybit_liquidations():
    from backend.services.events.liquidation_stream import (
        parse_okx_liquidations, parse_bybit_liquidations, okx_inst_to_pair, enabled_sources,
    )

    assert okx_inst_to_pair("BTC-USDT-SWAP") == "BTCUSDT" and okx_inst_to_pair("ETH-USDC-SWAP") == "ETHUSDC"
    assert okx_inst_to_pair("BTC-USD-SWAP") is None and okx_inst_to_pair("BTC-USDT") is None
    okx_msg = {"arg": {"channel": "liquidation-orders", "instType": "SWAP"},
               "data": [{"instId": "BTC-USDT-SWAP", "instType": "SWAP",
                         "details": [{"bkLoss": "0", "bkPx": "80000", "posSide": "long", "side": "sell", "sz": "5",
                                      "ts": "1788431181494"}]},
                        {"instId": "BTC-USD-SWAP", "details": [{"bkPx": "80000", "side": "buy", "sz": "1", "ts": "1"}]},
                        {"instId": "ZZZ-USDT-SWAP", "details": [{"bkPx": "1", "side": "buy", "sz": "1", "ts": "1"}]}]}
    ticks = parse_okx_liquidations(okx_msg, {"BTC-USDT-SWAP": 0.01})
    # 币本位与缺面值的 instId 被跳过；张数 × 面值 = 0.05 BTC，名义 4000
    assert len(ticks) == 1
    t = ticks[0]
    assert t.exchange == "okx" and t.pair == "BTCUSDT" and t.symbol == "BTC" and t.side == "SELL"
    assert t.long_liquidated and t.qty == pytest.approx(0.05) and t.notional_usd == pytest.approx(4000.0)
    assert parse_okx_liquidations("pong", {}) == [] and parse_okx_liquidations({"event": "subscribe"}, {}) == []

    bybit_msg = {"topic": "allLiquidation.ETHUSDT", "type": "snapshot", "ts": 1788431181494,
                 "data": [{"T": 1788431181000, "s": "ETHUSDT", "S": "Buy", "v": "2", "p": "3000"},
                          {"T": 1788431181001, "s": "ETHUSDT", "S": "Sell", "v": "1", "p": "3001"}]}
    bt = parse_bybit_liquidations(bybit_msg)
    assert len(bt) == 2 and all(x.exchange == "bybit" for x in bt)
    # Bybit S 为被清算的持仓方向：Buy=多头被清算 → 订单方向 SELL
    assert bt[0].side == "SELL" and bt[0].long_liquidated and bt[0].notional_usd == pytest.approx(6000.0)
    assert bt[1].side == "BUY" and not bt[1].long_liquidated
    assert parse_bybit_liquidations({"success": True, "op": "subscribe"}) == []

    import os
    old = os.environ.get("LIQUIDATION_STREAM_SOURCES")
    try:
        os.environ["LIQUIDATION_STREAM_SOURCES"] = "okx, bybit,unknown,okx"
        assert enabled_sources() == ["okx", "bybit"]
    finally:
        if old is None:
            os.environ.pop("LIQUIDATION_STREAM_SOURCES", None)
        else:
            os.environ["LIQUIDATION_STREAM_SOURCES"] = old


def test_collectors_status_reads_remote_file(tmp_path, monkeypatch):
    """采集在 dc 进程时，主进程通过状态文件读取；无文件/过期 → 本地状态 + 提示。"""
    from backend.services.events import collectors as col

    monkeypatch.setattr(col, "STATUS_FILE", str(tmp_path / "status.json"))
    monkeypatch.setattr(col, "_RUNNING_HOST", None)
    st = col.collectors_status()
    assert st["status_source"] == "local" and "status_note" in st
    col._write_status_file("dc")
    st2 = col.collectors_status()
    assert st2["status_source"] == "file" and st2["published_by"] == "dc" and st2["status_age_sec"] < 5
    assert "liquidation_stream" in st2 and "sources" in st2["liquidation_stream"]
    # 过期文件 → 退回本地
    import json
    data = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
    data["written_at"] -= 3600
    (tmp_path / "status.json").write_text(json.dumps(data), encoding="utf-8")
    st3 = col.collectors_status()
    assert st3["status_source"] == "local" and "过期" in st3["status_note"]
    # 本进程就是执行进程 → 永远本地
    monkeypatch.setattr(col, "_RUNNING_HOST", "dc")
    assert col.collectors_status()["status_source"] == "local"


def test_orchestrator_market_event_override_selection_and_enforcement():
    """事件总线 → 编排器：下架优先于级联；级联只在 2h 内；_coordinate 后硬约束落到方向/仓位。"""
    from backend.services.multi_timeframe_orchestrator import (
        MultiTimeframeOrchestrator, OrchestratorDecision, EVENT_OVERRIDE_RULES,
    )

    now = 1_800_000_000_000
    h = 3_600_000
    sel = MultiTimeframeOrchestrator.select_market_event_override
    # 严重度不够 → 无覆盖
    assert sel("SOL", [{"event_type": "announcement.delisting", "severity": 2, "ts_ms": now - h}], now_ms=now) is None
    # 过期级联（3h 前）→ 无覆盖；新级联 → event_liq_cascade
    assert sel("SOL", [{"event_type": "liquidation.cascade", "severity": 3, "ts_ms": now - 3 * h, "direction": -1}], now_ms=now) is None
    key, rule, note = sel("SOL", [{"event_type": "liquidation.cascade", "severity": 3, "ts_ms": now - h, "direction": -1}], now_ms=now)
    assert key == "event_liq_cascade" and rule == EVENT_OVERRIDE_RULES["event_liq_cascade"] and "多头清算级联" in note and "不追空" in note
    # 下架 + 级联同时存在 → 下架优先
    key, _, note = sel("SOL", [
        {"event_type": "liquidation.cascade", "severity": 4, "ts_ms": now - h, "direction": 1},
        {"event_type": "announcement.monitoring_tag", "severity": 3, "ts_ms": now - 5 * h, "title": "Binance adds Monitoring Tag to SOL"},
    ], now_ms=now)
    assert key == "event_delisting" and "监控标签" in note
    # 全市场级联（4h 内，S≥4）
    key, _, note = sel("BTC", [], [{"event_type": "liquidation.market_cascade", "severity": 4, "ts_ms": now - 2 * h, "direction": -1}], now_ms=now)
    assert key == "event_liq_cascade" and note.startswith("全市场多头")
    assert sel("BTC", [], [{"event_type": "liquidation.market_cascade", "severity": 3, "ts_ms": now - h}], now_ms=now) is None

    # 硬约束：模拟 _coordinate 之后
    d = OrchestratorDecision(symbol="SOL", allowed_direction="both", position_multiplier=1.0, event_bus_rule="event_delisting")
    MultiTimeframeOrchestrator.enforce_market_event_constraints(d)
    assert d.allowed_direction == "short_only" and d.position_multiplier == 0.5
    d = OrchestratorDecision(symbol="SOL", allowed_direction="long_only", position_multiplier=0.8, event_bus_rule="event_delisting")
    MultiTimeframeOrchestrator.enforce_market_event_constraints(d)
    assert d.allowed_direction == "none"
    d = OrchestratorDecision(symbol="SOL", allowed_direction="both", position_multiplier=0.9, event_bus_rule="event_liq_cascade")
    MultiTimeframeOrchestrator.enforce_market_event_constraints(d)
    assert d.allowed_direction == "both" and d.position_multiplier == 0.5
    d = OrchestratorDecision(symbol="SOL", allowed_direction="both", position_multiplier=0.9)
    MultiTimeframeOrchestrator.enforce_market_event_constraints(d)
    assert d.position_multiplier == 0.9 and d.coordination_note == ""


def test_summarize_market_events_text():
    from backend.services.unified_data_pool import UnifiedDataPool
    import time as _t

    now = int(_t.time() * 1000)
    txt = UnifiedDataPool.summarize_market_events(
        [{"event_type": "funding.extreme", "symbol": "ANKR", "severity": 2, "ts_ms": now - 600_000, "direction": 0.6, "title": "ANKR 8h funding -1.8%"}],
        [{"event_type": "liquidation.market_cascade", "symbol": None, "severity": 4, "ts_ms": now - 60_000, "direction": -1.0, "title": "全市场 5 分钟清算 $60,000,000"}],
    )
    lines = txt.splitlines()
    assert len(lines) == 2 and "S4" in lines[0] and "全市场" in lines[0] and "利空" in lines[0]
    assert "ANKR" in lines[1] and "利多" in lines[1]
    assert UnifiedDataPool.summarize_market_events([], None) == ""


def test_cascade_detector_cross_exchange_sum():
    """同一币在不同交易所的清算跨所合计触发级联；单笔大额事件按所区分。"""
    from backend.services.events.liquidation_stream import CascadeDetector, LiqTick

    det = CascadeDetector(window_sec=300, large_tick_usd=10_000.0, major_usd=5000.0, alt_usd=2000.0, market_usd=1e12)
    t0 = 1_700_000_000_000
    ev = det.add(LiqTick(pair="SOLUSDT", symbol="SOL", side="SELL", price=1, qty=1200, notional_usd=1200.0, ts_ms=t0, exchange="okx"))
    assert ev == []
    ev = det.add(LiqTick(pair="SOLUSDT", symbol="SOL", side="BUY", price=1, qty=900, notional_usd=900.0, ts_ms=t0 + 500, exchange="bybit"))
    assert [e.event_type for e in ev] == ["liquidation.cascade"]
    assert ev[0].source == "liq_ws" and ev[0].payload["total_usd"] == 2100.0 and ev[0].direction == -1.0
    big = det.add(LiqTick(pair="SOLUSDT", symbol="SOL", side="BUY", price=1, qty=20000, notional_usd=20000.0, ts_ms=t0 + 600, exchange="asterdex"))
    assert [e.event_type for e in big] == ["liquidation.large"] and big[0].source == "asterdex_ws"
    assert big[0].payload["exchange"] == "asterdex" and big[0].title.startswith("asterdex ")


def test_cascade_detector_thresholds_and_dedupe():
    from backend.services.events.liquidation_stream import CascadeDetector, LiqTick

    det = CascadeDetector(window_sec=300, large_tick_usd=1000.0, major_usd=5000.0, alt_usd=2000.0, market_usd=4000.0)
    t0 = 1_700_000_000_000

    def tick(sym, side, notional, ts):
        return LiqTick(pair=f"{sym}USDT", symbol=sym, side=side, price=1.0, qty=notional, notional_usd=notional, ts_ms=ts)

    # 单笔大额
    ev = det.add(tick("SOL", "SELL", 1500.0, t0))
    assert [e.event_type for e in ev] == ["liquidation.large"] and ev[0].direction == -0.5
    # 同币 5 分钟累计到 2000 → cascade（多头主导 → direction -1）
    ev = det.add(tick("SOL", "SELL", 600.0, t0 + 1000))
    assert "liquidation.cascade" in [e.event_type for e in ev]
    casc = next(e for e in ev if e.event_type == "liquidation.cascade")
    assert casc.direction == -1.0 and casc.payload["total_usd"] == 2100.0
    # 同一 5 分钟桶不再重复
    ev = det.add(tick("SOL", "SELL", 100.0, t0 + 2000))
    assert "liquidation.cascade" not in [e.event_type for e in ev]
    # BTC 使用更高阈值：2200 < 5000 不触发
    ev = det.add(tick("BTC", "BUY", 2200.0, t0 + 3000))
    assert "liquidation.cascade" not in [e.event_type for e in ev]
    # 全市场累计 1500+600+100+2200 = 4400 ≥ 4000 → market_cascade（一次）
    types = [e.event_type for e in ev]
    assert "liquidation.market_cascade" in types
    mkt = next(e for e in ev if e.event_type == "liquidation.market_cascade")
    assert mkt.symbol is None and mkt.payload["total_usd"] == 4400.0 and mkt.direction == -1.0  # 多 2200 = 空 2200 → 按多头主导（向下）
    ev = det.add(tick("ETH", "BUY", 10.0, t0 + 4000))
    assert "liquidation.market_cascade" not in [e.event_type for e in ev]
    # 窗口滑出后可再次触发（新桶）；此时全市场只剩 2500 < 4000 不触发 market_cascade
    ev = det.add(tick("SOL", "SELL", 2500.0, t0 + 600_000))
    assert [e.event_type for e in ev] == ["liquidation.large", "liquidation.cascade"]


# ───────────────────────────── position_structure ─────────────────────────────
def test_merge_rows_and_oi_jumps():
    from backend.services.events.position_structure import merge_rows, detect_oi_jumps

    rows = merge_rows(
        "BTC",
        oi=[{"timestamp": 1, "sumOpenInterest": "100", "sumOpenInterestValue": "1000"},
            {"timestamp": 2, "sumOpenInterest": "110", "sumOpenInterestValue": "1100"}],
        global_ls=[{"timestamp": 2, "longShortRatio": "1.2", "longAccount": "0.545"}],
        top_pos=[{"timestamp": 2, "longShortRatio": "1.9"}],
        top_acct=[{"timestamp": 1, "longShortRatio": "1.3"}],
        taker=[{"timestamp": 2, "buySellRatio": "0.98", "buyVol": "5", "sellVol": "5.1"}],
    )
    assert set(rows) == {1, 2}
    assert rows[2]["open_interest_value"] == 1100.0 and rows[2]["global_ls_ratio"] == 1.2
    assert rows[2]["top_position_ls_ratio"] == 1.9 and rows[2]["taker_sell_vol"] == 5.1
    assert rows[1]["top_account_ls_ratio"] == 1.3 and "global_ls_ratio" not in rows[1]
    # 10% 跃升 ≥ 8% → 事件；prev_value 参与首行比较
    evs = detect_oi_jumps("BTC", rows, prev_value=None, jump_pct=8.0)
    assert len(evs) == 1 and evs[0].payload["change_pct"] == pytest.approx(10.0) and evs[0].direction == 0.3
    evs = detect_oi_jumps("BTC", rows, prev_value=None, jump_pct=15.0)
    assert evs == []
    evs = detect_oi_jumps("BTC", {1: rows[1]}, prev_value=2000.0, jump_pct=8.0)
    assert len(evs) == 1 and evs[0].direction == -0.3 and evs[0].severity == 3


# ───────────────────────────── funding_universe ─────────────────────────────
def test_parse_funding_rows_and_extremes():
    from backend.services.events.funding_universe import parse_funding_rows, classify_extreme, rate_8h

    rows = parse_funding_rows([
        {"symbol": "BTCUSDT", "fundingTime": 1788364800005, "fundingRate": "0.00003819", "markPrice": "77250.98"},
        {"symbol": "BTCUSDT", "fundingTime": 1788336000000, "fundingRate": "0.0001"},
        {"symbol": "BTCUSDT", "fundingTime": 1788336000000, "fundingRate": "0.0001"},  # 重复
        {"bad": 1},
    ])
    assert [r[0] for r in rows] == [1788336000000, 1788364800005]
    assert rows[1][2] == pytest.approx(77250.98) and rows[0][2] is None
    assert parse_funding_rows({"not": "list"}) == []

    assert rate_8h("hyperliquid", 0.0001) == pytest.approx(0.0008)
    assert rate_8h("binance", 0.0001) == pytest.approx(0.0001)
    assert classify_extreme("binance", "BTC", 0.0005, 1_000_000, threshold_8h=0.001) is None
    ev = classify_extreme("binance", "FOO", 0.0015, 1_000_000, threshold_8h=0.001)
    assert ev is not None and ev.event_type == "funding.extreme" and ev.severity == 2 and ev.direction == -0.5
    ev = classify_extreme("hyperliquid", "FOO", -0.0002, 1_000_000, threshold_8h=0.001)  # 1h×8 = -0.16%
    assert ev is not None and ev.direction == 0.5 and ev.payload["rate_8h"] == pytest.approx(-0.0016)
    # 同 8h 桶 → 同 dedupe_hash
    a = classify_extreme("binance", "FOO", 0.002, 8 * 3600_000 + 10, threshold_8h=0.001)
    b = classify_extreme("binance", "FOO", 0.003, 8 * 3600_000 + 5_000_000, threshold_8h=0.001)
    assert a.dedupe_hash == b.dedupe_hash
    big = classify_extreme("binance", "FOO", 0.006, 1, threshold_8h=0.001)
    assert big.severity == 4


# ───────────────────────────── bridge ─────────────────────────────
def test_bridge_mappings():
    from backend.services.events.bridge import news_to_events, whale_to_event, macro_to_event

    # impact_strength 量纲是 **1–5**（NewsImpact.strength 定义与 LLM prompt 一致）。
    # [2026-09-03] 本用例原按 1–10 写（strength 8/9、阈值 7），与真实量纲不符：
    # 阈值 7 超出上界 → news.high_impact 自上线起恒为 0 条。默认阈值已改 4，severity 分界改 5。
    news = {"id": 7, "title": "SEC sues X", "impact_strength": 4, "impact_direction": -0.8,
            "affected_symbols": ["BTC", "ETH"], "published_at": None, "created_at": None}
    evs = news_to_events(news)
    assert len(evs) == 2 and {e.symbol for e in evs} == {"BTC", "ETH"} and evs[0].severity == 3
    assert evs[0].payload["window_hours"] == 6.0 and evs[0].direction == -0.8
    assert news_to_events(dict(news, impact_strength=3)) == []
    evs = news_to_events(dict(news, affected_symbols=None, impact_strength=5))
    assert len(evs) == 1 and evs[0].symbol is None and evs[0].severity == 4

    w = whale_to_event({"id": 3, "symbol": "BTCUSDT", "amount_usd": 60_000_000, "activity_type": "transfer",
                        "direction": "inflow_to_exchange", "signal_direction": None, "timestamp": None}, min_usd=50_000_000)
    assert w is not None and w.symbol == "BTC" and w.direction == -0.5 and w.severity == 2
    assert whale_to_event({"id": 4, "amount_usd": 1_000}, min_usd=50_000_000) is None

    now_ms = int(time.time() * 1000)
    sched = macro_to_event({"id": 1, "event": "CPI", "importance": 5, "scheduled_at": (now_ms + 3600_000) / 1000,
                            "actual": None}, now_ms=now_ms)
    assert sched is not None and sched.event_type == "macro.scheduled" and sched.severity == 3
    rel = macro_to_event({"id": 1, "event": "CPI", "importance": 5, "scheduled_at": (now_ms - 600_000) / 1000,
                          "actual": 3.1, "forecast": 3.0, "previous": 2.9, "impact_direction": -0.4}, now_ms=now_ms)
    assert rel is not None and rel.event_type == "macro.released" and rel.direction == -0.4
    assert rel.dedupe_hash != sched.dedupe_hash
    assert macro_to_event({"id": 2, "event": "x", "importance": 2, "scheduled_at": now_ms / 1000}, now_ms=now_ms) is None
    far = macro_to_event({"id": 3, "event": "x", "importance": 5, "scheduled_at": (now_ms + 72 * 3600_000) / 1000,
                          "actual": None}, now_ms=now_ms)
    assert far is None


# ───────────────────────────── risk.event_windows ─────────────────────────────
def test_event_block_reason_rules():
    from backend.services.risk.event_windows import event_block_reason

    now = 10_000_000
    fut = now + 3600_000
    delist = {"event_type": "announcement.delisting", "symbol": "FOO", "severity": 4, "window_ends_ms": fut, "ts_ms": now}
    mon = {"event_type": "announcement.monitoring_tag", "symbol": "BAR", "severity": 3, "window_ends_ms": fut, "ts_ms": now}
    casc = {"event_type": "liquidation.cascade", "symbol": "SOL", "severity": 4, "direction": -1.0, "window_ends_ms": fut, "ts_ms": now}
    mkt = {"event_type": "liquidation.market_cascade", "symbol": None, "severity": 5, "direction": -1.0, "window_ends_ms": fut, "ts_ms": now}
    news = {"event_type": "news.high_impact", "symbol": None, "severity": 4, "direction": -0.9, "window_ends_ms": fut, "ts_ms": now}
    expired = dict(delist, window_ends_ms=now - 1)

    assert event_block_reason([delist], "FOOUSDT", "buy", now_ms=now)["rule"] == "delisting"
    assert event_block_reason([delist], "FOO", "sell", now_ms=now)["rule"] == "delisting"
    assert event_block_reason([delist], "BTC", "buy", now_ms=now) is None
    assert event_block_reason([expired], "FOO", "buy", now_ms=now) is None
    assert event_block_reason([mon], "BAR", "buy", now_ms=now)["rule"] == "monitoring_tag_no_long"
    assert event_block_reason([mon], "BAR", "sell", now_ms=now) is None
    # 多头级联：禁开多，允许开空
    assert event_block_reason([casc], "SOL", "long", now_ms=now)["rule"] == "cascade_same_side"
    assert event_block_reason([casc], "SOL", "short", now_ms=now) is None
    assert event_block_reason([dict(casc, severity=3)], "SOL", "long", now_ms=now) is None
    # 全市场级联 severity 5：任何币任何方向
    assert event_block_reason([mkt], "ETH", "sell", now_ms=now)["rule"] == "market_cascade"
    assert event_block_reason([dict(mkt, severity=4)], "ETH", "sell", now_ms=now) is None
    # 负面新闻默认不拦，开关打开才拦多
    assert event_block_reason([news], "BTC", "buy", now_ms=now, news_block=False) is None
    assert event_block_reason([news], "BTC", "buy", now_ms=now, news_block=True)["rule"] == "negative_news_no_long"
    assert event_block_reason([news], "BTC", "sell", now_ms=now, news_block=True) is None


def test_event_windows_check_fail_open(monkeypatch):
    """事件层读库失败 → 不阻断（fail-open），但 status 记录 error。"""
    from backend.services.risk import event_windows as ew

    def boom(*a, **k):
        raise RuntimeError("db down")

    import backend.services.events.market_events_store as mes
    monkeypatch.setattr(mes, "active_risk_windows", boom)
    with ew._CACHE_LOCK:
        ew._CACHE["ts"] = 0.0
    assert ew.check("BTC", "buy") is None
    st = ew.status()
    assert st["active_events"] == 0 and st["error"] and "db down" in st["error"]
    monkeypatch.setenv("RISK_EVENT_WINDOWS_ENABLED", "false")
    assert ew.check("BTC", "buy") is None
    with ew._CACHE_LOCK:
        ew._CACHE["ts"] = 0.0


# ───────────────────────────── collectors host 选择 ─────────────────────────────
def test_collectors_host_mode(monkeypatch):
    from backend.services.events import collectors as c

    monkeypatch.delenv("EVENT_COLLECTORS_HOST", raising=False)
    assert c.host_mode() == "dc" and c.should_run_here("dc") and not c.should_run_here("main")
    monkeypatch.setenv("EVENT_COLLECTORS_HOST", "main")
    assert c.should_run_here("main") and not c.should_run_here("dc")
    monkeypatch.setenv("EVENT_COLLECTORS_HOST", "both")
    assert c.should_run_here("main") and c.should_run_here("dc")
    monkeypatch.setenv("EVENT_COLLECTORS_HOST", "garbage")
    assert c.host_mode() == "dc"
    names = {s["name"] for s in c.JOB_SPECS}
    assert names == {"exchange_announcements", "liquidation_rollup", "position_structure", "funding_backfill",
                     "funding_universe_scan", "market_events_bridge",
                     "smart_money_track", "smart_money_scorecard"}
