# -*- coding: utf-8 -*-
"""震荡行情策略加固契约测试（2026-09-09）。

覆盖本轮修复：
1. scalp_ranging_mr：RR 自洽抬升双重上限（对沿 + SL上限×MIN_RR），
   上限内达不到 MIN_RR → 显式 hold（不把 RR 倒挂信号丢给下游）。
2. scalp_loop：方向上下文缺失不接 MR；MR hold 不占用 ranging_mr 标记（_mr_owned 分流）。
3. scalp_execution_gate：range_position_5m 的假零(0.0)不再被 `or -1` 吞成未知。
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))


def _mr_market_data(price, swing_low, swing_high):
    """构造 48×5m K 线：尾部两根顶到 swing_high、首部两根压到 swing_low。"""
    n = 48
    closes = np.linspace(swing_low + 0.2, price, n)
    rows = []
    for i in range(n):
        c = float(closes[i])
        rows.append({"open": c, "high": c * 1.002, "low": c * 0.998, "close": c})
    df = pd.DataFrame(rows)
    df.loc[df.index[-1], "high"] = swing_high
    df.loc[df.index[-2], "high"] = swing_high
    df.loc[df.index[0], "low"] = swing_low
    df.loc[df.index[1], "low"] = swing_low
    return {"price": price, "mark_price": price, "klines": df}


def _patch_mr_consts(monkeypatch, min_rr):
    """隔离配置：振幅带/RSI 线/TP 比例用确定性值，MIN_RR 单独注入。"""
    from backend.services.scalp import scalp_ranging_mr as mr
    monkeypatch.setattr(mr, "SCALP_MR_HIGH_BAND", 0.70)
    monkeypatch.setattr(mr, "SCALP_MR_LOW_BAND", 0.30)
    monkeypatch.setattr(mr, "SCALP_MR_RSI_OB", 60.0)
    monkeypatch.setattr(mr, "SCALP_MR_RSI_OS", 40.0)
    monkeypatch.setattr(mr, "SCALP_MR_MIN_RANGE_PCT", 0.015)
    monkeypatch.setattr(mr, "SCALP_MR_MAX_RANGE_PCT", 0.05)
    monkeypatch.setattr(mr, "SCALP_MR_TP_RANGE_FRAC", 0.55)
    monkeypatch.setattr(mr, "SCALP_MR_MIN_TP", 0.006)
    monkeypatch.setattr(mr, "SCALP_MR_MIN_RR", min_rr)
    monkeypatch.setattr(mr, "apply_learned_mr", lambda tp, sl: (tp, sl), raising=False)


def _mock_rsi(monkeypatch, value):
    from backend.services.factor_engine.base_factors import factor_engine
    monkeypatch.setattr(factor_engine, "compute_rsi", lambda df: value)


class TestMrRrHardening:
    """RR 自洽：抬升受对沿与 SL上限×MIN_RR 双重约束；不可达即 hold。"""

    def test_rr_infeasible_returns_hold(self, monkeypatch):
        """窄区间低位做多，结构装不下 RR≥1.0 的止盈 → 显式 hold（不丢给下游）。"""
        from backend.services.scalp import scalp_ranging_mr as mr
        _patch_mr_consts(monkeypatch, min_rr=1.0)
        _mock_rsi(monkeypatch, 30.0)
        # amp = 1.6/99.15 ≈ 1.614%（在 [1.5%,5%] 带内）；pos ≈ 0.28 → long
        md = _mr_market_data(price=99.15, swing_low=98.7, swing_high=100.3)
        sig = mr.evaluate_ranging_mr("TEST", md)
        assert sig.action == "hold", f"RR 不可达应 hold: {sig.action} {sig.reasoning}"
        assert "RR不可达" in (sig.reasoning or "")

    def test_rr_env_075_still_fires_in_same_market(self, monkeypatch):
        """同场景 MIN_RR=0.75（生产 env 值）→ 抬升后 RR≥0.75，正常开火。"""
        from backend.services.scalp import scalp_ranging_mr as mr
        _patch_mr_consts(monkeypatch, min_rr=0.75)
        _mock_rsi(monkeypatch, 30.0)
        md = _mr_market_data(price=99.15, swing_low=98.7, swing_high=100.3)
        sig = mr.evaluate_ranging_mr("TEST", md)
        assert sig.action == "buy", f"应开火: {sig.action} {sig.reasoning}"
        assert sig.tp_pct / sig.sl_pct >= 0.75 - 1e-6
        # 抬升后的 TP 不得超过对沿（结构可达性）
        far_edge_pct = (100.3 - 99.15) / 99.15
        assert sig.tp_pct <= far_edge_pct + 1e-9, (
            f"TP {sig.tp_pct:.4%} 超过对沿 {far_edge_pct:.4%}"
        )

    def test_uplift_respects_sl_cap_times_min_rr(self, monkeypatch):
        """宽区间高位做空（8/23 契约场景）：TP 抬升不超过 SL上限×MIN_RR。"""
        from backend.services.scalp import scalp_ranging_mr as mr
        _patch_mr_consts(monkeypatch, min_rr=1.0)
        _mock_rsi(monkeypatch, 85.0)
        md = _mr_market_data(price=104.0, swing_low=95.0, swing_high=105.0)
        monkeypatch.setattr(mr, "SCALP_MR_MAX_RANGE_PCT", 0.20)
        sig = mr.evaluate_ranging_mr("TEST", md)
        assert sig.action == "sell"
        # SL 已封顶 1.6% → TP 抬升上限 = 1.6% × 1.0
        assert sig.tp_pct <= mr._MR_SL_CAP * 1.0 + 1e-9
        assert sig.tp_pct / sig.sl_pct >= 1.0 - 1e-6


class TestScalpLoopMrRoutingContract:
    """scalp_loop 源码契约：方向上下文闸 + hold 不占 MR 标记。"""

    def test_direction_context_gate_present(self):
        src = _src_loop()
        assert "方向上下文缺失" in src
        assert "_dir_ctx_ok" in src
        assert "防无背景接飞刀" in src

    def test_mr_owned_flow_preserves_hold_ownership(self):
        src = _src_loop()
        # MR 接管（_mr_owned）与 MR 开火（_mr_active）分开；hold 不占用 MR 标记
        assert "_mr_owned" in src
        assert "接管但未达边缘(hold)，不占用 MR 标记" in src
        assert 'str(getattr(_sig, "action", "") or "") in ("buy", "sell")' in src
        # 只有既未接管又未开火才回落趋势打法
        assert "if not _mr_active and not _mr_owned:" in src


class TestScalpGateFalsyRangePosition:
    """range_position_5m=0.0 不再被 `or -1` 吞成未知值。"""

    def test_explicit_none_check_in_source(self):
        src = open(
            os.path.join(os.path.dirname(__file__), "..", "..", "services", "scalp", "scalp_execution_gate.py"),
            encoding="utf-8",
        ).read()
        assert 'getattr(advisory, "range_position_5m", None) or -1' not in src
        assert '_rp = getattr(advisory, "range_position_5m", None)' in src
        assert "_mr_pos = float(_rp) if _rp is not None else None" in src


def _src_loop():
    return open(
        os.path.join(os.path.dirname(__file__), "..", "..", "services", "full_auto", "loops", "scalp_loop.py"),
        encoding="utf-8",
    ).read()


class TestKlineTrendVeto:
    """P8：K 线重算 1h/24h 涨跌，趋势/极端日否决 MR（防陈旧 regime 标签接飞刀）。"""

    def _trend_df(self, n=300, chg=0.06):
        closes = np.linspace(100.0, 100.0 * (1 + chg), n)
        rows = []
        for c in closes:
            rows.append({"open": c, "high": c * 1.001, "low": c * 0.999, "close": c})
        return pd.DataFrame(rows)

    def test_trend_day_vetoed(self):
        from backend.services.scalp import scalp_ranging_mr as mr
        md = {"price": 106.0, "klines": self._trend_df()}
        sig = mr.evaluate_ranging_mr("TEST", md)
        assert sig.action == "hold", f"趋势日应 hold: {sig.reasoning}"
        assert "K线趋势否决" in (sig.reasoning or "")

    def test_veto_fail_open_on_short_klines(self):
        from backend.services.scalp import scalp_ranging_mr as mr
        md = {"price": 100.0, "klines": _mr_market_data(100.0, 99.0, 101.0)["klines"]}
        sig = mr.evaluate_ranging_mr("TEST", md)
        assert "K线趋势否决" not in (sig.reasoning or ""), "48 根 K 线应 fail-open"

    def test_veto_disabled_rollback(self, monkeypatch):
        import backend.config.settings as settings_mod
        from backend.services.scalp import scalp_ranging_mr as mr
        monkeypatch.setattr(settings_mod, "SCALP_MR_TREND_VETO_ENABLED", False)
        md = {"price": 106.0, "klines": self._trend_df()}
        sig = mr.evaluate_ranging_mr("TEST", md)
        assert "K线趋势否决" not in (sig.reasoning or ""), "开关关闭应回滚"


class TestCalibratorQualityGate:
    """P7：score-win 相关性为噪声时拒绝拟合校准曲线（回退基础胜率）。"""

    def _patch(self, monkeypatch, pairs):
        import backend.config.settings as settings_mod
        from backend.services.scalp import scalp_confidence_calibrator as cal
        monkeypatch.setattr(settings_mod, "SCALP_CALIBRATOR_MIN_SAMPLES", 10)
        monkeypatch.setattr(settings_mod, "SCALP_CALIBRATOR_MIN_CORR", 0.05)
        monkeypatch.setattr(
            cal.scalp_confidence_calibrator, "_load_samples", lambda days, st: pairs,
        )
        return cal

    def test_noise_curve_rejected(self, monkeypatch):
        """分数升序 + 输赢交替 → corr≈0 → 拒绝校准。"""
        n = 200
        pairs = [(30.0 + i * 0.25, (i % 2 == 0)) for i in range(n)]
        cal = self._patch(monkeypatch, pairs)
        model = cal.scalp_confidence_calibrator._fit_model("ranging_mr")
        assert model.is_calibrated is False

    def test_monotone_curve_accepted(self, monkeypatch):
        """分数与输赢强正相关 → 允许校准。"""
        n = 200
        pairs = [(30.0 + i * 0.25, (30.0 + i * 0.25) > 65.0) for i in range(n)]
        cal = self._patch(monkeypatch, pairs)
        model = cal.scalp_confidence_calibrator._fit_model("ranging_mr")
        assert model.is_calibrated is True
        assert model.n_samples == n
