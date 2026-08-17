"""M1 修复回归测试（2026-08 审计 M1 落地）。

覆盖：
- P0-5  回放前视（funding/FGI 只取 ≤ts）、时序 PBO、closed_only 语义
- P0-1  PBO 时序 CSCV（indeterminate / 值排序废除）
- P0-2  衰减监控惩罚（双确认归零）
- P0-4  快照去重（唯一键放行 + strategy_id 入签名）
- P0-3  Gate2 真 Sharpe/真回撤判定

全部为纯单元测试（无 DB/网络）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pytest


# ────────────────────────── P0-5 回放前视 ──────────────────────────

@pytest.mark.unit
def test_funding_rate_backward_only_no_future():
    from backend.services.live_pipeline_backtest_engine import LivePipelineBacktestEngine

    # ts=1000：只允许 ≤1000 的样本；未来样本 1200 不可用
    rates = {800: 0.001, 1200: -0.005}
    assert LivePipelineBacktestEngine._get_funding_rate(1000, rates) == 0.001
    # 全部在未来 → 0.0
    assert LivePipelineBacktestEngine._get_funding_rate(500, rates) == 0.0


@pytest.mark.unit
def test_fgi_backward_only_no_future():
    from backend.services.live_pipeline_backtest_engine import LivePipelineBacktestEngine

    fgi = {100: 20.0, 900: 80.0}
    assert LivePipelineBacktestEngine._get_fgi(1000, fgi) == 80.0
    assert LivePipelineBacktestEngine._get_fgi(50, fgi) == 50.0  # 全部未来 → 默认


# ────────────────────────── P0-1 PBO 时序 CSCV ──────────────────────────

@pytest.mark.unit
def test_pbo_short_series_indeterminate():
    from backend.services.factor_engine.dsr_pbo import compute_pbo_simple

    # 旧调用方传 3 个跨币标量 → 样本不足，必须 indeterminate（fail-closed 依据）
    r = compute_pbo_simple([0.1, 0.2, 0.3])
    assert r["indeterminate"] is True
    assert r["significant"] is False


@pytest.mark.unit
def test_pbo_temporal_detects_direction_flip():
    from backend.services.factor_engine.dsr_pbo import compute_pbo_simple

    # 前半段正 IC、后半段负 IC：时序过拟合。用 n_splits=4 使 IS/OOS 组合
    # 不落入对称混合组合（is_mean==0 被跳过）的刀锋情形 → PBO=1.0 显著过拟合。
    # （n_splits=8 时 C(8,4) 中 k=2 的对称混合组合占比 51%，稀释到 ~0.49，
    #   是二元对称构造的病理情形，非实现缺陷。）
    series = [0.05] * 40 + [-0.05] * 40
    r = compute_pbo_simple(series, n_splits=4)
    assert r["indeterminate"] is False
    assert r["pbo"] == 1.0
    assert r["significant"] is False

    # 全程稳定正 IC → PBO 低、通过
    series2 = [0.05] * 80
    r2 = compute_pbo_simple(series2, n_splits=8)
    assert r2["indeterminate"] is False
    assert r2["pbo"] < 0.5
    assert r2["significant"] is True


@pytest.mark.unit
def test_rolling_ic_series_shape_and_nan_tail():
    from backend.services.factor_engine.factor_backtest_scorer import factor_backtest_scorer

    n = 120
    closes = np.linspace(100, 130, n)
    vals = np.arange(n, dtype=float)  # 与收益正相关
    ics = factor_backtest_scorer._rolling_ic_series(vals, closes, fwd=5, window=30)
    assert len(ics) == n
    assert np.isfinite(ics).sum() >= 60
    # 头部 window-1 根窗口不足 → NaN；尾部滚动窗内仍含有效前向收益 → 有限值
    assert np.isnan(ics[0])
    assert np.isfinite(ics[-1])


# ────────────────────────── P0-2 衰减惩罚 ──────────────────────────

@pytest.mark.unit
def test_decay_penalty_double_confirm_retire():
    from backend.services.factor_engine.factor_decay_monitor import decay_monitor, DecayStatus

    # recent 与 historical 都低于退役阈值 → 归零
    decay_monitor._decay_status["f1"] = DecayStatus(
        factor_id="f1", current_ic=0.001, historical_ic=0.005,
        decay_rate=-0.1, half_life_days=7, trend="dead", recommendation="retire",
    )
    assert decay_monitor.get_factor_weight_penalty("f1") == 0.0
    # recent 低但 historical 正常 → 只给 0.3 下限（防单次误评归零）
    decay_monitor._decay_status["f2"] = DecayStatus(
        factor_id="f2", current_ic=0.001, historical_ic=0.05,
        decay_rate=-0.1, half_life_days=7, trend="declining", recommendation="retire",
    )
    assert decay_monitor.get_factor_weight_penalty("f2") == 0.3
    # 未评估因子 → 1.0
    assert decay_monitor.get_factor_weight_penalty("f_none") == 1.0


# ────────────────────────── P0-4 快照去重 ──────────────────────────

@pytest.mark.unit
def test_dedup_unique_key_bypasses_window():
    from backend.services import decision_snapshot_writer as dsw

    class Snap:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    # 唯一键存在 → 不去重（即使同签名窗口内）
    s1 = Snap(account_id=1, strategy_id="sA", symbol="BTC", tier="short",
              action="buy", confidence=0.5, ai_reasoning="same",
              proposal_id="p1", trace_id=None)
    assert dsw._dedup_check(s1) is True
    # 无唯一键：签名含 strategy_id，不同策略不同签名 → 都放行
    s2 = Snap(account_id=1, strategy_id="sA", symbol="BTC", tier="short",
              action="buy", confidence=0.5, ai_reasoning="same",
              proposal_id=None, trace_id=None)
    s3 = Snap(account_id=1, strategy_id="sB", symbol="BTC", tier="short",
              action="buy", confidence=0.5, ai_reasoning="same",
              proposal_id=None, trace_id=None)
    assert dsw._dedup_check(s2) is True
    assert dsw._dedup_check(s3) is True
    # 同签名窗口内重复 → 丢弃
    s4 = Snap(account_id=1, strategy_id="sA", symbol="BTC", tier="short",
              action="buy", confidence=0.5, ai_reasoning="same",
              proposal_id=None, trace_id=None)
    assert dsw._dedup_check(s4) is False


# ────────────────────────── P0-3 Gate2 真指标 ──────────────────────────

@pytest.mark.unit
def test_gate2_uses_real_sharpe_and_equity_dd():
    from backend.services.strategy_validator import strategy_validator, PaperTradingMetrics

    # 伪 Sharpe 很高（旧口径能过）但真 Sharpe 低 → 必须拦
    m = PaperTradingMetrics(
        days_running=14, total_trades=30,
        sharpe_ratio=0.9,          # 旧伪 Sharpe（不再参与判定）
        max_drawdown_pct=8.0,      # 单笔最大亏损 8%（辅助门槛内）
        real_sharpe=0.3,           # 真 Sharpe 不足 1.0 → fail
        equity_dd_pct=12.0,        # 回撤超 10% → fail
        total_return_pct=5.0, backtest_return_pct=5.0,
    )
    r = strategy_validator.validate_gate2(m)
    assert r.passed is False
    assert any("Sharpe" in c for c in r.failed_checks)
    assert any("回撤" in c for c in r.failed_checks)

    # 达标样例 → pass
    m2 = PaperTradingMetrics(
        days_running=14, total_trades=30,
        sharpe_ratio=0.0,
        max_drawdown_pct=8.0,
        real_sharpe=1.2,
        equity_dd_pct=6.0,
        total_return_pct=8.0, backtest_return_pct=7.0,
    )
    r2 = strategy_validator.validate_gate2(m2)
    assert r2.passed is True


@pytest.mark.unit
def test_gate2_single_trade_loss_aux_gate():
    from backend.services.strategy_validator import strategy_validator, PaperTradingMetrics

    m = PaperTradingMetrics(
        days_running=14, total_trades=30,
        sharpe_ratio=0.0, max_drawdown_pct=22.0,  # 单笔亏损 22% → 辅助门槛拦
        real_sharpe=1.5, equity_dd_pct=5.0,
        total_return_pct=8.0, backtest_return_pct=7.0,
    )
    r = strategy_validator.validate_gate2(m)
    assert r.passed is False
    assert any("单笔" in c for c in r.failed_checks)