"""套利中心"继续完善"单测（2026-07-06）。

覆盖四项让 delta-neutral 刷分真正端到端可用的改动：
1. funding_rate_provider：符号归一、多场所覆盖判断、真实 DB 读取形状。
2. engine._inject_funding_matrix：嵌套直通 / 扁平回落 provider / 异常兜底。
3. engine._paper_execute：双腿计划空腿失败时回滚已成交长腿并计入回滚成本。
4. points_valuation.value_points_for_program + program_registry 估值参数：
   填齐参数才计积分价值，缺参数诚实降级为 0。
"""

import pytest


# ─────────────── 1. funding_rate_provider ───────────────

def test_provider_normalize_symbol():
    from backend.services.rebate_arb import funding_rate_provider as frp

    assert frp._normalize_symbol("BTC") == "BTC/USDT"
    assert frp._normalize_symbol("btc") == "BTC/USDT"
    assert frp._normalize_symbol("ETH-USDT") == "ETH/USDT"
    assert frp._normalize_symbol("SOL/USDT") == "SOL/USDT"
    assert frp._normalize_symbol("") == ""


def test_provider_multi_venue_coverage():
    from backend.services.rebate_arb import funding_rate_provider as frp

    single = {"hyperliquid": {"BTC/USDT": 1e-5, "ETH/USDT": 2e-5}}
    assert frp.has_multi_venue_coverage(single) is False

    multi = {
        "hyperliquid": {"BTC/USDT": 1e-5},
        "binance": {"BTC/USDT": -3e-5},
    }
    assert frp.has_multi_venue_coverage(multi) is True


def test_provider_returns_nested_shape():
    """真实 DB：返回 {exchange:{symbol:rate}} 形状（本环境至少有 hyperliquid）。"""
    from backend.services.rebate_arb import funding_rate_provider as frp

    data = frp.latest_funding_by_venue(use_cache=False)
    assert isinstance(data, dict)
    for ex, m in data.items():
        assert isinstance(ex, str)
        assert isinstance(m, dict)
        for sym, rate in m.items():
            assert "/" in sym  # 已归一
            assert isinstance(rate, float)


# ─────────────── 2. engine._inject_funding_matrix ───────────────

def test_inject_funding_matrix_passthrough_nested():
    from backend.services.rebate_arb.engine import RebateArbitrageEngine

    nested = {"hyperliquid": {"BTC/USDT": 1e-5}, "binance": {"BTC/USDT": -2e-5}}
    out = RebateArbitrageEngine._inject_funding_matrix({"foo": 1}, nested)
    assert out["funding_rates"] == nested
    assert out["foo"] == 1  # 原字段保留


def test_inject_funding_matrix_flat_falls_back_to_provider(monkeypatch):
    from backend.services.rebate_arb import funding_rate_provider as frp
    from backend.services.rebate_arb.engine import RebateArbitrageEngine

    fake = {"hyperliquid": {"BTC/USDT": 3e-5}, "binance": {"BTC/USDT": -1e-5}}
    monkeypatch.setattr(frp, "latest_funding_by_venue", lambda *a, **k: fake)

    flat = {"BTC/USDT": 1e-5}  # 扁平 → 触发 provider 回落
    out = RebateArbitrageEngine._inject_funding_matrix({}, flat)
    assert out["funding_rates"] == fake
    assert out["funding_rates_flat"] == flat


def test_inject_funding_matrix_does_not_mutate_caller():
    from backend.services.rebate_arb.engine import RebateArbitrageEngine

    original = {"a": 1}
    RebateArbitrageEngine._inject_funding_matrix(original, {"hyperliquid": {"BTC/USDT": 1e-5}})
    assert "funding_rates" not in original  # 浅拷贝，不污染调用方


# ─────────────── 3. _paper_execute 双腿回滚 ───────────────

class _FakeQuote:
    mid = 100.0

    def to_dict(self):
        return {"mid": 100.0}


def _make_fake_fill(exchange, side, size_usd, is_close):
    from backend.services.rebate_arb.rebate_paper_simulator import PaperLegFill

    return PaperLegFill(
        exchange=exchange,
        side=side,
        order_type="market",
        size_usd=size_usd,
        ref_price=100.0,
        filled_price=100.0,
        size_coins=size_usd / 100.0,
        slippage_rate=0.0005,
        slippage_cost_usd=size_usd * 0.0005,
        fee_rate=0.0004,
        fee_paid=size_usd * 0.0004,
        rebate_rate=0.0,
        rebate_received=0.0,
        is_maker=False,
        is_close=is_close,
    )


def test_paper_execute_rolls_back_long_leg_on_hedge_failure(monkeypatch):
    """长腿成交、对冲腿因无行情失败 → 回滚长腿且回滚成本计入结果。"""
    from backend.services.rebate_arb import engine as engine_mod
    from backend.services.rebate_arb import rebate_paper_simulator as sim_mod
    from backend.services.rebate_arb import rebate_paper_market as mkt_mod

    good_symbol = "BTC/USDT"
    bad_symbol = "GHOST/USDT"

    # 只有 good_symbol 能取到行情；bad_symbol 取不到 → 对冲腿失败
    def fake_resolve(symbol, exchange):
        return _FakeQuote() if symbol == good_symbol else None

    monkeypatch.setattr(mkt_mod, "resolve_paper_market", fake_resolve)

    def fake_simulate(**kwargs):
        if kwargs.get("market") is None:
            return None
        return _make_fake_fill(
            kwargs["exchange"], kwargs["side"], kwargs["size_usd"], kwargs.get("is_close", False)
        )

    monkeypatch.setattr(sim_mod, "simulate_leg_fill", fake_simulate)

    class _Pos:
        position_id = "test_pos"

    plan_side_a = {"exchange": "hyperliquid", "symbol": good_symbol, "side": "buy", "size_usd": 1000.0}
    plan_side_b = {"exchange": "binance", "symbol": bad_symbol, "side": "sell", "size_usd": 1000.0}

    eng = engine_mod.rebate_arb_engine
    result = eng._paper_execute(_Pos(), plan_side_a, plan_side_b)

    assert result["success"] is False
    assert result["rolled_back"] is True
    assert result["rollback_fill"] is not None
    # 回滚腿方向与长腿相反、且标记为平仓
    assert result["rollback_fill"]["side"] == "sell"
    assert result["rollback_fill"]["is_close"] is True
    # 成本汇总里同时含开长腿 + 回滚两笔手续费
    assert result["paper_cost_summary"]["fee_paid"] > 0


def test_paper_execute_both_legs_ok(monkeypatch):
    """两腿都能成交时正常返回、无回滚标记。"""
    from backend.services.rebate_arb import engine as engine_mod
    from backend.services.rebate_arb import rebate_paper_simulator as sim_mod
    from backend.services.rebate_arb import rebate_paper_market as mkt_mod

    monkeypatch.setattr(mkt_mod, "resolve_paper_market", lambda s, e: _FakeQuote())
    monkeypatch.setattr(
        sim_mod,
        "simulate_leg_fill",
        lambda **k: _make_fake_fill(k["exchange"], k["side"], k["size_usd"], k.get("is_close", False)),
    )

    class _Pos:
        position_id = "test_pos2"

    a = {"exchange": "hyperliquid", "symbol": "BTC/USDT", "side": "buy", "size_usd": 500.0}
    b = {"exchange": "binance", "symbol": "BTC/USDT", "side": "sell", "size_usd": 500.0}
    result = engine_mod.rebate_arb_engine._paper_execute(_Pos(), a, b)

    assert result.get("order_a") is not None
    assert result.get("order_b") is not None
    assert not result.get("rolled_back", False)


# ─────────────── 4. 积分估值参数 + 入 EV ───────────────

def test_points_valuation_not_ready_by_default():
    """真实项目默认未填 FDV/总积分/累积速率 → 诚实判不可估、积分价值 0。"""
    from backend.services.rebate_arb import points_valuation as pv

    v = pv.value_points_for_program(
        "hyperliquid_season2", notional_usd=10000.0, horizon_days=7.0
    )
    assert v.estimable is False
    assert v.my_points_value_conservative == 0.0


def test_points_valuation_ready_when_params_filled(monkeypatch):
    """填齐参数后：积分价值 > 0，且保守档 <= 基准档。"""
    from backend.services.rebate_arb import points_valuation as pv
    from backend.services.rebate_arb import program_registry as pr

    prog = pr.get_program("hyperliquid_season2")
    monkeypatch.setattr(prog, "expected_fdv_usd", 5_000_000_000.0, raising=False)
    monkeypatch.setattr(prog, "total_points_estimate", 1_000_000_000.0, raising=False)
    monkeypatch.setattr(prog, "airdrop_supply_pct", 0.10, raising=False)
    monkeypatch.setattr(prog, "points_per_1k_usd_per_day", 10.0, raising=False)

    assert prog.points_valuation_ready() is True

    v = pv.value_points_for_program(
        "hyperliquid_season2", notional_usd=10000.0, horizon_days=7.0
    )
    assert v.estimable is True
    assert v.my_points_value_base > 0
    assert v.my_points_value_conservative <= v.my_points_value_base


def test_estimate_my_points_scales_with_notional_and_time(monkeypatch):
    from backend.services.rebate_arb import program_registry as pr

    prog = pr.get_program("hyperliquid_season2")
    monkeypatch.setattr(prog, "points_per_1k_usd_per_day", 10.0, raising=False)

    # 10000 名义、7 天、10 pts/1k/day = 10 * 10 * 7 = 700
    pts = pr.estimate_my_points("hyperliquid_season2", 10000.0, 7.0)
    assert pts == pytest.approx(700.0)
    # 名义翻倍 → 积分翻倍
    pts2 = pr.estimate_my_points("hyperliquid_season2", 20000.0, 7.0)
    assert pts2 == pytest.approx(1400.0)


# ─────────────── 5. 资金费累计 + combo 透传 + 平仓含资金费 ───────────────

def test_hold_funding_pnl_pure():
    from backend.services.rebate_arb.funding_rate_provider import hold_funding_pnl

    # 10000 名义、每日净资金费 0.001、7 天 = 10000*0.001*7 = 70
    assert hold_funding_pnl(0.001, 10000.0, 7 * 86400) == pytest.approx(70.0)
    # 零名义/零时间 → 0
    assert hold_funding_pnl(0.001, 0.0, 86400) == 0.0
    assert hold_funding_pnl(0.001, 10000.0, 0.0) == 0.0
    # 负净资金费（付多于收）→ 负盈亏
    assert hold_funding_pnl(-0.0005, 10000.0, 86400) == pytest.approx(-5.0)


def test_sdn_plan_threads_combo():
    from backend.services.rebate_arb.strategies.s_delta_neutral_points import (
        DeltaNeutralPointsStrategy,
    )

    combo = {
        "symbol": "ETH/USDT",
        "long_exchange": "lighter",
        "short_exchange": "binance",
        "net_funding_per_day": 0.0012,
        "long_funding_per_day": -0.0003,
        "short_funding_per_day": 0.0009,
    }
    plan = DeltaNeutralPointsStrategy().build_execution_plan(
        size_usd=1000.0, combo=combo, paper_mode=True
    )
    # 用真实 combo 的场所/symbol，而非占位 hyperliquid/binance
    assert plan["side_a"]["exchange"] == "lighter"
    assert plan["side_b"]["exchange"] == "binance"
    assert plan["side_a"]["symbol"] == "ETH/USDT"
    # funding_meta 已写入、供平仓累计资金费
    assert plan["funding_meta"]["net_funding_per_day"] == pytest.approx(0.0012)
    assert plan["delta_neutral"] is True


def test_adaptive_horizon_pure():
    """自适应持有期：正carry+长保本→延长(封顶)；负carry/短保本→默认。"""
    from backend.services.rebate_arb.strategies.s_delta_neutral_points import (
        DeltaNeutralPointsStrategy,
    )

    s = DeltaNeutralPointsStrategy()
    # 保本 10 天 → 10×1.5=15，未超上限 21
    assert s._adaptive_horizon(
        {"net_funding_per_day": 0.0003, "breakeven_days": 10.0}
    ) == pytest.approx(15.0)
    # 保本 20 天 → 30 但封顶 21
    assert s._adaptive_horizon(
        {"net_funding_per_day": 0.0003, "breakeven_days": 20.0}
    ) == pytest.approx(21.0)
    # 负 carry → 默认 7（延长无益）
    assert s._adaptive_horizon(
        {"net_funding_per_day": -0.0001, "breakeven_days": None}
    ) == pytest.approx(s.HORIZON_DAYS)
    # 保本 < 默认窗口 → 默认 7
    assert s._adaptive_horizon(
        {"net_funding_per_day": 0.0003, "breakeven_days": 3.0}
    ) == pytest.approx(s.HORIZON_DAYS)


def test_evaluate_extends_horizon_and_improves_ev(monkeypatch):
    """保本期跨过默认窗口时，evaluate 自适应延长持有期并抬高净 EV。"""
    from backend.services.rebate_arb.strategies import s_delta_neutral_points as sdn_mod
    from backend.services.rebate_arb.strategies.s_delta_neutral_points import (
        DeltaNeutralPointsStrategy,
    )

    s = DeltaNeutralPointsStrategy()
    # 净积分估值置 0（保持诚实，纯看资金费）
    monkeypatch.setattr(s, "_value_points_usd", lambda combo, notional: 0.0)

    # 12% 毛年化、保本 10 天的正 carry 组合
    nfpd = 0.12 / 365.0
    combo = {
        "symbol": "ETH/USDT",
        "long_exchange": "gateio",
        "short_exchange": "hyperliquid",
        "net_funding_per_day": nfpd,
        "fee_drag": nfpd * 10.0,  # → breakeven 10 天
        "breakeven_days": 10.0,
    }
    monkeypatch.setattr(s, "_best_combo", lambda data: combo)

    ev = s.evaluate({"funding_rates": {"x": {"y": 1}}}, account_equity=100000.0)
    d = ev.details
    # 自适应到 15 天（10×1.5）
    assert d["horizon_adaptive"] is True
    assert d["horizon_days"] == pytest.approx(15.0)
    assert d["default_horizon_days"] == pytest.approx(7.0)
    # 持有超过保本期 → 净 EV 为正（默认 7 天时是负的）
    assert d["net_ev_usd_horizon"] > 0.0
    # combo 已写回 effective_horizon_days，供执行计划 hold_phase 使用
    assert combo["effective_horizon_days"] == pytest.approx(15.0)
    plan = s.build_execution_plan(size_usd=1000.0, combo=combo, paper_mode=True)
    assert plan["hold_phase"]["horizon_days"] == pytest.approx(15.0)


def test_paper_close_includes_funding_pnl(monkeypatch):
    """delta-neutral 仓平仓 PnL 含持仓期资金费（两腿价格抵消后的经济核心）。"""
    import time as _time

    from backend.services.rebate_arb import engine as engine_mod
    from backend.services.rebate_arb import rebate_paper_simulator as sim_mod
    from backend.services.rebate_arb import rebate_paper_market as mkt_mod
    from backend.services.rebate_arb.models import (
        RebatePosition,
        RebatePositionStatus,
        RebateStrategyType,
    )

    monkeypatch.setattr(mkt_mod, "resolve_paper_market", lambda s, e: _FakeQuote())
    monkeypatch.setattr(
        sim_mod,
        "simulate_leg_fill",
        lambda **k: _make_fake_fill(k["exchange"], k["side"], k["size_usd"], k.get("is_close", False)),
    )

    entry_a = _make_fake_fill("lighter", "buy", 10000.0, False).to_dict()
    entry_b = _make_fake_fill("binance", "sell", 10000.0, False).to_dict()

    pos = RebatePosition(
        position_id="sdn_test",
        strategy_type=RebateStrategyType.SDN_DELTA_NEUTRAL,
        source_exchange="lighter",
        target_exchange="binance",
        symbol="ETH/USDT",
        side_a_size=10000.0,
        side_b_size=10000.0,
        entry_time=_time.time() - 7 * 86400,  # 已持有 7 天
        status=RebatePositionStatus.ACTIVE,
        paper_mode=True,
        metadata={
            "delta_neutral": True,
            "funding_meta": {"net_funding_per_day": 0.001, "long_exchange": "lighter", "short_exchange": "binance"},
            "paper_entry_fills": {"a": entry_a, "b": entry_b},
            "side_a": {"exchange": "lighter", "symbol": "ETH/USDT", "side": "buy"},
            "side_b": {"exchange": "binance", "symbol": "ETH/USDT", "side": "sell"},
        },
    )

    result = engine_mod.rebate_arb_engine._paper_close_execute(pos)
    assert result["success"] is True
    summary = pos.metadata["paper_close_summary"]
    # 持仓 7 天 × 0.001/day × 10000 名义 ≈ 70 的资金费收益计入
    assert summary["funding_pnl"] == pytest.approx(70.0, abs=0.5)
    # current_pnl 应显著为正（资金费 70 >> 两腿手续费/滑点）
    assert pos.current_pnl > 50.0


# ─────────────── 6. 多场所资金费采集器 ───────────────

def test_collector_to_base_symbol():
    from backend.services import multi_venue_funding_collector as mv

    assert mv._to_base_symbol("BTC/USDT:USDT") == "BTC"
    assert mv._to_base_symbol("ETH/USDT") == "ETH"
    assert mv._to_base_symbol("btc/usdt:usdt") == "BTC"
    # 非 USDT 本位 → 过滤掉
    assert mv._to_base_symbol("BTC/USD:USD") == ""
    assert mv._to_base_symbol("BTC/USDC:USDC") == ""
    # 无斜杠/空 → 空
    assert mv._to_base_symbol("BTC") == ""
    assert mv._to_base_symbol("") == ""


def test_collector_filter_and_normalize():
    from backend.services import multi_venue_funding_collector as mv

    raw = {
        "BTC/USDT:USDT": 0.0001,
        "ETH/USDT:USDT": -0.00002,
        "DOGE/USDT:USDT": 0.00005,
        "BTC/USD:USD": 0.0003,   # 币本位 → 丢弃
        "SOL/USDT:USDT": "bad",  # 非数值 → 丢弃
    }
    out = mv._filter_and_normalize(raw, {"BTC", "ETH"})
    assert out == {"BTC": pytest.approx(0.0001), "ETH": pytest.approx(-0.00002)}
    # 无白名单时保留所有可解析 USDT 本位
    out2 = mv._filter_and_normalize(raw, None)
    assert set(out2.keys()) == {"BTC", "ETH", "DOGE"}


def test_collector_persist_empty_is_zero():
    from backend.services import multi_venue_funding_collector as mv

    assert mv._persist({}, 123456789) == 0


def test_collect_once_offline_no_fabrication(monkeypatch):
    """无场所返回数据（离线）→ offline=True、0 写入、不造数。"""
    from backend.services import multi_venue_funding_collector as mv

    monkeypatch.setattr(mv, "_run_fetch_selector_loop", lambda *a, **k: {})

    def _no_write(*a, **k):
        raise AssertionError("离线不应写库")

    # 离线时 venue_rates 为空，_persist 会因 `if not venue_rates: return 0` 提前返回，
    # 但为确保逻辑上不触达写库，这里额外断言不会真正写入。
    monkeypatch.setattr(mv, "_persist", lambda vr, ts: 0 if not vr else _no_write())

    summary = mv.collect_once(symbols=["BTC"], venues=["binance"])
    assert summary["offline"] is True
    assert summary["rows_written"] == 0
    assert summary["venues_with_data"] == []


def test_collect_once_writes_when_data_present(monkeypatch):
    """有真实场所数据时：汇总正确、覆盖的 symbol 正确。"""
    from backend.services import multi_venue_funding_collector as mv

    fake = {"gateio": {"BTC": 0.0001, "ETH": 0.00002}}
    monkeypatch.setattr(mv, "_run_fetch_selector_loop", lambda *a, **k: fake)
    written_calls = {}

    def _fake_persist(venue_rates, ts_ms):
        written_calls["rates"] = venue_rates
        return sum(len(m) for m in venue_rates.values())

    monkeypatch.setattr(mv, "_persist", _fake_persist)

    summary = mv.collect_once(symbols=["BTC", "ETH"], venues=["gateio"])
    assert summary["offline"] is False
    assert summary["venues_with_data"] == ["gateio"]
    assert summary["rows_written"] == 2
    assert summary["symbols_covered"] == ["BTC", "ETH"]
    assert written_calls["rates"] == fake


def test_collect_once_emits_per_venue_report(monkeypatch):
    """摘要含逐场所诊断：成功/取消各有状态，未回报的场所兜底 unknown。"""
    from backend.services import multi_venue_funding_collector as mv

    def _fake_loop(venues, symbols_upper, diagnostics=None, **k):
        if diagnostics is not None:
            diagnostics["gateio"] = {"status": "ok", "count": 2, "elapsed_ms": 120, "via": "per_symbol"}
            diagnostics["okx"] = {"status": "cancelled", "count": 0, "elapsed_ms": 0, "via": None}
        return {"gateio": {"BTC": 0.0001, "ETH": 0.00002}}

    monkeypatch.setattr(mv, "_run_fetch_selector_loop", _fake_loop)
    monkeypatch.setattr(mv, "_persist", lambda vr, ts: sum(len(m) for m in vr.values()))

    summary = mv.collect_once(symbols=["BTC", "ETH"], venues=["gateio", "okx", "binance"])
    report = summary["venue_report"]
    # 每个请求的场所都有一条
    assert set(report.keys()) == {"gateio", "okx", "binance"}
    assert report["gateio"]["status"] == "ok"
    assert report["gateio"]["via"] == "per_symbol"
    assert report["okx"]["status"] == "cancelled"
    # 未回报的 binance 兜底为 unknown（诚实：不假装成功）
    assert report["binance"]["status"] == "unknown"


def test_funding_matrix_endpoint_adds_sdn_flags(monkeypatch):
    """/funding-matrix 的 combos 叠加 SDN 自适应持有期可行性标记。"""
    import asyncio

    from backend.services.rebate_arb import funding_rate_provider as frp
    from backend.api import rebate_routes as rr

    by_venue = {
        "gateio": {"ETH/USDT": 0.00001},
        "hyperliquid": {"ETH/USDT": 0.00005},
    }
    monkeypatch.setattr(frp, "latest_funding_by_venue", lambda use_cache=True: by_venue)
    monkeypatch.setattr(frp, "has_multi_venue_coverage", lambda d: True)

    r = asyncio.run(rr.get_funding_matrix(horizon_days=7.0, use_taker=True, min_net_apr=-1e9))
    assert r["multi_venue"] is True
    assert r["combos"], "应至少形成一个 delta-neutral 组合"
    c = r["combos"][0]
    for k in ("sdn_horizon_days", "sdn_net_apr", "sdn_viable", "sdn_min_net_apr", "sdn_horizon_adaptive"):
        assert k in c, f"combo 缺少 SDN 字段 {k}"
    assert isinstance(c["sdn_viable"], bool)
    assert c["sdn_horizon_days"] >= 7.0


def test_maybe_alert_fires_after_threshold_and_recovers(monkeypatch):
    """连续失败达阈值→告警一次（不重复刷）；恢复→复位并发恢复通知。"""
    from backend.config import settings
    from backend.services import multi_venue_funding_collector as mv

    # 隔离全局计数/告警态，避免与其他用例串扰
    mv._CONSEC_FAIL.clear()
    mv._ALERTED_VENUES.clear()
    monkeypatch.setattr(settings, "MULTI_VENUE_FUNDING_ALERT_THRESHOLD", 2, raising=False)

    sent = []

    class _FakeNotifier:
        def send_sync(self, text="", title="", level="info", event_type="system"):
            sent.append((title, text))
            return True

    import backend.services.openclaw_notify as notify_mod
    monkeypatch.setattr(notify_mod, "get_notifier", lambda: _FakeNotifier())

    fail = {"okx": {"status": "error", "count": 0, "elapsed_ms": 10, "via": None, "error": "boom"}}
    ok = {"okx": {"status": "ok", "count": 3, "elapsed_ms": 20, "via": "bulk"}}

    # 第 1 轮失败：未达阈值，不告警
    assert mv._maybe_alert(fail) == []
    assert not sent
    # 第 2 轮失败：达阈值 → 告警一次
    assert mv._maybe_alert(fail) == ["okx"]
    assert len(sent) == 1 and "okx" in sent[0][1]
    # 第 3 轮仍失败：已在告警态，不重复
    assert mv._maybe_alert(fail) == []
    assert len(sent) == 1
    # 恢复：复位 + 恢复通知
    assert mv._maybe_alert(ok) == []
    assert mv._CONSEC_FAIL["okx"] == 0
    assert "okx" not in mv._ALERTED_VENUES
    assert any("恢复" in t for t, _ in sent)


def test_maybe_alert_empty_status_not_counted(monkeypatch):
    """status=empty（连通但无匹配symbol）不计为故障，不触发告警。"""
    from backend.config import settings
    from backend.services import multi_venue_funding_collector as mv

    mv._CONSEC_FAIL.clear()
    mv._ALERTED_VENUES.clear()
    monkeypatch.setattr(settings, "MULTI_VENUE_FUNDING_ALERT_THRESHOLD", 1, raising=False)

    empty = {"bybit": {"status": "empty", "count": 0, "elapsed_ms": 5, "via": "bulk"}}
    assert mv._maybe_alert(empty) == []
    assert mv._maybe_alert(empty) == []
    assert mv._CONSEC_FAIL.get("bybit", 0) == 0


def test_collector_status_endpoint_shape(monkeypatch):
    """/funding-collector/status 透出配置 + 最近一轮健康快照。"""
    import asyncio

    from backend.services import multi_venue_funding_collector as mv
    from backend.api import rebate_routes as rr

    mv._LAST_REPORT.clear()
    mv._LAST_REPORT.update({
        "venues_with_data": ["gateio"],
        "rows_written": 2,
        "symbols_covered": ["BTC", "ETH"],
        "offline": False,
        "elapsed_ms": 130,
        "as_of": 1700000000000,
        "as_of_iso": "2023-11-14T22:13:20+00:00",
        "venue_report": {
            "gateio": {"status": "ok", "count": 2, "elapsed_ms": 120, "via": "per_symbol"},
            "okx": {"status": "error", "count": 0, "elapsed_ms": 0, "via": None, "error": "x"},
        },
    })

    r = asyncio.run(rr.get_funding_collector_status())
    assert r["has_report"] is True
    assert r["rows_written"] == 2
    assert set(r["venue_report"].keys()) == {"gateio", "okx"}
    assert r["venue_report"]["gateio"]["status"] == "ok"
    assert "consecutive_failures" in r and "alerted_venues" in r
