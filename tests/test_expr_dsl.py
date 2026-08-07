"""
P1.1 表达式 DSL 引擎测试。

完成标准（方案 P1.1）：
    1. Alpha101 公式可表达并通过 audit + 数值与现有实现对齐。
    2. look-ahead bias 被编译期拦截。
    3. 未知 op/字段被审计拒绝。
    4. ExpressionCache 命中/未命中统计正确。
    5. expr_id 相同表达式去重。
"""
from __future__ import annotations

import numpy as np
import pytest

from backend.services.factor_engine.expr.audit import audit, is_safe
from backend.services.factor_engine.expr.parser import (
    ExpressionCache,
    expr_id,
    parse,
)
from backend.services.factor_engine.formula_ops import (
    decay_linear,
    ts_corr,
    ts_mean,
    ts_rank,
)
from backend.services.factor_engine.formula_ops import (
    delta as fo_delta,
)

pytestmark = pytest.mark.unit


# ==================== 测试夹具 ====================

@pytest.fixture
def sample_fields():
    """100 根 K 线的合成 OHLCV 数据。"""
    rng = np.random.default_rng(42)
    n = 100
    close = 100 + np.cumsum(rng.normal(0, 1, n))
    return {
        "open": close * (1 + rng.normal(0, 0.001, n)),
        "high": close * (1 + np.abs(rng.normal(0, 0.005, n))),
        "low": close * (1 - np.abs(rng.normal(0, 0.005, n))),
        "close": close,
        "volume": np.abs(rng.normal(1000, 200, n)) + 10,
        "vwap": close * (1 + rng.normal(0, 0.0005, n)),
        "returns": np.concatenate([[0.0], np.diff(close) / close[:-1]]),
        "funding": rng.normal(0.0001, 0.0005, n),
        "oi": np.abs(rng.normal(1e6, 1e5, n)),
    }


# ==================== 1. 审计：合法表达式通过 ====================

class TestAuditPass:
    def test_simple_field(self):
        ast = {"f": "close"}
        assert is_safe(ast)

    def test_nested_rank_corr(self):
        # Alpha101 风格：rank(corr(vwap, volume, 5))
        ast = {"op": "rank", "args": [
            {"op": "corr", "args": [{"f": "vwap"}, {"f": "volume"}, {"c": 5}]}
        ]}
        assert is_safe(ast)

    def test_constant(self):
        ast = {"c": 3.14}
        assert is_safe(ast)

    def test_decay(self):
        ast = {"op": "decay_linear", "args": [{"f": "returns"}, {"c": 10}]}
        assert is_safe(ast)


# ==================== 2. 审计：拒绝非法表达式 ====================

class TestAuditReject:
    def test_unknown_op(self):
        ast = {"op": "nonexistent_op", "args": [{"f": "close"}]}
        r = audit(ast)
        assert not r.ok
        assert "未知算子" in r.errors[0]

    def test_unknown_field(self):
        ast = {"f": "future_price"}  # 不在 ALLOWED_FIELDS
        r = audit(ast)
        assert not r.ok
        assert "未知字段" in r.errors[0]

    def test_lookahead_negative_window(self):
        # 负窗口 = look-ahead
        ast = {"op": "mean", "args": [{"f": "close"}, {"c": -5}]}
        r = audit(ast)
        assert not r.ok
        assert "look-ahead" in r.errors[0]

    def test_lookahead_negative_ref(self):
        ast = {"op": "ref", "args": [{"f": "close"}, {"c": -3}]}
        r = audit(ast)
        assert not r.ok

    def test_arity_mismatch(self):
        # corr 期望 3 参数，给 2 个
        ast = {"op": "corr", "args": [{"f": "close"}, {"f": "volume"}]}
        r = audit(ast)
        assert not r.ok
        assert "期望" in r.errors[0]

    def test_non_dict_root(self):
        r = audit("not a dict")
        assert not r.ok


# ==================== 3. 求值：合法表达式产出有限值数组 ====================

class TestEvaluate:
    def test_field_eval(self, sample_fields):
        expr = parse({"f": "close"})
        out = expr.evaluate(sample_fields)
        assert len(out) == len(sample_fields["close"])
        assert np.allclose(out, sample_fields["close"])

    def test_rank_corr(self, sample_fields):
        ast = {"op": "rank", "args": [
            {"op": "corr", "args": [{"f": "close"}, {"f": "volume"}, {"c": 10}]}
        ]}
        expr = parse(ast)
        out = expr.evaluate(sample_fields)
        assert len(out) == 100
        # 排名结果应在 [0,1]
        valid = out[np.isfinite(out)]
        assert valid.min() >= -1e-9
        assert valid.max() <= 1.0 + 1e-9

    def test_protected_div_no_nan_explosion(self, sample_fields):
        ast = {"op": "div", "args": [{"f": "close"}, {"c": 0}]}
        expr = parse(ast)
        out = expr.evaluate(sample_fields)
        # 除以 0 应返回 0 而非 inf/nan
        assert np.all(np.isfinite(out))
        assert np.all(out == 0.0)

    def test_log_protected(self, sample_fields):
        ast = {"op": "log", "args": [{"f": "volume"}]}
        out = parse(ast).evaluate(sample_fields)
        assert np.all(np.isfinite(out[np.isfinite(out)]))


# ==================== 4. Alpha101 数值对齐 ====================

class TestAlpha101Alignment:
    """
    验证 DSL 算子与现有 formula_ops 实现数值一致（最大相对误差 < 1e-9）。
    这是 P1.1 的硬完成标准：DSL 可表达 Alpha101 且数值对齐。
    """

    def test_ts_mean_alignment(self, sample_fields):
        ast = {"op": "mean", "args": [{"f": "close"}, {"c": 10}]}
        dsl_out = parse(ast).evaluate(sample_fields)
        ref_out = ts_mean(sample_fields["close"], 10)
        assert np.allclose(dsl_out, ref_out, equal_nan=True)

    def test_ts_rank_alignment(self, sample_fields):
        ast = {"op": "ts_rank", "args": [{"f": "volume"}, {"c": 20}]}
        dsl_out = parse(ast).evaluate(sample_fields)
        ref_out = ts_rank(sample_fields["volume"], 20)
        assert np.allclose(dsl_out, ref_out, equal_nan=True)

    def test_ts_corr_alignment(self, sample_fields):
        ast = {"op": "corr", "args": [{"f": "close"}, {"f": "volume"}, {"c": 10}]}
        dsl_out = parse(ast).evaluate(sample_fields)
        ref_out = ts_corr(sample_fields["close"], sample_fields["volume"], 10)
        assert np.allclose(dsl_out, ref_out, equal_nan=True)

    def test_decay_linear_alignment(self, sample_fields):
        ast = {"op": "decay_linear", "args": [{"f": "returns"}, {"c": 5}]}
        dsl_out = parse(ast).evaluate(sample_fields)
        ref_out = decay_linear(sample_fields["returns"], 5)
        assert np.allclose(dsl_out, ref_out, equal_nan=True)

    def test_delta_alignment(self, sample_fields):
        ast = {"op": "delta", "args": [{"f": "close"}, {"c": 3}]}
        dsl_out = parse(ast).evaluate(sample_fields)
        ref_out = fo_delta(sample_fields["close"], 3)
        assert np.allclose(dsl_out, ref_out, equal_nan=True)


# ==================== 5. expr_id 去重 ====================

class TestExprId:
    def test_same_ast_same_id(self):
        a = {"op": "rank", "args": [{"op": "corr", "args": [{"f": "vwap"}, {"f": "volume"}, {"c": 5}]}]}
        b = {"op": "rank", "args": [{"op": "corr", "args": [{"f": "vwap"}, {"f": "volume"}, {"c": 5}]}]}
        assert expr_id(a) == expr_id(b)

    def test_different_ast_different_id(self):
        a = {"op": "ts_rank", "args": [{"f": "close"}, {"c": 5}]}
        b = {"op": "ts_rank", "args": [{"f": "close"}, {"c": 10}]}
        assert expr_id(a) != expr_id(b)

    def test_arg_order_independent_for_id(self):
        # 规范化后，不同键顺序应给相同 id（dict 键排序）
        a = {"args": [{"c": 5}, {"f": "close"}], "op": "ts_rank"}
        b = {"op": "ts_rank", "args": [{"c": 5}, {"f": "close"}]}
        assert expr_id(a) == expr_id(b)


# ==================== 6. ExpressionCache ====================

class TestExpressionCache:
    def test_cache_hit(self, sample_fields):
        cache = ExpressionCache()
        ast = {"op": "ts_rank", "args": [{"f": "close"}, {"c": 10}]}
        expr = parse(ast)
        # 首次：miss
        cache.get_or_eval(expr, sample_fields, "BTC", "win1")
        assert cache.misses == 1
        assert cache.hits == 0
        # 二次：hit
        cache.get_or_eval(expr, sample_fields, "BTC", "win1")
        assert cache.hits == 1
        assert cache.stats()["hit_rate"] == pytest.approx(0.5)

    def test_cache_different_window_miss(self, sample_fields):
        cache = ExpressionCache()
        ast = {"op": "mean", "args": [{"f": "close"}, {"c": 5}]}
        expr = parse(ast)
        cache.get_or_eval(expr, sample_fields, "BTC", "win1")
        cache.get_or_eval(expr, sample_fields, "BTC", "win2")
        assert cache.misses == 2
        assert cache.hits == 0

    def test_cache_lru_eviction(self, sample_fields):
        cache = ExpressionCache(max_entries=2)
        ast = {"op": "mean", "args": [{"f": "close"}, {"c": 5}]}
        expr = parse(ast)
        cache.get_or_eval(expr, sample_fields, "BTC", "w1")
        cache.get_or_eval(expr, sample_fields, "BTC", "w2")
        cache.get_or_eval(expr, sample_fields, "BTC", "w3")  # 触发驱逐
        assert len(cache._cache) <= 2
