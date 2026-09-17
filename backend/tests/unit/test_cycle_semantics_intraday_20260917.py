"""[轮48 2026-09-17 目标④] 「日内(intraday) + 长期趋势(trend)」周期语义对齐契约测试。

用户口径：真实结构是「日内 + 长期趋势」，而非「中期 + 长期」。
实测支持：mid 车道中位持仓 3.2h、94% < 24h（= 日内）；long 车道中位 12.8h、>7d 0 笔。

根因（本测试锁定的缺陷）：15m/1h 因子被 `_tag_one_short_horizon` 打成
`horizon=scalp`，而两个池的判据是
    scalp_active_factor_set._is_scalp   : horizon != "midlong"  → True
    midlong_active_factor_set._is_midlong: horizon == "midlong"  → False
⇒ 15m/1h 必然落进**已判死**的短线池，永远进不了交易。
因子库现状：15m 20 个 / 1h 24 个，全部 rejected、0 candidate / 0 active。

契约：
1. 默认（FACTOR_EVO_INTRADAY_PERIODS 未设）打标与路由**逐位不变**；
2. 设 "15m" 后 15m 改打 horizon=intraday + intra_ 前缀，且 1h/4h 不受影响；
3. 设 "15m,1h" 后两者都迁；越界值（如 "4h"）被忽略；
4. 池路由：intraday 既不属于短线池，也能被中线池接纳；
5. settings 侧声明存在。
"""
from __future__ import annotations

import pytest


# ── 1. 语义真源 ────────────────────────────────────────────────────────────

def test_period_to_cycle_mapping():
    from backend.config.cycle_semantics import period_to_cycle, INTRADAY, TREND
    for p in ("1m", "3m", "5m", "15m", "30m", "1h", "2h"):
        assert period_to_cycle(p) == INTRADAY, f"{p} 应为日内"
    for p in ("4h", "8h", "1d", "1w", "1M"):
        assert period_to_cycle(p) == TREND, f"{p} 应为长期趋势"


def test_month_vs_minute_not_confused():
    """1M(月) 与 1m(分) 必须区分 —— 大小写归一化最易在此出错。"""
    from backend.config.cycle_semantics import period_to_cycle, INTRADAY, TREND
    assert period_to_cycle("1M") == TREND
    assert period_to_cycle("1m") == INTRADAY


def test_legacy_alias_normalization():
    """存量标签只做等价换算，不声称数据已被改写。"""
    from backend.config.cycle_semantics import normalize_horizon, INTRADAY, TREND
    assert normalize_horizon("scalp") == INTRADAY
    assert normalize_horizon("midlong") == TREND
    assert normalize_horizon("intraday") == INTRADAY
    # "mid" 故意不映射（它在代码里同时指 4h 与 15m，映射会把歧义固化）
    assert normalize_horizon("mid") is None
    assert normalize_horizon(None) is None


# ── 2. 开关：周期列表 ──────────────────────────────────────────────────────

def test_intraday_periods_default_empty(monkeypatch):
    monkeypatch.delenv("FACTOR_EVO_INTRADAY_PERIODS", raising=False)
    from backend.config.cycle_semantics import intraday_periods, intraday_horizon_enabled
    assert intraday_periods() == frozenset()
    assert intraday_horizon_enabled() is False


def test_intraday_periods_reads_env_and_filters(monkeypatch):
    """越界周期必须被忽略 —— 否则 4h 因子会被误迁进日内档。"""
    monkeypatch.setenv("FACTOR_EVO_INTRADAY_PERIODS", "15m,1h,4h,垃圾")
    from backend.config.cycle_semantics import intraday_periods
    assert intraday_periods() == frozenset({"15m", "1h"})


# ── 3. 打标：默认不变 / 启用后迁移 ─────────────────────────────────────────

def test_tagging_default_unchanged(monkeypatch):
    """默认（列表为空）时 15m 仍打 s5m_ + horizon=scalp —— 逐位维持原行为。"""
    monkeypatch.delenv("FACTOR_EVO_INTRADAY_PERIODS", raising=False)
    from backend.services.evolution.factor_evolution_loop import _tag_one_short_horizon
    f = _tag_one_short_horizon({"factor_id": "abc", "source": "gp"}, "15m")
    assert f["factor_id"] == "s5m_abc"
    assert "horizon=scalp|period=15m" in f["source"]


def test_tagging_migrates_15m_only(monkeypatch):
    monkeypatch.setenv("FACTOR_EVO_INTRADAY_PERIODS", "15m")
    from backend.services.evolution.factor_evolution_loop import _tag_one_short_horizon
    f = _tag_one_short_horizon({"factor_id": "abc", "source": "gp"}, "15m")
    assert f["factor_id"] == "intra_abc"
    assert "horizon=intraday|period=15m" in f["source"]
    assert "horizon=scalp" not in f["source"]
    # 1h 未在列表内 → 与改动前一致（1h 本就不在 _SHORT_HORIZON_PERIODS，不打标）
    g = _tag_one_short_horizon({"factor_id": "h1", "source": "gp"}, "1h")
    assert g["factor_id"] == "h1"
    # 5m 未在列表内 → 仍走短线档
    s = _tag_one_short_horizon({"factor_id": "m5", "source": "gp"}, "5m")
    assert s["factor_id"] == "s5m_m5"


def test_tagging_migrates_both_15m_and_1h(monkeypatch):
    """用户要求补的正是这两个周期。"""
    monkeypatch.setenv("FACTOR_EVO_INTRADAY_PERIODS", "15m,1h")
    from backend.services.evolution.factor_evolution_loop import _tag_one_short_horizon
    for p in ("15m", "1h"):
        f = _tag_one_short_horizon({"factor_id": "x", "source": "gp"}, p)
        assert f["factor_id"] == "intra_x", f"{p} 未迁档"
        assert f"horizon=intraday|period={p}" in f["source"], f"{p} tag 错误"


def test_tagging_does_not_double_prefix(monkeypatch):
    monkeypatch.setenv("FACTOR_EVO_INTRADAY_PERIODS", "15m")
    from backend.services.evolution.factor_evolution_loop import _tag_one_short_horizon
    f = _tag_one_short_horizon({"factor_id": "intra_abc", "source": "gp"}, "15m")
    assert f["factor_id"] == "intra_abc"


# ── 4. 池路由（这是"进不了交易"的直接原因）────────────────────────────────

def test_scalp_pool_excludes_intraday():
    from backend.services.factor_engine.scalp_active_factor_set import _is_scalp
    assert _is_scalp({"extra": {"horizon": "scalp"}}) is True
    assert _is_scalp({}) is True                      # 缺省仍算短线（原行为）
    assert _is_scalp({"extra": {"horizon": "midlong"}}) is False
    assert _is_scalp({"extra": {"horizon": "intraday"}}) is False   # ★ 本轮修复


def test_midlong_pool_accepts_intraday():
    from backend.services.factor_engine.midlong_active_factor_set import _is_midlong
    assert _is_midlong({"extra": {"horizon": "midlong"}}) is True
    assert _is_midlong({"extra": {"horizon": "scalp"}}) is False
    assert _is_midlong({}) is False
    assert _is_midlong({"extra": {"horizon": "intraday"}}) is True   # ★ 本轮修复（车道融合）


def test_migration_actually_reroutes(monkeypatch):
    """端到端：迁档后 15m 因子必须落进中线池、且不在短线池 —— 这是目标本身。"""
    monkeypatch.setenv("FACTOR_EVO_INTRADAY_PERIODS", "15m,1h")
    from backend.services.evolution.factor_evolution_loop import _tag_one_short_horizon
    from backend.services.factor_engine.scalp_active_factor_set import _is_scalp
    from backend.services.factor_engine.midlong_active_factor_set import _is_midlong
    for p in ("15m", "1h"):
        f = _tag_one_short_horizon({"factor_id": "z", "source": "gp"}, p)
        rec = {"factor_id": f["factor_id"], "source": f["source"],
               "extra": {"horizon": "intraday"}}   # 登记时 extra.horizon 取该值
        assert _is_scalp(rec) is False, f"{p} 仍被判为短线"
        assert _is_midlong(rec) is True, f"{p} 未被中线池接纳"


# ── 5. settings 声明（§73.4）──────────────────────────────────────────────

def test_settings_declares_both_switches():
    import inspect
    from backend.config import settings as S
    assert hasattr(S, "FACTOR_EVO_INTRADAY_PERIODS")
    assert isinstance(S.FACTOR_EVO_INTRADAY_PERIODS, str)
    assert isinstance(S.FACTOR_EVO_INTRADAY_1H_ENABLED, bool)
    src = inspect.getsource(S)
    assert 'FACTOR_EVO_INTRADAY_1H_ENABLED", "true"' in src, "1h 进化开关代码默认值应为 true"


# ── 6. 1h 进化入口存在（此前 1h 完全没有调度）──────────────────────────────

def test_1h_evolution_entrypoint_exists():
    from backend.services.evolution.factor_evolution_loop import (
        run_intraday_1h_factor_evolution_loop,
    )
    assert callable(run_intraday_1h_factor_evolution_loop)


def test_1h_has_split_and_fwd_config():
    """机器本身要支持 1h —— 否则加了调度也跑不了。"""
    from backend.services.evolution.factor_evolution_loop import (
        _PERIOD_SPLIT_DAYS, _PERIOD_FWD_BARS,
    )
    assert "1h" in _PERIOD_SPLIT_DAYS
    assert _PERIOD_FWD_BARS["1h"] == 2, "1h 前瞻应为 2 根 = 2h（日内节奏）"
