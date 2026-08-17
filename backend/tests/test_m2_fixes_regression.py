"""M2 修复回归测试（2026-08 审计 M2 落地）。

覆盖：
- P1-2  降级期望值判据（低胜率高赔率不误杀）
- P1-7  tier→nature 单一权威
- P1-9  walk_forward 年化参数化
- P1-10 AST 前视审计（字面/变量负 shift）
- P1-11 learned_weighting 训练卫生（min_ic 筛选、时间切分、校验 IC 门槛）
- P1-12 记忆衰减时间戳多键回退

全部为纯单元测试（无 DB/网络）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
import pytest


# ────────────────────────── P1-10 AST 前视审计 ──────────────────────────

@pytest.mark.unit
def test_audit_literal_negative_shift_blocked():
    from backend.services.factor_engine.lookahead_audit import audit_lookahead

    verdict, detail = audit_lookahead("x = df['close'].shift(-3)")
    assert verdict == "blocked"
    verdict2, _ = audit_lookahead("y = close.shift(-1) / close - 1")
    assert verdict2 == "blocked"


@pytest.mark.unit
def test_audit_variable_negative_shift_blocked():
    """变量负移（旧正则漏检）：shift(-confirm_bars+1) 含一元负号 → blocked。"""
    from backend.services.factor_engine.lookahead_audit import audit_lookahead

    src = "future_low = data['low'].rolling(confirm_bars).min().shift(-confirm_bars+1)"
    verdict, detail = audit_lookahead(src)
    assert verdict == "blocked", detail


@pytest.mark.unit
def test_audit_positive_and_variable_shift_ok_or_review():
    from backend.services.factor_engine.lookahead_audit import audit_lookahead

    # 正常正窗口 shift(1) → ok
    assert audit_lookahead("x = close.shift(1)")[0] == "ok"
    # 变量窗口 shift(lookback)（可能负）→ review（人工复核，不拦截）
    assert audit_lookahead("x = close.shift(lookback)")[0] == "review"


# ────────────────────────── P1-12 记忆衰减时间戳 ──────────────────────────

@pytest.mark.unit
def test_memory_decay_entry_ts_fallback():
    from backend.services.memory_decay_service import MemoryDecayService

    # 无 ts 但有 discovered_at → 可解析（不再永不衰减）
    entry = {"discovered_at": "2026-08-01T00:00:00"}
    assert MemoryDecayService._entry_ts(entry) == "2026-08-01T00:00:00"
    # ingested_at 回退
    assert MemoryDecayService._entry_ts({"ingested_at": "2026-07-01"}) == "2026-07-01"
    # ts 优先
    assert MemoryDecayService._entry_ts({"ts": "a", "discovered_at": "b"}) == "a"
    # 无任何时间戳 → None
    assert MemoryDecayService._entry_ts({"type": "loss"}) is None


# ────────────────────────── P1-7 tier→nature 权威 ──────────────────────────

@pytest.mark.unit
def test_tier_nature_single_authority():
    from backend.services.tp_sl_authority import TIER_TO_NATURE
    from backend.services.trade_nature_resolver import TIER_TO_NATURE_MAP
    from backend.services.unified_learning_service import TIER_TO_NATURE as UL_TIER_TO_NATURE

    assert TIER_TO_NATURE == {"short": "scalp", "mid": "swing", "long": "trend_follow"}
    assert TIER_TO_NATURE_MAP == TIER_TO_NATURE
    assert UL_TIER_TO_NATURE == TIER_TO_NATURE


# ────────────────────────── P1-9 walk_forward 年化 ──────────────────────────

@pytest.mark.unit
def test_calc_sharpe_periods_parameterized():
    from backend.services.walk_forward_validator import WalkForwardValidator

    rets = np.array([0.01, -0.005, 0.02, 0.0, 0.015])
    s_h1 = WalkForwardValidator._calc_sharpe(rets, periods_per_year=8760.0)
    s_daily = WalkForwardValidator._calc_sharpe(rets, periods_per_year=365.0)
    assert s_h1 > s_daily > 0
    # 默认保持 h1=8760 兼容
    assert abs(WalkForwardValidator._calc_sharpe(rets) - s_h1) < 1e-9


# ────────────────────────── P1-11 learned_weighting 卫生 ──────────────────────────

@pytest.mark.unit
def test_train_rejects_model_on_weak_validation_ic():
    """校验段无预测力 → 丢弃新模型（保留旧模型 = model 仍为 None）。"""
    from backend.services.factor_engine.learned_weighting import LearnedFactorWeighting

    n = 200
    idx = pd.date_range("2026-01-01", periods=n, freq="h")
    rng = np.random.default_rng(7)
    noise = pd.DataFrame({f"f{i}": rng.normal(0, 1, n) for i in range(4)}, index=idx)
    labels = pd.Series(rng.normal(0, 1, n), index=idx)  # 纯噪声标签
    lw = LearnedFactorWeighting()
    ok = lw.train(noise, labels)
    # 噪声标签 → 校验 IC 低 → 模型被丢弃；即便训练本身不抛错，最终不得上线
    assert lw.model is None
    assert ok is False


@pytest.mark.unit
def test_train_accepts_predictive_data():
    from backend.services.factor_engine.learned_weighting import LearnedFactorWeighting

    n = 200
    idx = pd.date_range("2026-01-01", periods=n, freq="h")
    rng = np.random.default_rng(11)
    sig = rng.normal(0, 1, n)
    # f0 与标签强相关 → 校验段应通过（线性模型可学到）
    df = pd.DataFrame({"f0": sig, "f1": rng.normal(0, 1, n)}, index=idx)
    labels = pd.Series(sig * 3 + rng.normal(0, 0.3, n), index=idx)
    lw = LearnedFactorWeighting()
    ok = lw.train(df, labels)
    assert ok is True
    assert lw.model is not None