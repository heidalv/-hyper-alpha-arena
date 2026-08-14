"""阶段 5（DSL 前视 + 调度/鉴权）回归测试（2026-08-14）。

锁定：
- P1-G1 cs_rank 单序列默认抛错；env 开关下退化为滚动 ts_rank（因果）
- P1-G1 expr audit 拦截 rank/cs_rank/scale（新公式禁用）；ts_rank 等滚动算子放行
- parser 对 rank 表达式抛 ExprError（fail-closed）
"""
from __future__ import annotations

import numpy as np
import pytest


# ═══════════════════════════════════════════════════════════
# P1-G1：cs_rank
# ═══════════════════════════════════════════════════════════

def test_cs_rank_raises_by_default():
    from backend.services.factor_engine.expr.ops import cs_rank

    with pytest.raises(ValueError):
        cs_rank(np.linspace(1, 10, 10))


def test_cs_rank_causal_fallback_with_env(monkeypatch):
    from backend.services.factor_engine.expr.ops import cs_rank

    monkeypatch.setenv("FACTOR_EXPR_RANK_SINGLE_SERIES", "1")
    out = cs_rank(np.linspace(1, 10, 50))
    assert out.shape == (50,)
    # 滚动 ts_rank：前 19 根为 NaN（窗口预热），其后因果（只用 ≤t 数据）
    assert np.isnan(out[:19]).all()
    assert np.isfinite(out[20:]).all()


# ═══════════════════════════════════════════════════════════
# P1-G1：audit 拦截
# ═══════════════════════════════════════════════════════════

def test_audit_blocks_single_series_lookahead_ops():
    from backend.services.factor_engine.expr.audit import audit

    for op in ("rank", "cs_rank", "scale"):
        result = audit({"op": op, "args": [{"f": "close"}, {"c": 1.0}]})
        assert result.ok is False
        assert any(op in e for e in result.errors)


def test_audit_allows_causal_rolling_ops():
    from backend.services.factor_engine.expr.audit import audit

    ok1 = audit({"op": "ts_rank", "args": [{"f": "close"}, {"c": 20}]})
    assert ok1.ok is True
    ok2 = audit({"op": "mean", "args": [{"f": "close"}, {"c": 10}]})
    assert ok2.ok is True


def test_parser_rejects_rank_expression():
    from backend.services.factor_engine.expr.parser import ExprError, parse

    with pytest.raises(ExprError):
        parse({"op": "rank", "args": [{"f": "close"}]})
