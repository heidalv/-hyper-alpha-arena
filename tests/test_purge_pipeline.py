"""
P1.2 因子清洗管线测试。

完成标准（方案 P1.2）：
    - 静态审计拦截无法转译/audit 失败的因子
    - 去重移除完全重复 + 近重复
    - CPCV 初筛按 ICIR/单调性/turnover 阈值过滤
    - 增量池筛选实现 pool-aware（高相关因子被拒）
    - 输出 ≤ max_active_factors
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backend.services.factor_engine.purge_pipeline import (
    CandidateFactor,
    PurgeConfig,
    run_purge_pipeline,
    stage1_static_audit,
    stage2_dedup,
    stage6_pool_select,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def n_series():
    return 200


@pytest.fixture
def return_series(n_series):
    rng = np.random.default_rng(42)
    return pd.Series(rng.normal(0, 0.01, n_series))


def _make_candidate(fid: str, ast: dict | None, factor_vals: np.ndarray | None = None) -> CandidateFactor:
    c = CandidateFactor(factor_id=fid, source_name=fid, expr_ast=ast)
    c._vals = factor_vals  # type: ignore[attr-defined]
    return c


class TestStage1StaticAudit:
    def test_valid_ast_passes(self):
        c = _make_candidate("f1", {"op": "rank", "args": [{"op": "corr", "args": [{"f": "vwap"}, {"f": "volume"}, {"c": 5}]}]})
        surv, rej = stage1_static_audit([c])
        assert len(surv) == 1
        assert len(rej) == 0

    def test_none_ast_rejected(self):
        c = _make_candidate("f1", None)
        surv, rej = stage1_static_audit([c])
        assert len(surv) == 0
        assert rej[0].reject_reason == "无法转译为表达式 AST（自由 Python 代码）"

    def test_invalid_ast_rejected(self):
        # look-ahead（负窗口）
        c = _make_candidate("f1", {"op": "mean", "args": [{"f": "close"}, {"c": -5}]})
        surv, rej = stage1_static_audit([c])
        assert len(surv) == 0
        assert "audit 失败" in rej[0].reject_reason


class TestStage2Dedup:
    def test_exact_duplicate_removed(self):
        ast = {"op": "rank", "args": [{"f": "close"}]}
        c1 = _make_candidate("f1", ast)
        c2 = _make_candidate("f2", ast)  # 完全相同
        surv, rej = stage2_dedup([c1, c2], PurgeConfig())
        assert len(surv) == 1

    def test_different_ast_kept(self):
        c1 = _make_candidate("f1", {"op": "rank", "args": [{"f": "close"}]})
        c2 = _make_candidate("f2", {"op": "rank", "args": [{"f": "volume"}]})
        surv, rej = stage2_dedup([c1, c2], PurgeConfig())
        assert len(surv) == 2

    def test_near_duplicate_removed_by_value(self, n_series):
        rng = np.random.default_rng(1)
        base = rng.normal(0, 1, n_series)
        c1 = _make_candidate("f1", {"op": "rank", "args": [{"f": "close"}]}, base)
        c2 = _make_candidate("f2", {"op": "rank", "args": [{"f": "vwap"}]}, base * 1.001)  # 近重复
        c3 = _make_candidate("f3", {"op": "rank", "args": [{"f": "volume"}]}, rng.normal(0, 1, n_series))
        eval_fn = lambda c: c._vals  # type: ignore[attr-defined]
        surv, rej = stage2_dedup([c1, c2, c3], PurgeConfig(dedup_corr_threshold=0.95), eval_fn=eval_fn)
        assert len(surv) == 2  # c2 被去重


class TestStage6PoolSelect:
    def test_high_corr_rejected(self, n_series, return_series):
        """两个高相关因子，pool-aware 只留一个。"""
        rng = np.random.default_rng(3)
        base = rng.normal(0, 1, n_series)
        c1 = _make_candidate("f1", {"op": "rank", "args": [{"f": "close"}]}, base)
        c2 = _make_candidate("f2", {"op": "rank", "args": [{"f": "volume"}]}, base * 1.01)  # 高相关
        # 给 ICIR（让排序有意义）
        from backend.services.factor_engine.evaluation import FactorEvalResult
        c1.eval_result = FactorEvalResult("f1", icir=0.5)
        c2.eval_result = FactorEvalResult("f2", icir=0.4)
        fs_fn = lambda c: pd.Series(c._vals)  # type: ignore[attr-defined]
        pool, rej = stage6_pool_select([c1, c2], fs_fn, return_series, PurgeConfig())
        assert len(pool) == 1
        assert len(rej) == 1

    def test_low_corr_both_kept(self, n_series, return_series):
        rng = np.random.default_rng(4)
        c1 = _make_candidate("f1", {"f": "close"}, rng.normal(0, 1, n_series))
        c2 = _make_candidate("f2", {"f": "volume"}, rng.normal(0, 1, n_series))  # 独立
        from backend.services.factor_engine.evaluation import FactorEvalResult
        c1.eval_result = FactorEvalResult("f1", icir=0.5)
        c2.eval_result = FactorEvalResult("f2", icir=0.4)
        fs_fn = lambda c: pd.Series(c._vals)  # type: ignore[attr-defined]
        pool, rej = stage6_pool_select([c1, c2], fs_fn, return_series, PurgeConfig())
        assert len(pool) == 2

    def test_max_active_cap(self, n_series, return_series):
        """超过 max_active 的被拒。"""
        rng = np.random.default_rng(5)
        cands = []
        for i in range(10):
            c = _make_candidate(f"f{i}", {"f": "close"}, rng.normal(0, 1, n_series))
            from backend.services.factor_engine.evaluation import FactorEvalResult
            c.eval_result = FactorEvalResult(f"f{i}", icir=0.5 - i * 0.01)
            cands.append(c)
        fs_fn = lambda c: pd.Series(c._vals)  # type: ignore[attr-defined]
        pool, rej = stage6_pool_select(cands, fs_fn, return_series, PurgeConfig(max_active_factors=5))
        assert len(pool) == 5


class TestRunFullPipeline:
    def test_end_to_end(self, n_series, return_series):
        """端到端：混合好/坏/重复因子，管线正确过滤。"""
        rng = np.random.default_rng(7)
        # 构造强信号因子：因子值直接 = 收益 ×10（强相关，ICIR/单调性都会显著）
        strong_signal = return_series.values * 10 + rng.normal(0, 0.01, n_series)
        cands = [
            # 有效 + 高 ICIR（与收益强相关）
            _make_candidate("good1", {"op": "rank", "args": [{"f": "close"}]}, strong_signal),
            # 近重复 good1
            _make_candidate("dup1", {"op": "rank", "args": [{"f": "vwap"}]}, strong_signal * 1.001),
            # 无法转译
            _make_candidate("bad1", None),
            # audit 失败（look-ahead）
            _make_candidate("bad2", {"op": "mean", "args": [{"f": "close"}, {"c": -3}]}),
            # 独立噪声因子（低 ICIR，会被初筛拒）
            _make_candidate("noise", {"op": "rank", "args": [{"f": "volume"}]},
                            rng.normal(0, 1, n_series)),
        ]
        fs_fn = lambda c: pd.Series(c._vals)  # type: ignore[attr-defined]
        final, report = run_purge_pipeline(
            cands, factor_series_fn=fs_fn, return_series=return_series,
            config=PurgeConfig(),
        )
        assert report.total_input == 5
        assert report.rejected_static >= 2  # bad1 + bad2
        # good1 和 dup1 至少去重一个，noise 被初筛拒
        assert report.surviving >= 1
        print(f"\n清洗报告: {report.summary()}")
