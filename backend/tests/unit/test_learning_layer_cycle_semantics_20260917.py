"""[轮51 2026-09-17 目标④] 学习进化层的周期语义对齐契约测试。

问题：学习层 `evolution_memory_v7._period_to_cycle` 是**第三套**独立词表
（4h/8h/1d→"L"，15m/30m/1h/2h→"M"，其余→"S"），与另外两层冲突 ——
同一个"中"字在三层指三个不同周期：
    因子发现：4h = "midlong"(中期)
    调度 V7 ：15m = "中周期"
    学习层  ：15m/30m/1h/2h = "M"(中期)
    策略层  ：mid 车道实际中位持仓 3.2h（= 日内）

本测试锁定：学习层改为从唯一真源 `backend/config/cycle_semantics.py` 派生，
且行为差异只有一处已知项（1w/1M 从错误的 "S" 归到 "L"）。
"""
from __future__ import annotations

import pytest


def _cyc(p):
    from backend.services.evolution.evolution_memory_v7 import _period_to_cycle
    return _period_to_cycle(p)


def test_intraday_periods_map_to_M():
    """15m/30m/1h/2h = 日内 → "M"（字母保留，含义重定为「日内」而非「中期」）。"""
    for p in ("15m", "30m", "1h", "2h", "15M".lower()):
        assert _cyc(p) == "M", f"{p} 应为日内档 M"


def test_trend_periods_map_to_L():
    for p in ("4h", "8h", "1d"):
        assert _cyc(p) == "L", f"{p} 应为长期趋势档 L"


def test_sub_minute_stays_S():
    """分钟级仍是短线档 —— 不得被日内迁档误伤。"""
    for p in ("1m", "3m", "5m"):
        assert _cyc(p) == "S", f"{p} 应为短线档 S"


def test_known_behavior_delta_week_and_month():
    """唯一的行为差异：1w/1M 此前落进 "S"（月线不是短线），现归 "L"。"""
    assert _cyc("1w") == "L"
    assert _cyc("1M") == "L"


def test_empty_and_unknown_fall_back_to_S():
    assert _cyc(None) == "S"
    assert _cyc("") == "S"
    assert _cyc("zzz") == "S"


def test_derives_from_single_source_of_truth():
    """必须真的从 cycle_semantics 派生，而不是复制一份映射。"""
    import inspect
    from backend.services.evolution import evolution_memory_v7 as M
    src = inspect.getsource(M._period_to_cycle)
    assert "cycle_semantics" in src, "未引用唯一真源"
    assert "period_to_cycle" in src, "未调用真源的 period_to_cycle"


def test_fail_safe_when_source_unavailable(monkeypatch):
    """真源不可用时必须退回历史词表，绝不打断学习链路。"""
    import builtins
    real_import = builtins.__import__

    def _boom(name, *a, **k):
        if "cycle_semantics" in str(name):
            raise ImportError("simulated")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _boom)
    assert _cyc("4h") == "L"
    assert _cyc("1h") == "M"
    assert _cyc("5m") == "S"


def test_v7_runner_default_periods_include_1h():
    """1h 此前没有调度 → V7 从不记录 1h 教训；现补入默认周期。"""
    from backend.services.evolution.evolution_v7_runner import DEFAULT_PERIODS, VALID_PERIODS
    assert "1h" in DEFAULT_PERIODS, "DEFAULT_PERIODS 未含 1h"
    assert "1h" in VALID_PERIODS
    for p in ("4h", "15m", "5m"):
        assert p in DEFAULT_PERIODS


def test_learning_and_factor_layers_agree_on_cycles():
    """三层必须对"哪些周期是日内/趋势"给出一致答案（此前三层互相冲突）。"""
    from backend.config.cycle_semantics import INTRADAY, TREND, period_to_cycle
    from backend.services.evolution.evolution_memory_v7 import _period_to_cycle as evo_cyc
    for p, want in (("1h", INTRADAY), ("15m", INTRADAY), ("4h", TREND), ("1d", TREND)):
        assert period_to_cycle(p) == want
        letter = evo_cyc(p)
        assert letter == ("M" if want == INTRADAY else "L"), f"{p} 两层不一致"
