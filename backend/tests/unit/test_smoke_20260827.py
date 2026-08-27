# -*- coding: utf-8 -*-
"""2026-08-27 全量改动冒烟: 新逻辑功能等价性/语义回归(本会话所有关键修复)。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
import numpy as np
import pandas as pd
from unittest.mock import MagicMock


# ── 1. 滚动算子向量化等价性(公式 ops 是本会话 MCTS 挂死修复的核心) ──
def test_vectorized_rolling_equivalence():
    from backend.services.factor_engine import formula_ops as F
    rng = np.random.default_rng(42)
    x = rng.normal(0, 1, 300)
    x[50] = np.nan
    x[120:130] = np.nan
    y = rng.normal(0, 1, 300)

    def ref_rolling(a, w, fn):
        # 新语义参考实现: 与 pandas rolling(min_periods=max(2, w//2)) 对齐——
        # 相比旧 Python 循环, 头部 w-2 个位置在有效值足够时也计算(更多覆盖, 非错误)。
        out = np.full(len(a), np.nan)
        for i in range(1, len(a)):
            win = a[max(0, i - w + 1):i + 1]
            m = np.isfinite(win)
            if m.sum() < max(2, w // 2):
                continue
            out[i] = fn(win[m])
        return out

    for w in (3, 10, 60):
        assert np.allclose(F.ts_sum(x, w), ref_rolling(x, w, np.sum), equal_nan=True), f"ts_sum w={w}"
        assert np.allclose(F.ts_mean(x, w), ref_rolling(x, w, np.mean), equal_nan=True), f"ts_mean w={w}"
        assert np.allclose(F.ts_std(x, w), ref_rolling(x, w, np.std), equal_nan=True), f"ts_std w={w}"
        assert np.allclose(F.ts_max(x, w), ref_rolling(x, w, np.max), equal_nan=True), f"ts_max w={w}"
        assert np.allclose(F.ts_min(x, w), ref_rolling(x, w, np.min), equal_nan=True), f"ts_min w={w}"


def test_ts_rank_semantics():
    from backend.services.factor_engine import formula_ops as F
    x = np.array([1.0, 3.0, 2.0, 5.0, 4.0, 6.0])
    out = F.ts_rank(x, 3)
    # 窗口[0..2]: rank(2)=2/3; 窗口[1..3]: rank(5)=3/3; 窗口[2..4]: rank(4)=2/3
    assert np.isnan(out[0]) and np.isnan(out[1])
    assert abs(out[2] - 2 / 3) < 1e-9
    assert abs(out[3] - 1.0) < 1e-9
    assert abs(out[4] - 2 / 3) < 1e-9
    assert abs(out[5] - 1.0) < 1e-9


def test_ts_corr_vectorized():
    from backend.services.factor_engine import formula_ops as F
    rng = np.random.default_rng(7)
    x = rng.normal(0, 1, 200)
    y = 0.5 * x + rng.normal(0, 0.5, 200)
    out = F.ts_corr(x, y, 20)
    assert np.isfinite(out[19:]).mean() > 0.95
    assert abs(float(np.nanmean(out[19:])) - 0.7) < 0.15


# ── 2. 滚动窗口上限(防挂死护栏) ──
def test_rolling_window_cap():
    from backend.services.factor_engine import formula_ops as F
    x = np.arange(500.0)
    # w=1e9 不应挂死/爆炸——上限钳到 120, 结果与 ts_mean(x, 120) 完全一致
    out = F.ts_mean(x, 1e9)
    ref = F.ts_mean(x, 120)
    assert len(out) == 500
    assert np.allclose(out, ref, equal_nan=True)


# ── 3. SL 只收紧不放宽(止损失效修复) ──
def test_sl_widen_rejected():
    os.environ["MIDLONG_ALLOW_SL_WIDEN"] = "false"
    from backend.services.paper_trading_engine import PaperTradingEngine
    eng = PaperTradingEngine.__new__(PaperTradingEngine)
    pos = MagicMock()
    pos.symbol = "TEST"; pos.side = "long"; pos.status = "open"
    pos.sl_price = 90.0  # 现有 SL(收紧方向)
    pos.tp_price = None
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = pos
    ok = eng.update_position_tp_sl(db, 1, sl_price=80.0)  # 放宽 → 拒
    assert ok is False
    assert pos.sl_price == 90.0


def test_sl_tighten_allowed():
    os.environ["MIDLONG_ALLOW_SL_WIDEN"] = "false"
    from backend.services.paper_trading_engine import PaperTradingEngine
    eng = PaperTradingEngine.__new__(PaperTradingEngine)
    pos = MagicMock()
    pos.symbol = "TEST"; pos.side = "long"; pos.status = "open"
    pos.sl_price = 90.0
    pos.tp_price = None
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = pos
    ok = eng.update_position_tp_sl(db, 1, sl_price=95.0)  # 收紧 → 允许
    assert ok is True
    assert pos.sl_price == 95.0


# ── 4. 门禁 min_fitness 重校(挖掘根治) ──
def test_admission_min_fitness_04():
    from backend.services.factor_engine.evaluation import admission_gate, DEFAULT_GATE_CONFIG
    assert DEFAULT_GATE_CONFIG["min_fitness"] == 0.4
    r = admission_gate(factor_id="t", top_quantile_sharpe=2.0, fitness=0.75,
                       turnover=0.2, max_pool_corr=0.1, ic_halflife_bars=8,
                       ic_mean=0.23, ic_p=1e-6)
    assert r.passed, r.reasons


# ── 5. DSR 单幸存者退化修复(挖掘根治) ──
def test_dsr_single_survivor_significant():
    from backend.services.factor_engine.dsr_pbo import compute_dsr_pbo_for_factors
    r = compute_dsr_pbo_for_factors(icir_list=[0.90], n_total_candidates=1, sample_len=1670)
    assert r["dsr_result"]["significant"] is True
    assert r["dsr_result"]["dsr"] > 30


# ── 6. Codegen 云端优先(用户指令) ──
def test_codegen_cloud_first_resolution():
    from backend.services.evolution.alpha_miner import CodegenCritic
    c = CodegenCritic()
    primary, fallback = c._load_configs()
    if primary is None:
        # 无 LLM 配置环境(CI)时跳过
        return
    assert str(getattr(primary, "provider", "")).lower() == "deepseek", getattr(primary, "provider", None)
    if fallback is not None:
        assert str(getattr(fallback, "provider", "")).lower() == "ollama"


# ── 7. 挖掘跨币错配修复(初筛) ──
def test_purge_factor_series_same_source():
    # factor_series_fn 与 return_series 必须同源(first_df)——修复后代码固定用
    # list(dfs.values())[0]; 校验实际代码不再通过 best_sym 取数据(注释提及不算)。
    import inspect
    from backend.services.evolution import factor_evolution_loop as L
    src = inspect.getsource(L._purge_and_select)
    fn_src = src.split("def factor_series_fn")[1].split("def factor_matrix_fn")[0]
    assert "dfs.get(best_sym)" not in fn_src
    assert "df = list(dfs.values())[0]" in fn_src


# ── 8. 晋升即隔离修复: M2 复评/漂移监控对本轮新晋升宽限一轮 ──
def test_review_promote_grace_skips_quarantine():
    """fresh_ids 内因子即使全窗 net_ic<阈值也不在同轮被隔离。"""
    from unittest.mock import patch
    from backend.services.evolution import factor_evolution_loop as L
    expr = MagicMock()
    expr.evaluate.return_value = np.ones(400) * 1e-6  # 近零序列 → net_ic≈0
    f = {"factor_id": "fresh_x", "source": "t", "expr": expr, "state": "PAPER"}
    idx = pd.date_range("2026-01-01", periods=400, freq="1d")
    closes = 100.0 * np.cumprod(1.0 + np.full(400, 0.001))
    df = pd.DataFrame({"open": closes, "high": closes * 1.001, "low": closes * 0.999,
                       "close": closes, "volume": np.full(400, 1e5)}, index=idx)
    with patch.object(L, "_deactivate_factor") as deact:
        kept, degraded = L._review_active_factors([f], {"BTC": df}, fresh_ids={"fresh_x"})
    assert degraded == []
    assert kept == [f]
    assert not deact.called


def test_monitor_promote_grace_skips_drift():
    """fresh_ids 内因子跳过本轮漂移监控(不给 DriftWatcher 喂样本)。"""
    from unittest.mock import patch, MagicMock as MM
    from backend.services.evolution import factor_evolution_loop as L
    expr = MM()
    expr.evaluate.return_value = np.arange(400, dtype=float)
    f = {"factor_id": "fresh_x", "source": "t", "expr": expr}
    idx = pd.date_range("2026-01-01", periods=400, freq="1d")
    closes = 100.0 * np.cumprod(1.0 + np.full(400, 0.001))
    df = pd.DataFrame({"open": closes, "high": closes * 1.001, "low": closes * 0.999,
                       "close": closes, "volume": np.full(400, 1e5)}, index=idx)

    class BoomWatcher:
        def observe_error(self, *a, **k):
            raise AssertionError("fresh 因子不应进入漂移监控")
        def should_rollback(self, *a, **k):
            return True
    with patch("backend.services.evolution.drift_watcher.DriftWatcher", BoomWatcher):
        degraded = L._monitor_active([f], {"BTC": df}, fresh_ids={"fresh_x"})
    assert degraded == []


def test_review_without_grace_still_quarantines():
    """无宽限时(非本轮晋升)近零 net_ic 仍被隔离——保证宽限没有弱化门禁。"""
    from unittest.mock import patch
    from backend.services.evolution import factor_evolution_loop as L
    expr = MagicMock()
    expr.evaluate.return_value = np.ones(400) * 1e-6
    f = {"factor_id": "old_x", "source": "t", "expr": expr, "state": "ACTIVE"}
    idx = pd.date_range("2026-01-01", periods=400, freq="1d")
    closes = 100.0 * np.cumprod(1.0 + np.full(400, 0.001))
    df = pd.DataFrame({"open": closes, "high": closes * 1.001, "low": closes * 0.999,
                       "close": closes, "volume": np.full(400, 1e5)}, index=idx)
    with patch.object(L, "_deactivate_factor") as deact:
        kept, degraded = L._review_active_factors([f], {"BTC": df})
    assert degraded == [f]
    assert kept == []
    assert deact.called
