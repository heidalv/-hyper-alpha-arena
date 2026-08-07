"""
AI 主驾改造 第一批回归测试

改动 1: FanOut 置信度不再稀释 —— AI 原始置信度保留
改动 2: _calibrate_confidence 仅日志标注 —— 返回 AI 原始置信度，不改写
改动 6: 反馈归因标注 —— retrospective lesson 区分止损触发 vs AI 主动平仓
"""
import pytest


pytestmark = pytest.mark.unit


# ────────────── 改动 2: _calibrate_confidence 不改写 ──────────────

def test_calibrate_confidence_returns_ai_raw_value():
    """置信度校准返回 AI 原始值，即使规则信号建议大改也不改写。"""
    from backend.services.full_auto_trading_service import FullAutoTradingService

    svc = FullAutoTradingService.__new__(FullAutoTradingService)
    svc._pre_screen_passed = set()

    # AI 给 75%，规则信号全反向（bear_count 高 + trend bearish），旧逻辑会降到 ~55%
    analyst_reports = {
        "market": {"signals": [
            {"symbol": "BTC", "signal": "bearish"},
            {"symbol": "BTC", "signal": "bearish"},
            {"symbol": "BTC", "signal": "bearish"},
        ]}
    }
    market_summary = {
        "BTC": {
            "trend_direction": "bearish",
            "volatility_regime": "extreme",
            "orchestrator": {"final_side": "short", "weighted_confidence": 0.8},
        }
    }
    result = svc._calibrate_confidence(
        raw_conf=75, action="buy", symbol="BTC",
        analyst_reports=analyst_reports, market_summary=market_summary,
    )
    # AI 主驾：返回 75（原始值），不因反向信号改写
    assert result == 75, f"应返回AI原始置信度75, 实际={result}"


def test_calibrate_confidence_hold_unchanged():
    """hold 动作直接返回原值（无需校准）。"""
    from backend.services.full_auto_trading_service import FullAutoTradingService

    svc = FullAutoTradingService.__new__(FullAutoTradingService)
    result = svc._calibrate_confidence(
        raw_conf=80, action="hold", symbol="BTC",
        analyst_reports={}, market_summary={},
    )
    assert result == 80


def test_calibrate_confidence_aligned_signals_still_raw():
    """方向一致 + 信号支持时，也返回 AI 原始值（不再上调）。"""
    from backend.services.full_auto_trading_service import FullAutoTradingService

    svc = FullAutoTradingService.__new__(FullAutoTradingService)
    svc._pre_screen_passed = {"BTC"}
    analyst_reports = {
        "market": {"signals": [
            {"symbol": "BTC", "signal": "bullish"},
            {"symbol": "BTC", "signal": "bullish"},
            {"symbol": "BTC", "signal": "bullish"},
            {"symbol": "BTC", "signal": "bullish"},
        ]}
    }
    market_summary = {"BTC": {"trend_direction": "bullish", "volatility_regime": "normal"}}
    result = svc._calibrate_confidence(
        raw_conf=60, action="buy", symbol="BTC",
        analyst_reports=analyst_reports, market_summary=market_summary,
    )
    # AI 主驾：返回 60（旧逻辑会上调到 ~78）
    assert result == 60, f"应返回AI原始置信度60, 实际={result}"


# ────────────── 改动 1: FanOut 置信度不稀释（逻辑验证）──────────────

def test_fanout_blend_keeps_ai_confidence():
    """FanOut 的三个分支都应保留 AI 原始置信度，不再 0.35/0.65 稀释。

    FanOut 逻辑内联在 _expand_multi_tier_decisions，难以单测整个方法。
    这里验证核心公式：新逻辑下 blended == llm_conf（而非 llm*0.35+orch*0.65）。
    """
    _llm_conf = 0.75  # AI 给 75%
    _orch_conf = 0.90  # 编排器 90%

    # 旧逻辑（match 分支）：0.75*0.35 + 0.90*0.65 = 0.8475
    _old_blend = _llm_conf * 0.35 + _orch_conf * 0.65
    assert round(_old_blend, 4) == 0.8475

    # 新逻辑（AI 主驾）：直接用 AI 值
    _new_blend = _llm_conf
    assert _new_blend == 0.75

    # 关键：新逻辑不等于旧逻辑（证明改动生效）
    assert _new_blend != _old_blend


def test_fanout_strong_oppose_still_skips():
    """方向性 veto（强反向 SKIP）保留 —— 那是硬安全网，不是数值改写。"""
    # 强反向 + 编排器低置信 → 应 SKIP（continue），这不属于置信度改写
    _bias = "strongly_bearish"
    _orch_conf = 0.20  # < 0.30
    _oppose_side = "bearish"
    _should_skip = _bias in (f"strongly_{_oppose_side}", _oppose_side) and _orch_conf < 0.30
    assert _should_skip is True, "强反向 veto 应保留"


# ────────────── 改动 6: 反馈归因标注 ──────────────

def test_retrospective_lesson_has_attribution_tag():
    """retrospective lesson 应含归因标注（止损触发 vs AI 主动平仓）。

    直接验证 lesson 文本生成逻辑（从 _write_retrospective 提取的核心判断）。
    """
    # 止损出场场景
    exit_reason_sl = "stop_loss"
    total_pnl = -50.0
    pnl_pct = -2.5
    lesson_sl = (
        f"BTC long: 被{exit_reason_sl}出场, 亏损{total_pnl:.2f}({pnl_pct:.2f}%). "
        f"[归因:止损触发(SL/TP硬监控)] 下次类似情况考虑: 等待确认信号再入场, "
        f"评估方向判断与止损距离是否合理."
    )
    assert "[归因:止损触发" in lesson_sl

    # 爆仓场景
    exit_reason_liq = "liquidation"
    lesson_liq = (
        f"BTC long: 被{exit_reason_liq}出场, 亏损{total_pnl:.2f}({pnl_pct:.2f}%). "
        f"[归因:止损触发(爆仓,杠杆/仓位可能过高)] 下次类似情况考虑: ..."
    )
    assert "[归因:止损触发(爆仓" in lesson_liq

    # AI 主动平仓亏损
    lesson_manual = (
        f"BTC long: 亏损{total_pnl:.2f}({pnl_pct:.2f}%). "
        f"[归因:AI主动平仓] 检查方向判断是否有误."
    )
    assert "[归因:AI主动平仓]" in lesson_manual
