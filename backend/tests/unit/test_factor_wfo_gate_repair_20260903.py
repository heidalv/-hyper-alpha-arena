# -*- coding: utf-8 -*-
"""WFO 门禁修复（2026-09-03 审查修正 B）。

审查发现 WFO 门禁在生产被关（08-27），复核后根因不是"数据不够"，而是四个叠加缺陷：
1. 策略级 WFO 读 WalkForwardResult 不存在的字段（consistency/n_periods → 恒 0）
   → 门禁从写出来那天起不可能通过，08-27 把症状当成数据不足关掉了整个门；
2. IC-WFO 窗口只有 4h 口径（60/15/7 天），5m/15m 数据永远凑不出一个窗；
3. 每窗单独求值 → 滚动因子预热期吃掉大半测试窗（4h 90 根剩 41 根）；
4. 短测试窗 + 重叠窗 → 噪声上也能得到 |IC|≈0.2、p≈0（小样本负偏 + 伪独立）。

本用例锁定每一条的修复，以及"噪声不放行、真信号放行"的端到端行为。
"""
import numpy as np
import pandas as pd
import pytest

from backend.services.evolution import factor_wfo as fw


def _klines(rows, seed, trend=0.0, freq="1d", ar=0.0):
    """ar>0 → 收益 AR(1) 自相关（动量因子才有真信号；常数漂移与 mean(returns) 零相关）。"""
    rng = np.random.default_rng(seed)
    eps = rng.normal(0.0, 0.01, rows)
    rets = np.empty(rows)
    rets[0] = trend + eps[0]
    for i in range(1, rows):
        rets[i] = trend + ar * (rets[i - 1] - trend) + eps[i]
    closes = 100.0 * np.cumprod(1.0 + rets)
    o = closes * (1 + rng.normal(0, 0.002, rows))
    return pd.DataFrame({
        "open": o,
        "high": np.maximum(o, closes) * 1.001,
        "low": np.minimum(o, closes) * 0.999,
        "close": closes,
        "volume": rng.uniform(1e5, 1e6, rows),
    }, index=pd.date_range("2026-01-01", periods=rows, freq=freq))


def _mom_expr(n=5):
    from backend.services.factor_engine.expr.parser import parse
    return parse({"op": "mean", "args": [{"f": "returns"}, {"c": n}]})


class TestReportFieldMismatch:
    """缺陷 1：字段名对不上 → 门永远关。"""

    def test_reads_consistency_score_and_periods(self):
        class _P:  # 模拟 WalkForwardPeriod
            def __init__(self, ok):
                self.test_result = object() if ok else None

        class _R:  # 模拟 WalkForwardResult（真实字段名）
            consistency_score = 0.83
            periods = [_P(True), _P(True), _P(False), _P(True)]

        c, n = fw._report_consistency_and_periods(_R())
        assert c == pytest.approx(0.83)
        assert n == 3, "只数有 test_result 的有效期"

    def test_legacy_names_still_accepted(self):
        class _R:
            consistency = 0.7
            n_periods = 5

        assert fw._report_consistency_and_periods(_R()) == (0.7, 5)

    def test_empty_report_is_zero_not_crash(self):
        assert fw._report_consistency_and_periods(object()) == (0.0, 0)


class TestIcWfoWindows:
    """缺陷 2/4：窗口分档、最小 OOS 点数、不重叠。"""

    def test_5m_windows_fit_50_days(self, monkeypatch):
        for k in ("WFO_IC_TRAIN_DAYS", "WFO_IC_TEST_DAYS", "WFO_IC_STEP_DAYS"):
            monkeypatch.delenv(k, raising=False)
        tr, te, st = fw._wfo_ic_windows("5min", total_bars=14085, bpd=288)
        assert tr == 20 * 288 and te == 5 * 288 and st == te
        # 50 天数据至少能出 3 个窗
        assert (14085 - tr - te) // st + 1 >= 3

    def test_low_freq_test_window_raised_to_min_points(self, monkeypatch):
        for k in ("WFO_IC_TRAIN_DAYS", "WFO_IC_TEST_DAYS", "WFO_IC_STEP_DAYS"):
            monkeypatch.delenv(k, raising=False)
        tr, te, st = fw._wfo_ic_windows("1d", total_bars=400, bpd=1)
        assert te >= fw._WFO_IC_MIN_TEST_BARS, "日线 15 天只有 15 个点，必须抬到最小点数"
        assert st == te, "测试窗不重叠"
        assert tr >= 2 * te

    def test_shrinks_when_data_short_but_keeps_min_test(self, monkeypatch):
        for k in ("WFO_IC_TRAIN_DAYS", "WFO_IC_TEST_DAYS", "WFO_IC_STEP_DAYS"):
            monkeypatch.delenv(k, raising=False)
        # 4h 只有 100 天数据：60/15 出 3 窗需要 90 天 ok；给 60 天则要收缩
        tr, te, st = fw._wfo_ic_windows("4h", total_bars=60 * 6, bpd=6)
        assert te >= fw._WFO_IC_MIN_TEST_BARS
        assert st == te
        assert tr + te + st * (fw._WFO_IC_MIN_WINDOWS - 1) <= 60 * 6 + te  # 大致装得下

    def test_env_override_wins(self, monkeypatch):
        monkeypatch.setenv("WFO_IC_TRAIN_DAYS", "10")
        monkeypatch.setenv("WFO_IC_TEST_DAYS", "3")
        monkeypatch.setenv("WFO_IC_STEP_DAYS", "3")
        monkeypatch.setattr(fw, "_WFO_IC_TRAIN_DAYS", 10)
        monkeypatch.setattr(fw, "_WFO_IC_TEST_DAYS", 3)
        monkeypatch.setattr(fw, "_WFO_IC_STEP_DAYS", 3)
        tr, te, st = fw._wfo_ic_windows("5min", total_bars=14085, bpd=288)
        assert (tr, te, st) == (10 * 288, 3 * 288, 3 * 288)


class TestIcWfoStatistics:
    """缺陷 3/4 端到端：噪声不放行、趋势放行、反转因子按定向放行。"""

    def test_noise_not_admitted_across_seeds(self):
        expr = _mom_expr(5)
        fp = 0
        for seed in (3, 5, 11, 17, 23, 31, 41, 47):
            r = fw.run_factor_wfo_ic(expr, _klines(400, seed), "noise", freq="1d")
            fp += int(bool(r.get("passed")))
        assert fp <= 1, f"i.i.d. 噪声上放行 {fp}/8（修复前 15 点窗下 5/8 放行）"

    def test_trend_admitted(self):
        # 1200 根日线 → 60 根不重叠测试窗约 17 个；AR(1)=0.5 的收益自相关是真动量信号
        expr = _mom_expr(5)
        r = fw.run_factor_wfo_ic(expr, _klines(1200, 7, ar=0.5), "trend", freq="1d")
        assert r["passed"] is True, r
        assert r["orientation"] == 1

    def test_negative_ic_factor_admitted_by_orientation(self):
        """线上按 expected_sign 反向使用的负 IC 因子，不该被"原始 OOS IC ≥ 0.01"拒掉。"""
        from backend.services.factor_engine.expr.parser import parse
        neg = parse({"op": "mul", "args": [{"c": -1}, {"op": "mean", "args": [{"f": "returns"}, {"c": 5}]}]})
        r = fw.run_factor_wfo_ic(neg, _klines(1200, 7, ar=0.5), "neg", freq="1d")
        assert r["passed"] is True, r
        assert r["orientation"] == -1
        assert r["oos_ic_mean"] > 0, "定向后的 OOS IC 为正"

    def test_full_series_evaluation_has_no_warmup_loss(self):
        """全序列求值后再切片：测试窗首根不再是 NaN 预热。"""
        expr = _mom_expr(50)
        df = _klines(600, 9, ar=0.3, freq="4h")
        r = fw.run_factor_wfo_ic(expr, df, "warm", freq="4h")
        assert r["skipped"] is False
        # 逐窗单独求值时 50 根预热会让 90 根测试窗只剩 40 点，IC 噪声 ~0.16；
        # 全序列求值后 OOS IC 序列的离散度应明显更小
        assert float(r["oos_ic_std"]) < 0.16, r


class TestStrategyWfoAdvisory:
    """策略级 WFO 默认只作参考，IC-WFO 才是约束门。"""

    def test_default_not_binding(self, monkeypatch):
        monkeypatch.delenv("FACTOR_EVO_WFO_STRATEGY_GATE", raising=False)
        assert fw._strategy_gate_binding() is False
        monkeypatch.setenv("FACTOR_EVO_WFO_STRATEGY_GATE", "1")
        assert fw._strategy_gate_binding() is True

    def test_insufficient_data_does_not_block_when_advisory(self, monkeypatch):
        monkeypatch.delenv("FACTOR_EVO_WFO_STRATEGY_GATE", raising=False)
        monkeypatch.setattr(fw, "FEATURE_WFO_GATE_ENABLED", True)
        r = fw.run_factor_wfo(_mom_expr(), _klines(100, 1), "f", freq="1d")
        assert r["skipped"] is True and r["passed"] is True and r["binding"] is False

    def test_insufficient_data_blocks_when_binding(self, monkeypatch):
        monkeypatch.setenv("FACTOR_EVO_WFO_STRATEGY_GATE", "1")
        monkeypatch.setattr(fw, "FEATURE_WFO_GATE_ENABLED", True)
        monkeypatch.setattr(fw, "_WFO_FAIL_CLOSED", True)
        r = fw.run_factor_wfo(_mom_expr(), _klines(100, 1), "f", freq="1d")
        assert r["skipped"] is True and r["passed"] is False


class TestMultiSymbolRuleDefault:
    """进化主链：多币 WFO 默认 2/3 通过即可（池化口径），不再一票否决。"""

    def test_default_is_ratio_mode(self):
        import inspect
        from backend.services.evolution import factor_evolution_loop as fel
        src = inspect.getsource(fel._run_evolution_loop_impl)
        assert 'getenv("FACTOR_EVO_WFO_REQUIRE_ALL") or "0"' in src
        assert 'FACTOR_EVO_WFO_MIN_SYMBOL_RATIO", "0.66"' in src
