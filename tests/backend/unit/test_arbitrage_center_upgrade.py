"""套利中心升级（Phase 0-3）单元测试。

覆盖：
- program_registry：程序生命周期、策略自检、离线费率兜底
- ALL_STRATEGIES：注册表构建、S1/S5 排除、SDN 注册、引擎可 import
- funding_rate_matrix：多空腿选择、净资金费/成本、积分场所偏好
- points_valuation：FDV 折现估值、数据不足不臆造、净 EV
- paper_delta_neutral_executor：双腿成交、空腿失败回滚、delta 漂移、成本模型
- arb_switches：两条链路开关语义
"""

import pytest


# ─────────────── program_registry ───────────────

def test_program_registry_lifecycle():
    from backend.services.rebate_arb import program_registry as pr

    aster = pr.get_program("aster_stage6")
    assert aster is not None
    assert aster.status == "ended"
    assert not aster.is_active()

    hl = pr.get_program("hyperliquid_season2")
    assert hl is not None
    assert hl.status == "active"
    assert hl.is_active()


def test_program_registry_strategy_selfcheck():
    from backend.services.rebate_arb import program_registry as pr

    # S8 → Aster Stage6 ended → 不应刷
    assert pr.is_strategy_program_active("S8") is False
    assert pr.strategy_program_status("S8") == "ended"
    # S3 → HL Season2 active
    assert pr.is_strategy_program_active("S3") is True
    # 无对应项目的策略（纯资金费）不受约束
    assert pr.is_strategy_program_active("S6") is True
    assert pr.strategy_program_status("S6") == "no_program"


def test_program_registry_empty_strategy_id_no_false_match():
    from backend.services.rebate_arb import program_registry as pr

    # 空 sid 不应误匹配到 strategy_id=None 的项目
    assert pr.get_program_for_strategy("") is None
    assert pr.get_program_for_strategy(None) is None


def test_program_registry_offline_fees():
    from backend.services.rebate_arb import program_registry as pr

    hl = pr.get_offline_incentive("hyperliquid")
    assert hl["maker_rate"] == pytest.approx(0.00015)
    assert hl["taker_rate"] == pytest.approx(0.00045)
    unknown = pr.get_offline_incentive("nonexistent_ex")
    assert "maker_rate" in unknown and "taker_rate" in unknown


# ─────────────── ALL_STRATEGIES ───────────────

def test_all_strategies_registry():
    from backend.services.rebate_arb.strategies import ALL_STRATEGIES, DEPRECATED_STRATEGY_IDS

    # S1/S5 下线，不注册
    assert "S1" not in ALL_STRATEGIES
    assert "S5" not in ALL_STRATEGIES
    assert set(DEPRECATED_STRATEGY_IDS) == {"S1", "S5"}
    # 新 delta-neutral 主力已注册
    assert "SDN" in ALL_STRATEGIES
    # 每个策略都有统一接口
    for sid, strat in ALL_STRATEGIES.items():
        assert hasattr(strat, "evaluate")
        assert hasattr(strat, "update_params")


def test_engine_imports_without_error():
    # 病灶A 回归：引擎顶层 import ALL_STRATEGIES 不再崩溃
    from backend.services.rebate_arb.engine import rebate_arb_engine

    assert rebate_arb_engine is not None
    assert rebate_arb_engine._is_strategy_program_active("S8") is False
    assert rebate_arb_engine._is_strategy_program_active("SDN") is True


# ─────────────── funding_rate_matrix ───────────────

def test_funding_matrix_picks_long_low_short_high():
    from backend.services.rebate_arb.funding_rate_matrix import scan_funding_matrix

    # binance 资金费高（应做空），hyperliquid 负（应做多）
    fr = {
        "binance": {"BTC/USDT": 0.0001},
        "hyperliquid": {"BTC/USDT": -0.00003},
    }
    combos = scan_funding_matrix(fr, horizon_days=7, min_net_apr=-1e9)
    assert len(combos) == 1
    c = combos[0]
    assert c.long_exchange == "hyperliquid"   # 资金费最低处做多
    assert c.short_exchange == "binance"      # 资金费最高处做空
    assert c.net_funding_per_day > 0
    assert c.points_long_leg is True          # HL 有 active 积分项目


def test_funding_matrix_single_venue_no_combo():
    from backend.services.rebate_arb.funding_rate_matrix import scan_funding_matrix

    fr = {"binance": {"BTC/USDT": 0.0001}}
    assert scan_funding_matrix(fr, min_net_apr=-1e9) == []


# ─────────────── points_valuation ───────────────

def test_points_valuation_estimable():
    from backend.services.rebate_arb.points_valuation import PointsValuationInput, value_points

    v = value_points(PointsValuationInput(
        program_id="p",
        expected_fdv_usd=1_000_000_000,
        airdrop_supply_pct=0.10,
        total_points_estimate=1_000_000_000,
        my_points_estimate=10_000,
    ))
    assert v.estimable is True
    # conservative < base < optimistic
    assert v.my_points_value_conservative < v.my_points_value_base < v.my_points_value_optimistic


def test_points_valuation_refuses_without_data():
    from backend.services.rebate_arb.points_valuation import PointsValuationInput, value_points

    v = value_points(PointsValuationInput(program_id="p"))
    assert v.estimable is False
    assert v.my_points_value_base == 0.0


def test_net_ev_deducts_costs():
    from backend.services.rebate_arb.points_valuation import net_ev

    r = net_ev(
        notional_usd=1000,
        net_funding_per_day=0.001,
        fee_drag=0.002,
        horizon_days=7,
        points_value_usd=0.0,
    )
    # 资金费 7 天 = 0.007*1000=7；成本=0.002*1000=2 → 净 5
    assert r.gross_funding_pnl_usd == pytest.approx(7.0)
    assert r.fee_cost_usd == pytest.approx(2.0)
    assert r.net_ev_usd == pytest.approx(5.0)


# ─────────────── paper_delta_neutral_executor ───────────────

class _FakeQuote:
    def __init__(self, mid):
        self.mid = mid
        self.bid = mid * 0.9999
        self.ask = mid * 1.0001


def _plan():
    return {
        "side_a": {"exchange": "hyperliquid", "symbol": "BTC/USDT", "side": "buy"},
        "side_b": {"exchange": "binance", "symbol": "BTC/USDT", "side": "sell"},
    }


def test_dn_executor_both_legs_neutral():
    from backend.services.rebate_arb.paper_delta_neutral_executor import PaperDeltaNeutralExecutor

    ex = PaperDeltaNeutralExecutor(quote_resolver=lambda e, s: _FakeQuote(100.0))
    res = ex.execute(_plan(), 200.0, combo={"net_funding_per_day": 0.001}, horizon_days=7)
    assert res.success is True
    assert res.rolled_back is False
    assert res.delta_drift_pct == pytest.approx(0.0)
    assert "net_ev_usd" in res.cost_model


def test_dn_executor_rolls_back_on_leg_b_failure():
    from backend.services.rebate_arb.paper_delta_neutral_executor import PaperDeltaNeutralExecutor

    def resolver(exchange, symbol):
        return _FakeQuote(100.0) if exchange == "hyperliquid" else None

    ex = PaperDeltaNeutralExecutor(quote_resolver=resolver)
    res = ex.execute(_plan(), 200.0)
    assert res.success is False
    assert res.rolled_back is True
    # 长腿成交 + 空腿失败 + 回滚平仓 三条记录
    assert any(l.reason == "rollback_close" for l in res.legs)


def test_dn_executor_no_naked_leg_when_a_fails():
    from backend.services.rebate_arb.paper_delta_neutral_executor import PaperDeltaNeutralExecutor

    ex = PaperDeltaNeutralExecutor(quote_resolver=lambda e, s: None)
    res = ex.execute(_plan(), 200.0)
    assert res.success is False
    assert res.rolled_back is False  # 长腿都没成，无需回滚


# ─────────────── arb_switches ───────────────

def test_arb_switches_semantics():
    from backend.services.rebate_arb.arb_switches import get_arb_switch_status, is_v3_arb_runnable

    st = get_arb_switch_status(session_arb_enabled=False)
    # V3 需要 env AND session；session=False → 不可运行
    assert st.v3_runnable is False
    assert is_v3_arb_runnable(False) is False
    # Rebate paper 扫描恒可运行
    assert st.rebate_scan_runnable is True
    # 实盘恒关
    assert st.live_trading_enabled is False


# ─────────────── SDN 策略 ───────────────

def test_sdn_strategy_honest_without_funding_data():
    from backend.services.rebate_arb.strategies import ALL_STRATEGIES

    sdn = ALL_STRATEGIES["SDN"]
    ev = sdn.evaluate({}, 300.0)
    assert ev.is_viable is False
    assert "无资金费率数据" in ev.details.get("reason", "")


def test_sdn_strategy_viable_with_funding_data():
    from backend.services.rebate_arb.strategies import ALL_STRATEGIES

    sdn = ALL_STRATEGIES["SDN"]
    incentive = {"funding_rates": {"binance": {"BTC/USDT": 0.0002}, "hyperliquid": {"BTC/USDT": -0.00005}}}
    ev = sdn.evaluate(incentive, 300.0)
    assert ev.is_viable is True
    assert ev.details["source_exchange"] == "hyperliquid"
    assert ev.details["hedge_exchange"] == "binance"
    assert ev.details["delta_neutral"] is True
