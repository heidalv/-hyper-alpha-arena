"""
test_factor_wfo_ic — v6 阶段 2（S2-5）滚动训练窗 OOS IC 序列 WFO 单元测试

覆盖:
1. _freq_to_bars_per_day 周期解析
2. 趋势数据 → OOS IC 均值正 → 通过
3. 噪声数据 → 判据不满足 → 拒绝（passed=False）
4. 数据不足/窗口不足 → fail-open skipped
5. 序列输出结构（oos_ic_series 与 n_windows 对齐）
6. factor_evolution_loop 接入：IC-WFO 拒绝拦截晋升 + 日志 action=wfo_ic_reject
"""
import numpy as np
import pandas as pd
from unittest.mock import patch

# ════════════════════════════════════════════════════════
#  Helpers
# ════════════════════════════════════════════════════════

def _make_klines(rows: int = 300, seed: int = 42, trend: float = 0.02) -> pd.DataFrame:
    """正弦调制正收益 K线：动量因子 OOS IC 稳定为正。"""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-01-01", periods=rows, freq="1d")
    phase = np.arange(rows) / 12.0
    rets = np.maximum(trend * 0.5 + 0.01 * np.sin(phase), 0.002)
    closes = 100.0 * np.cumprod(1.0 + rets)
    o = closes * (1 + rng.normal(0, 0.002, rows))
    return pd.DataFrame({
        "open": o,
        "high": np.maximum(o, closes) * (1 + np.abs(rng.normal(0, 0.001, rows))),
        "low": np.minimum(o, closes) * (1 - np.abs(rng.normal(0, 0.001, rows))),
        "close": closes,
        "volume": rng.uniform(1e5, 1e6, rows),
    }, index=idx)


def _make_noise_klines(rows: int = 300, seed: int = 99) -> pd.DataFrame:
    """零漂移噪声 K线：无信号。"""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-01-01", periods=rows, freq="1d")
    rets = rng.normal(0, 0.01, rows)
    closes = 100.0 * np.cumprod(1.0 + rets)
    o = closes * (1 + rng.normal(0, 0.002, rows))
    return pd.DataFrame({
        "open": o,
        "high": np.maximum(o, closes) * (1 + np.abs(rng.normal(0, 0.001, rows))),
        "low": np.minimum(o, closes) * (1 - np.abs(rng.normal(0, 0.001, rows))),
        "close": closes,
        "volume": rng.uniform(1e5, 1e6, rows),
    }, index=idx)


def _make_momentum_expr():
    from backend.services.factor_engine.expr.parser import parse
    return parse({"op": "mean", "args": [{"f": "returns"}, {"c": 5}]})


# ════════════════════════════════════════════════════════
#  1. 周期解析
# ════════════════════════════════════════════════════════

class TestFreqParsing:
    def test_known_freqs(self):
        from backend.services.evolution.factor_wfo import _freq_to_bars_per_day
        assert _freq_to_bars_per_day("4h") == 6
        assert _freq_to_bars_per_day("1h") == 24
        assert _freq_to_bars_per_day("5min") == 288
        assert _freq_to_bars_per_day("15min") == 96
        assert _freq_to_bars_per_day("1d") == 1

    def test_unknown_freq(self):
        from backend.services.evolution.factor_wfo import _freq_to_bars_per_day
        assert _freq_to_bars_per_day("quarterly") is None


# ════════════════════════════════════════════════════════
#  2. IC-WFO 语义
# ════════════════════════════════════════════════════════

class TestRunFactorWfoIc:
    def test_trend_passes(self):
        """趋势数据：OOS IC 均值正 + 显著 → passed。"""
        from backend.services.evolution.factor_wfo import run_factor_wfo_ic
        expr = _make_momentum_expr()
        df = _make_klines(rows=400, seed=7)
        res = run_factor_wfo_ic(expr, df, "test_f1", freq="1d")
        assert res["passed"] is True, res
        assert res["skipped"] is False
        assert res["oos_ic_mean"] > 0

    def test_noise_rejected(self):
        """噪声数据：OOS IC 不显著为正 → 拒绝。"""
        from backend.services.evolution.factor_wfo import run_factor_wfo_ic
        expr = _make_momentum_expr()
        df = _make_noise_klines(rows=400, seed=3)
        res = run_factor_wfo_ic(expr, df, "test_f2", freq="1d")
        assert res["skipped"] is False
        assert res["passed"] is False

    def test_insufficient_data_fail_closed(self):
        """数据不足 → fail-closed skipped（对齐 FACTOR_EVO_GATE_FAIL_CLOSED 默认 1）。"""
        from backend.services.evolution.factor_wfo import run_factor_wfo_ic
        expr = _make_momentum_expr()
        df = _make_klines(rows=100, seed=8)
        res = run_factor_wfo_ic(expr, df, "test_f3", freq="1d")
        assert res["passed"] is False
        assert res["skipped"] is True
        assert res.get("reason") == "insufficient_data"

    def test_unknown_freq_fail_closed(self):
        from backend.services.evolution.factor_wfo import run_factor_wfo_ic
        expr = _make_momentum_expr()
        df = _make_klines(rows=400, seed=9)
        res = run_factor_wfo_ic(expr, df, "test_f4", freq="quarterly")
        assert res["passed"] is False and res["skipped"] is True
        assert "unknown_freq" in res.get("reason", "")

    def test_series_structure(self):
        """OOS IC 序列输出：长度 = n_windows，逐窗口有 train/oos IC。"""
        from backend.services.evolution.factor_wfo import run_factor_wfo_ic
        expr = _make_momentum_expr()
        df = _make_klines(rows=500, seed=11)
        res = run_factor_wfo_ic(expr, df, "test_f5", freq="1d")
        assert res["skipped"] is False
        assert len(res["oos_ic_series"]) == res["n_windows"] >= 3
        assert len(res["train_ic_series"]) == res["n_windows"]
        assert 0.0 <= res["decay_rate"] <= 1.0 or res["decay_rate"] < 0.0  # clip 到 [-1,1]
        assert -1.0 <= res["decay_rate"] <= 1.0
        assert res["oos_ic_p"] is not None

    def test_exception_fail_closed(self):
        """表达式求值异常 → fail-closed skipped（不炸、不放行）。"""
        from backend.services.evolution.factor_wfo import run_factor_wfo_ic
        df = _make_klines(rows=400, seed=12)

        class BoomExpr:
            expr_id = "boom"
            def evaluate(self, fields):
                raise RuntimeError("boom")
        res = run_factor_wfo_ic(BoomExpr(), df, "test_f6", freq="1d")
        # 单窗求值异常被吞并跳过 → 无有效窗口 → fail-closed（不炸、不放行）
        assert res["passed"] is False
        assert res["skipped"] is True
        assert str(res.get("reason", "")).startswith("insufficient_windows")


# ════════════════════════════════════════════════════════
#  3. factor_evolution_loop 接入
# ════════════════════════════════════════════════════════

class TestEvolutionLoopIcWfo:
    def _run_loop(self, ic_wfo_result, wfo_gate: str = "true"):
        """跑一遍进化主链。

        [2026-09-02 E14 契约同步] 必须显式设置 FEATURE_WFO_GATE_ENABLED。
        F36b（09-02）让进化主链真正读取该开关，而仓库 .env 里它是 false
        （运维决定：WFO 多币验证误杀，交由 PAPER 影子期 OOS 兜底）。测试进程会
        加载 .env，于是本类原先实际测到的是「开关关闭时不拦截」——与用例名声称的
        「拒绝→拦截」正好相反，属于漏设前提而非代码放松。
        """
        import os as _os
        from unittest.mock import patch as _patch

        from backend.services.evolution import factor_evolution_loop as fel
        from backend.services.factor_engine.expr.parser import parse

        ast = {"op": "mean", "args": [{"f": "returns"}, {"c": 5}]}
        expr = parse(ast)
        dfs = {"BTC": _make_klines(rows=500, seed=21)}
        _env_patch = _patch.dict(
            _os.environ, {"FEATURE_WFO_GATE_ENABLED": wfo_gate},
        )

        logged = []

        def fake_log(factor_id, phase, **kwargs):
            logged.append((factor_id, phase, kwargs))

        with _env_patch, \
             patch.object(fel, "_load_data", return_value=dfs), \
             patch.object(fel, "_ensure_governance_columns"), \
             patch.object(fel, "_mine_candidates",
                          return_value=[(expr, "mom5")]), \
             patch.object(fel, "_evaluate_candidates",
                          return_value={expr.expr_id: {
                              "expr": expr, "source": "mom5",
                              "avg_icir": 0.3, "avg_ic": 0.05,
                              "avg_turnover": 0.1, "net_ic": 0.03,
                              "n_symbols": 1, "best_sym": "BTC",
                          }}), \
             patch.object(fel, "_purge_and_select",
                          return_value=[{"factor_id": expr.expr_id,
                                         "source": "mom5",
                                         "expr_ast": ast,
                                         "eval_result": type("E", (), {
                                             "icir": 0.3, "monotonicity_p": 0.1,
                                             "turnover": 0.1, "halflife_bars": 8})(),
                                         "incremental_corr": 0.3,
                                         "expr": expr}]), \
             patch.object(fel, "_promote_factors",
                          return_value=[{"factor_id": expr.expr_id,
                                         "source": "mom5", "expr_ast": ast,
                                         "expr": expr, "_to_state": "PAPER",
                                         "incremental_corr": 0.3,
                                         "eval_result": type("E", (), {
                                             "icir": 0.3})()}]), \
             patch.object(fel, "_load_active_factors", return_value=[]), \
             patch.object(fel, "_final_test_confirm",
                          side_effect=lambda p, e, d: p), \
             patch.object(fel, "_save_active_factors"), \
             patch.object(fel, "_monitor_active", return_value=[]), \
             patch.object(fel, "_advance_shadow_factors", return_value=[]), \
             patch.object(fel, "_review_active_factors",
                          return_value=([], [])), \
             patch.object(fel, "_enforce_active_cap", return_value=0), \
             patch.object(fel, "_replace_degraded", return_value=0), \
             patch.object(fel, "_update_online_weights", return_value={}), \
             patch.object(fel, "_log_evolution", side_effect=fake_log), \
             patch("backend.services.evolution.factor_wfo.run_factor_wfo",
                   return_value={"passed": True, "report": None, "skipped": False}), \
             patch("backend.services.evolution.factor_wfo.run_factor_wfo_ic",
                   return_value=ic_wfo_result):
            report = fel.run_factor_evolution_loop(symbols=["BTC"], period="1d")
        return report, logged, expr.expr_id

    def test_ic_wfo_reject_blocks_promotion(self):
        """IC-WFO 拒绝 → 晋升被拦截 + 日志 action=wfo_ic_reject（门禁开启时）。"""
        report, logged, fid = self._run_loop({
            "passed": False, "skipped": False,
            "oos_ic_mean": -0.01, "oos_ic_p": 0.9, "decay_rate": 0.8,
        })
        assert report["promoted"] == 0
        rejects = [l for l in logged if l[2].get("action") == "wfo_ic_reject"]
        assert len(rejects) == 1
        assert rejects[0][0] == fid
        assert "OOS IC 均值" in rejects[0][2]["reason"]

    def test_ic_wfo_pass_allows_promotion(self):
        """IC-WFO 通过 → 晋升保留。"""
        report, logged, fid = self._run_loop({
            "passed": True, "skipped": False,
            "oos_ic_mean": 0.03, "oos_ic_p": 0.01, "decay_rate": 0.2,
        })
        assert report["promoted"] == 1
        assert not [l for l in logged if l[2].get("action") == "wfo_ic_reject"]

    def test_gate_disabled_skips_wfo_rejection(self):
        """[2026-09-02 E14] FEATURE_WFO_GATE_ENABLED=false → 不因 WFO 拒绝而拦截。

        锁定 F36b 的开关语义（仓库 .env 当前正是 false）：门禁关闭时 WFO 的拒绝
        结论不参与晋升决策，改由 PAPER 影子期 OOS 复检兜底。此用例与上面
        test_ic_wfo_reject_blocks_promotion 成对，任一侧被误改都会立刻暴露。
        """
        report, logged, fid = self._run_loop({
            "passed": False, "skipped": False,
            "oos_ic_mean": -0.01, "oos_ic_p": 0.9, "decay_rate": 0.8,
        }, wfo_gate="false")
        assert report["promoted"] == 1, "门禁关闭时不应因 WFO 拒绝而拦截晋升"
        assert not [l for l in logged if l[2].get("action") == "wfo_ic_reject"]
