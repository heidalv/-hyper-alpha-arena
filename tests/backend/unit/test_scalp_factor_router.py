"""ScalpFactorRouter 单元测试"""
import pytest


pytestmark = pytest.mark.unit


def test_is_scalp_nature():
    from backend.services.scalp_factor_router import ScalpFactorRouter
    r = ScalpFactorRouter()
    assert r.is_scalp_nature("scalp") is True
    assert r.is_scalp_nature("intraday") is True
    assert r.is_scalp_nature("swing") is False
    assert r.is_scalp_nature("trend_follow") is False


def test_low_factor_score_hold(monkeypatch):
    """因子分数 < 50 → hold（隔离清算磁吸独立点燃分支，只测因子分数本身的门槛逻辑）。"""
    from backend.services.scalp_factor_router import ScalpFactorRouter
    from backend.services import crypto_alpha_signals
    from backend.services.crypto_alpha_signals import AlphaReading

    # 显式关闭磁吸信号，避免真实环境里的实时清算数据把 neutral 独立点燃成有方向信号
    # （那是 2026-07-07 新增的独立开仓功能，见下面 test_liq_magnet_seeds_* 系列测试）
    monkeypatch.setattr(
        crypto_alpha_signals.crypto_alpha, "liquidation_magnet",
        lambda symbol: AlphaReading(available=False),
    )

    r = ScalpFactorRouter()
    md = {"price": 65000, "indicators": {"rsi": 50, "macd": 0, "ema_trend": 0}}
    sig = r.evaluate("BTC", md)
    assert sig.action == "hold"
    assert sig.factor_score < 50


def test_high_factor_score_buysignal():
    """因子分数高 + 看多 → buy。"""
    from backend.services.scalp_factor_router import ScalpFactorRouter
    r = ScalpFactorRouter()
    md = {"price": 65000, "indicators": {"rsi": 25, "macd": 0.5, "ema_trend": 0.8},
          "volatility_value": 0.02}
    sig = r.evaluate("BTC", md)
    # RSI=25 超卖看多 + MACD正 + EMA正 → 应看多
    assert sig.direction == "long"
    assert sig.action in ("buy", "hold")  # hold 如果被 fee_guard 拦
    if sig.action == "buy":
        assert sig.entry_price == 65000
        assert sig.sl_pct > 0
        assert sig.tp_pct > sig.sl_pct  # 盈亏比 > 1


def test_composite_signal_priority():
    """有 composite_signal 时优先用它。"""
    from backend.services.scalp_factor_router import ScalpFactorRouter
    r = ScalpFactorRouter()
    md = {
        "price": 65000,
        "composite_signal": {"direction": 0.8, "strength": 0.9},
        "indicators": {"rsi": 50, "macd": 0, "ema_trend": 0},  # 这些应被忽略
    }
    sig = r.evaluate("BTC", md)
    assert sig.factor_score > 0
    assert sig.direction == "long"  # composite direction=0.8>0.1


def test_short_signal():
    """因子看空 → sell。"""
    from backend.services.scalp_factor_router import ScalpFactorRouter
    r = ScalpFactorRouter()
    md = {"price": 65000, "indicators": {"rsi": 80, "macd": -0.5, "ema_trend": -0.8},
          "volatility_value": 0.02}
    sig = r.evaluate("BTC", md)
    if sig.factor_score >= 50:
        assert sig.direction == "short"
        assert sig.action == "sell"


def test_no_data_hold():
    """无数据 → hold。"""
    from backend.services.scalp_factor_router import ScalpFactorRouter
    r = ScalpFactorRouter()
    sig = r.evaluate("BTC", {})
    assert sig.action == "hold"


def test_sl_tp_ratio():
    """SL/TP 基于 ATR，TP > SL（盈亏比 > 1）。"""
    from backend.services.scalp_factor_router import ScalpFactorRouter
    r = ScalpFactorRouter()
    sl, tp = r._compute_sl_tp({"volatility_value": 0.02})
    assert sl > 0
    assert tp > sl  # 盈亏比 > 1
    assert sl <= 0.05   # structure_stop 上限 5%
    assert tp <= 0.12   # 无价格兜底路径：tp = sl × 2.5（最高约 11.25%）


def test_fee_guard_rejects_tiny_tp():
    """TP 太小被 fee_guard 拦截 → hold。"""
    from backend.services.scalp_factor_router import ScalpFactorRouter
    r = ScalpFactorRouter()
    # 极低波动率 → TP 很小
    md = {"price": 65000, "indicators": {"rsi": 25, "macd": 0.5, "ema_trend": 0.8},
          "volatility_value": 0.001}  # 0.1% 波动
    sig = r.evaluate("BTC", md)
    # TP = 0.001*2.5 = 0.25%，可能被 fee_guard 拦（<0.5%）
    if sig.tp_pct < 0.005:
        assert sig.action == "hold"


# ── 清算磁吸独立开仓信号（2026-07-07）──
# 用户反馈：空仓时因子读数中性，高强度反向清算磁吸出现却完全捕捉不到，只能"等着"。
# 修复：因子方向 neutral 时，高强度磁吸单独作为信号源直接给出方向 + 保守基础分。

def _neutral_md(price=65000.0):
    """构造一个因子方向必然 neutral 的 market_data（RSI/MACD/EMA 全部中性）。"""
    return {"price": price, "indicators": {"rsi": 50, "macd": 0, "ema_trend": 0},
             "volatility_value": 0.02}


def test_liq_magnet_seeds_direction_when_factor_neutral(monkeypatch):
    """因子中性 + 高强度磁吸(long) → 方向被磁吸独立点燃为 long，且过探索门槛。"""
    from backend.services.scalp_factor_router import ScalpFactorRouter
    from backend.services import crypto_alpha_signals
    from backend.services.crypto_alpha_signals import AlphaReading

    seed = AlphaReading(
        direction="long", strength=0.85, severity="high",
        available=True, note="磁吸(severity=high,清算$5.0M,偏long)",
    )
    monkeypatch.setattr(
        crypto_alpha_signals.crypto_alpha, "liquidation_magnet",
        lambda symbol: seed,
    )
    # 固定动态门槛（正常按真实数据库最近胜率浮动 25~50，实盘后台正在跑单，
    # 这里跑单元测试不应受当下真实胜率影响）→ 用默认门槛，结果才是确定性的
    monkeypatch.setattr(
        ScalpFactorRouter, "_get_adaptive_threshold",
        lambda self, symbol: 25,
    )

    r = ScalpFactorRouter()
    sig = r.evaluate("BTC", _neutral_md())

    assert sig.direction == "long"
    assert sig.action == "buy"
    assert "liq_magnet_seed" in sig.factor_breakdown
    assert sig.action != "hold"


def test_liq_magnet_seed_ignored_when_low_severity(monkeypatch):
    """因子中性 + 低强度磁吸 → 不应被独立点燃（severity 不是 high）→ 仍 hold。"""
    from backend.services.scalp_factor_router import ScalpFactorRouter
    from backend.services import crypto_alpha_signals
    from backend.services.crypto_alpha_signals import AlphaReading

    seed = AlphaReading(
        direction="short", strength=0.3, severity="low",
        available=True, note="磁吸(severity=low)",
    )
    monkeypatch.setattr(
        crypto_alpha_signals.crypto_alpha, "liquidation_magnet",
        lambda symbol: seed,
    )

    r = ScalpFactorRouter()
    sig = r.evaluate("BTC", _neutral_md())

    assert sig.action == "hold"
    assert "liq_magnet_seed" not in (sig.factor_breakdown or {})


def test_liq_magnet_seed_skipped_when_factor_already_directional(monkeypatch):
    """因子已经有明确方向时（非 neutral）→ 不走独立点燃分支，走原有共振加分逻辑。"""
    from backend.services.scalp_factor_router import ScalpFactorRouter
    from backend.services import crypto_alpha_signals
    from backend.services.crypto_alpha_signals import AlphaReading

    seed = AlphaReading(
        direction="long", strength=0.85, severity="high",
        available=True, note="磁吸(severity=high)",
    )
    monkeypatch.setattr(
        crypto_alpha_signals.crypto_alpha, "liquidation_magnet",
        lambda symbol: seed,
    )

    r = ScalpFactorRouter()
    # RSI=25超卖看多 + MACD正 + EMA正 → 因子本身已给出 long 方向，非 neutral
    md = {"price": 65000, "indicators": {"rsi": 25, "macd": 0.5, "ema_trend": 0.8},
          "volatility_value": 0.02}
    sig = r.evaluate("BTC", md)

    assert sig.direction == "long"
    # 独立点燃分支（1.45）在 direction 非 neutral 时应跳过，不会往 breakdown 里写
    # liq_magnet_seed；磁吸信号仍会通过原有 1.5 共振加分逻辑生效（crypto_alpha 字段）。
    assert "liq_magnet_seed" not in (sig.factor_breakdown or {})
