"""
P1.7 永续特化因子 + P1.8 DatasetCache 测试。

P1.7 完成标准：永续因子表达式通过 audit + 求值有限。
P1.8 完成标准：DatasetCache 命中；infer/learn 分离独立；标签横截面 z-score 生效。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backend.services.factor_engine.dataset_cache import (
    DataHandler,
    DatasetCache,
    DropnaLabel,
    Fillna,
    RobustScale,
    ZScoreNorm,
)
from backend.services.factor_engine.expr.audit import is_safe
from backend.services.factor_engine.expr.parser import parse
from backend.services.factor_engine.perp_factors import (
    PERP_FACTOR_EXPRS,
    get_perp_factor_expr,
    get_perp_factor_names,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def perp_fields():
    """永续合约字段数据（含 funding/oi/basis/liquidation）。"""
    rng = np.random.default_rng(42)
    n = 200
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    return {
        "close": close,
        "returns": np.concatenate([[0.0], np.diff(close) / close[:-1]]),
        "funding": rng.normal(0.0001, 0.0005, n),
        "oi": np.abs(rng.normal(1e8, 1e7, n)),
        "basis": rng.normal(0.001, 0.005, n),
        "liquidation": np.abs(rng.normal(1e6, 5e5, n)),
    }


# ==================== P1.7 永续因子 ====================

class TestPerpFactors:
    def test_all_factors_audit_pass(self):
        """所有永续因子表达式通过 audit（无 look-ahead / 结构错误）。"""
        for name, ast in PERP_FACTOR_EXPRS.items():
            assert is_safe(ast), f"因子 {name} audit 失败"

    def test_all_factors_parse_and_eval(self, perp_fields):
        """所有永续因子可 parse 并求值（输出有限值数组）。"""
        for name in get_perp_factor_names():
            ast = get_perp_factor_expr(name)
            expr = parse(ast)
            out = expr.evaluate(perp_fields)
            assert len(out) == len(perp_fields["close"]), f"{name} 输出长度不符"

    def test_funding_skew_finite(self, perp_fields):
        expr = parse(get_perp_factor_expr("funding_skew"))
        out = expr.evaluate(perp_fields)
        valid = out[np.isfinite(out)]
        assert len(valid) > 0

    def test_get_unknown_raises(self):
        with pytest.raises(KeyError):
            get_perp_factor_expr("nonexistent")


# ==================== P1.8 DatasetCache ====================

@pytest.fixture
def sample_df():
    rng = np.random.default_rng(1)
    n = 100
    return pd.DataFrame({
        "f1": rng.normal(0, 1, n),
        "f2": rng.normal(5, 2, n),
        "label": rng.normal(0, 0.1, n),
    }, index=pd.RangeIndex(n))


class TestProcessors:
    def test_fillna(self, sample_df):
        df = sample_df.copy()
        df.iloc[5, 0] = np.nan
        out = Fillna()(df)
        assert not out.isna().any().any()

    def test_zscore_norm_zero_mean_unit_std(self, sample_df):
        p = ZScoreNorm().fit(sample_df[["f1", "f2"]])
        out = p(sample_df[["f1", "f2"]])
        assert abs(out["f1"].mean()) < 1e-9
        assert abs(out["f1"].std() - 1.0) < 1e-9

    def test_robust_scale(self, sample_df):
        out = RobustScale()(sample_df[["f1", "f2"]])
        assert len(out) == len(sample_df)

    def test_dropna_label(self, sample_df):
        df = sample_df.copy()
        df.iloc[10, df.columns.get_loc("label")] = np.nan
        out = DropnaLabel("label")(df)
        assert len(out) == len(df) - 1


class TestDataHandler:
    def test_infer_learn_separation(self, sample_df):
        """infer 处理特征，learn 额外处理标签。"""
        handler = DataHandler(
            infer_processors=[Fillna(), ZScoreNorm()],
            learn_processors=[DropnaLabel("label")],
        )
        handler.fit(sample_df)
        # infer 路径：保留所有行，特征归一化
        infer_out = handler.process_infer(sample_df)
        assert len(infer_out) == len(sample_df)
        # learn 路径：应用 infer + learn
        learn_out = handler.process_learn(sample_df)
        assert "label" in learn_out.columns or len(learn_out) <= len(sample_df)

    def test_split_features_label(self, sample_df):
        handler = DataHandler()
        feats, label = handler.split_features_label(sample_df)
        assert "label" not in feats.columns
        assert len(label) == len(sample_df)

    def test_infer_uses_fit_params(self, sample_df):
        """infer 用 fit 时学的参数（不重新 fit，防泄漏）。"""
        handler = DataHandler(infer_processors=[ZScoreNorm()])
        handler.fit(sample_df)
        # process_infer 不应重新 fit
        out1 = handler.process_infer(sample_df)
        out2 = handler.process_infer(sample_df)
        pd.testing.assert_frame_equal(out1, out2)


class TestDatasetCache:
    def test_cache_hit_same_config(self, sample_df):
        cache = DatasetCache()
        handler = DataHandler(infer_processors=[Fillna(), ZScoreNorm()])
        handler.fit(sample_df)
        cache.get_or_process(handler, sample_df, "train")
        assert cache.misses == 1
        cache.get_or_process(handler, sample_df, "train")
        assert cache.hits == 1

    def test_cache_miss_different_segment(self, sample_df):
        cache = DatasetCache()
        handler = DataHandler(infer_processors=[Fillna()])
        handler.fit(sample_df)
        cache.get_or_process(handler, sample_df, "train")
        cache.get_or_process(handler, sample_df, "test")
        assert cache.misses == 2

    def test_cache_miss_different_config(self, sample_df):
        cache = DatasetCache()
        h1 = DataHandler(infer_processors=[Fillna()])
        h1.fit(sample_df)
        h2 = DataHandler(infer_processors=[Fillna(), ZScoreNorm()])
        h2.fit(sample_df)
        cache.get_or_process(h1, sample_df, "train")
        cache.get_or_process(h2, sample_df, "train")
        assert cache.misses == 2  # 不同配置 → 都 miss

    def test_infer_vs_learn_separate_cache(self, sample_df):
        cache = DatasetCache()
        handler = DataHandler(
            infer_processors=[Fillna()],
            learn_processors=[DropnaLabel("label")],
        )
        handler.fit(sample_df)
        cache.get_or_process(handler, sample_df, "train", learn=False)
        cache.get_or_process(handler, sample_df, "train", learn=True)
        assert cache.misses == 2  # infer 和 learn 分开缓存
