"""S8 积分最大化模式单元测试"""

from backend.services.rebate_arb.rebate_position_mtm import (
    _estimate_paper_points,
    _s8_rh_display_fields,
    serialize_position_for_api,
)
from backend.services.rebate_arb.models import RebatePosition, RebatePositionStatus, RebateStrategyType
from backend.services.rebate_arb.strategies.s8_asterdex_rh import S8AsterdexRhStrategy
import time


def test_points_mode_full_margin_notional():
    s8 = S8AsterdexRhStrategy({"points_maximization_mode": True, "default_leverage": 10})
    plan = s8.build_execution_plan(
        size_usd=60.0,
        symbol="BTC/USDT",
        paper_mode=True,
        ai_signal={
            "available": True,
            "direction": "bullish",
            "confidence": 40,
            "risk_level": "warning",
        },
        points_maximization=True,
    )
    assert not plan.get("skip")
    # size_usd=60 为名义价值；warning 在积分模式下仍 ≥90% 满配 → margin ≥ 60×0.9/10
    assert plan["margin_usd"] >= 5.4
    assert plan["side_a"]["size_usd"] == round(plan["margin_usd"] * 10, 2)
    assert plan["side_a"]["margin_usd"] == plan["margin_usd"]
    assert plan["symbol_boost"] == 1.5


def test_neutral_direction_skips_by_default():
    """AI 方向中性时默认 skip，杜绝隐式默认做多。"""
    s8 = S8AsterdexRhStrategy({"points_maximization_mode": True})
    plan = s8.build_execution_plan(
        size_usd=60.0,
        symbol="BTC/USDT",
        paper_mode=True,
        ai_signal={
            "available": True,
            "direction": "neutral",
            "confidence": 55,
            "risk_level": "normal",
        },
        points_maximization=True,
    )
    assert plan.get("skip") is True
    assert "中性" in plan.get("skip_reason", "")


def test_neutral_direction_half_when_configured():
    """显式配置 neutral_direction_action=half 时允许半仓。"""
    s8 = S8AsterdexRhStrategy({
        "points_maximization_mode": False,
        "neutral_direction_action": "half",
        "default_leverage": 10,
    })
    plan = s8.build_execution_plan(
        size_usd=60.0,
        symbol="BTC/USDT",
        paper_mode=True,
        ai_signal={
            "available": True,
            "direction": "neutral",
            "confidence": 60,
            "risk_level": "normal",
        },
        points_maximization=False,
    )
    assert not plan.get("skip")
    # 非积分模式: 0.5 (neutral half) × 0.8 (confidence 60) = 0.4
    assert plan["margin_usd"] <= 60.0 * 0.5


def test_points_mode_danger_skips():
    s8 = S8AsterdexRhStrategy({"points_maximization_mode": True})
    plan = s8.build_execution_plan(
        size_usd=60.0,
        ai_signal={
            "available": True,
            "direction": "bearish",
            "confidence": 30,
            "risk_level": "danger",
        },
        points_maximization=True,
    )
    assert plan.get("skip") is True


def test_symbol_boost_scoring_order():
    s8 = S8AsterdexRhStrategy()
    assert s8.symbol_boost("BTC/USDT") == 1.5
    assert s8.symbol_boost("DOGE/USDT") == 1.0


def test_s8_rh_display_fields():
    pos = RebatePosition(
        position_id="s8-test",
        strategy_type=RebateStrategyType.S8_ASTERDEX_RH,
        source_exchange="asterdex",
        target_exchange=None,
        symbol="ETH/USDT",
        side_a_size=600.0,
        entry_time=time.time() - 1800,
        paper_mode=True,
        status=RebatePositionStatus.ACTIVE,
        metadata={
            "margin_usd": 60.0,
            "symbol_boost": 1.2,
            "hold_start_time": time.time() - 1800,
            "hold_target_time": time.time() + 2100,
            "hold_phase": {"total_seconds": 3900},
            "estimated_round_rh": 115.2,
            "multiplier_stack": {"taker": 2, "hold_time": 2, "usdf": 20, "symbol_boost": 1.2},
        },
    )
    api = serialize_position_for_api(pos)
    assert api["rh_target_hours"] == 1.08
    assert api["estimated_round_rh"] == 115.2
    assert api["symbol_boost"] == 1.2
    assert api["rh_time_bonus_active"] is False


def test_s8_paper_points_match_rh_progress_not_compound():
    """持仓积分应与整轮 Rh 预估 × 时间进度一致，MTM 多次刷新不得重复累加。"""
    meta = {
        "margin_usd": 51.94,
        "symbol_boost": 1.5,
        "hold_phase": {"total_seconds": 3900},
        "estimated_round_rh": 12.47,
    }
    pos = RebatePosition(
        position_id="s8-pts",
        strategy_type=RebateStrategyType.S8_ASTERDEX_RH,
        source_exchange="asterdex",
        target_exchange=None,
        symbol="BTC/USDT",
        side_a_size=519.4,
        entry_time=time.time() - 3600,
        paper_mode=True,
        status=RebatePositionStatus.ACTIVE,
        metadata=meta,
        accumulated_points=999.0,  # 旧 bug 会误当作 base 叠加
    )

    once = _estimate_paper_points(pos, meta)
    pos.accumulated_points = once
    twice = _estimate_paper_points(pos, meta)
    assert once == twice
    assert once == round(12.47 * (3600 / 3900), 2)


def test_s8_risk_exposure_uses_margin_not_notional():
    from backend.services.rebate_arb.engine import _position_risk_exposure_usd

    pos = RebatePosition(
        position_id="s8-exp",
        strategy_type=RebateStrategyType.S8_ASTERDEX_RH,
        source_exchange="asterdex",
        target_exchange=None,
        symbol="BTC/USDT",
        side_a_size=519.4,
        paper_mode=True,
        status=RebatePositionStatus.ACTIVE,
        metadata={"margin_usd": 51.94, "leverage": 10},
    )
    assert _position_risk_exposure_usd(pos) == 51.94


def test_s8_round_metrics_include_cost_and_quality_scores():
    s8 = S8AsterdexRhStrategy({"points_maximization_mode": True, "default_leverage": 10})
    metrics = s8.estimate_round_metrics(
        margin_usd=50,
        leverage=10,
        symbol="ASTER/USDT",
        hold_seconds=3900,
        confidence=80,
        risk_level="normal",
    )
    assert metrics["round_volume_usd"] == 1000
    assert metrics["estimated_rh"] > 0
    assert metrics["estimated_cost_usd"] > 0
    assert metrics["rh_per_fee_usd"] > 0
    assert 0 <= metrics["round_quality_score"] <= 100
    assert 0 <= metrics["safety_score"] <= 100


def test_s8_stage6_ev_model_fee_corrected_and_breakdown():
    """Stage 6 模型：费率校正为 0.04% taker / 0% maker，输出积分类别明细与净 EV。"""
    s8 = S8AsterdexRhStrategy()
    fees = s8.stage6_fee_rates()
    assert fees["taker"] == 0.0004
    assert fees["maker"] == 0.0

    metrics = s8.estimate_round_metrics(
        margin_usd=50, leverage=10, symbol="BTC/USDT", hold_seconds=3900,
    )
    assert metrics["formula_version"] == "s8_stage6_ev_v1"
    # taker 费 = 1000 × 0.04% = $0.40（旧模型按 0.005% 只算 $0.05）
    assert abs(metrics["gross_fee_usd"] - 0.4) < 1e-9
    s6 = metrics["stage6"]
    assert s6["trading_points"] > 0
    assert s6["position_points"] > 0
    assert s6["asset_points"] > 0
    assert s6["valuation_speculative"] is True
    assert "net_ev_usd" in metrics


def test_s8_stage6_maker_ratio_cuts_fee_and_keeps_points():
    """Maker 占比提高 → 费用下降，交易积分仍有 Maker 流动性贡献。"""
    s8 = S8AsterdexRhStrategy()
    taker_only = s8.estimate_round_metrics(
        margin_usd=50, leverage=10, symbol="BTC/USDT", maker_ratio=0.0,
    )
    maker_first = s8.estimate_round_metrics(
        margin_usd=50, leverage=10, symbol="BTC/USDT", maker_ratio=0.9,
    )
    assert maker_first["estimated_cost_usd"] < taker_only["estimated_cost_usd"]
    assert maker_first["stage6"]["maker_volume_usd"] > 0
    assert maker_first["net_ev_usd"] > taker_only["net_ev_usd"]


def test_s8_stage6_optimal_is_default_with_maker_first_plan():
    """stage6_optimal 为默认模式：限价 Maker 优先 + Taker 回退 + 全仓 pre-step。"""
    s8 = S8AsterdexRhStrategy({"points_maximization_mode": True, "default_leverage": 10})
    plan = s8.build_execution_plan(
        size_usd=60.0,
        symbol="BTC/USDT",
        paper_mode=True,
        ai_signal={
            "available": True,
            "direction": "bullish",
            "confidence": 80,
            "risk_level": "normal",
        },
        points_maximization=True,
    )
    assert plan["rh_optimization_mode"] == "stage6_optimal"
    # Maker 优先：开/平仓均为限价 post_only，带 Taker 回退
    assert plan["side_a"]["type"] == "limit"
    assert plan["side_a"]["post_only"] is True
    assert plan["side_a"]["taker_fallback"] is True
    assert plan["side_a"]["margin_mode"] == "cross"
    assert plan["close_plan"]["type"] == "limit"
    assert plan["close_plan"]["taker_fallback"] is True
    # 全仓模式确认 pre-step
    actions = [s["action"] for s in plan["pre_steps"]]
    assert "mint_usdf" in actions
    assert "ensure_cross_margin" in actions
    # 动态持仓默认 4h
    assert plan["hold_phase"]["total_seconds"] == s8.STAGE6_HOLD_DEFAULT_SECONDS
    assert plan["target"] == "rh_points_stage6"
    assert plan["stage6_breakdown"]["maker_ratio"] == 0.7


def test_s8_stage6_dynamic_hold_follows_funding_direction():
    """资金费方向决定持仓时长：成本→缩短到 2h，收益→拉长到 8h。"""
    s8 = S8AsterdexRhStrategy({"default_leverage": 10})
    bullish = {"available": True, "direction": "bullish", "confidence": 80, "risk_level": "normal"}
    bearish = {"available": True, "direction": "bearish", "confidence": 80, "risk_level": "normal"}

    # 多头 + 正资金费 = 成本 → 最短持仓
    plan_cost = s8.build_execution_plan(
        size_usd=60.0, symbol="BTC/USDT", paper_mode=True,
        ai_signal=bullish, funding_rate=0.0005,
    )
    assert plan_cost["hold_phase"]["total_seconds"] == s8.STAGE6_HOLD_MIN_SECONDS
    assert "funding_cost_shorten" in plan_cost["rh_optimizer"]["reasons"]

    # 空头 + 正资金费 = 收益 → 最长持仓
    plan_income = s8.build_execution_plan(
        size_usd=60.0, symbol="BTC/USDT", paper_mode=True,
        ai_signal=bearish, funding_rate=0.0005,
    )
    assert plan_income["hold_phase"]["total_seconds"] == s8.STAGE6_HOLD_MAX_SECONDS
    assert "funding_income_extend" in plan_income["rh_optimizer"]["reasons"]

    # 资金费不显著 → 默认时长
    plan_default = s8.build_execution_plan(
        size_usd=60.0, symbol="BTC/USDT", paper_mode=True,
        ai_signal=bullish, funding_rate=0.0,
    )
    assert plan_default["hold_phase"]["total_seconds"] == s8.STAGE6_HOLD_DEFAULT_SECONDS


def test_s8_stage6_can_fall_back_to_legacy_safe_mode():
    """显式配置 rh_optimization_mode=safe 时回退旧 Taker 市价行为。"""
    s8 = S8AsterdexRhStrategy({"rh_optimization_mode": "safe", "default_leverage": 10})
    plan = s8.build_execution_plan(
        size_usd=60.0,
        symbol="BTC/USDT",
        paper_mode=True,
        ai_signal={
            "available": True,
            "direction": "bullish",
            "confidence": 80,
            "risk_level": "normal",
        },
    )
    assert plan["rh_optimization_mode"] == "safe"
    assert plan["side_a"]["type"] == "market"
    assert "taker_fallback" not in plan["side_a"]
    actions = [s["action"] for s in plan["pre_steps"]]
    assert "ensure_cross_margin" not in actions


def test_s8_quick_mode_downgrades_when_signal_weak():
    s8 = S8AsterdexRhStrategy({
        "points_maximization_mode": True,
        "rh_optimization_mode": "quick",
        "default_leverage": 15,
    })
    plan = s8.build_execution_plan(
        size_usd=60,
        symbol="BTC/USDT",
        paper_mode=True,
        ai_signal={
            "available": True,
            "direction": "bullish",
            "confidence": 40,
            "risk_level": "normal",
        },
        points_maximization=True,
    )
    assert plan["rh_optimization_mode"] == "safe"
    assert plan["side_a"]["leverage"] <= s8.SAFE_MAX_LEVERAGE
    assert "confidence_below_quick_threshold" in plan["rh_optimizer"]["reasons"]


def test_s8_paper_experiment_uses_short_hold_and_ab_matrix():
    s8 = S8AsterdexRhStrategy({
        "points_maximization_mode": True,
        "rh_optimization_mode": "paper_experiment",
        "paper_experiment_hold_seconds": 900,
    })
    plan = s8.build_execution_plan(
        size_usd=60,
        symbol="ASTER/USDT",
        paper_mode=True,
        ai_signal={
            "available": True,
            "direction": "bullish",
            "confidence": 80,
            "risk_level": "normal",
        },
        points_maximization=True,
    )
    assert plan["rh_optimization_mode"] == "paper_experiment"
    assert plan["hold_phase"]["total_seconds"] == 900
    assert plan["close_plan"]["min_elapsed_seconds"] == 900
    assert len(plan["paper_ab_test_matrix"]) >= 4
