# -*- coding: utf-8 -*-
"""M2 异常检测的单元测试（纯函数，无需 DB）。

判据必须**确定性、可复现、可单测** —— 这正是不用 LLM 做检测的原因。
本测试固定 2026-09-21 回放验收得出的结论：
  · A1 默认**关闭**（宽判据误报、紧判据漏报）
  · A2 是**主力检测器**（回放里在 14:20 命中 SOL，正是那波行情起点）
  · A4 阈值提到 5.0（3.0 时早期误报多）
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]   # backend/tests/unit/x.py -> 仓库根
SCRIPT = ROOT / "scripts" / "m1m2_monitor.py"


def _load():
    spec = importlib.util.spec_from_file_location("m1m2_under_test", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    saved = sys.stdout
    try:
        spec.loader.exec_module(m)
    finally:
        sys.stdout = saved
    return m


M = _load()


def _snap(**kw) -> dict:
    base = {
        "windows": {10: {"fills": 30, "taker_fills": 0, "worst_fill_usd": 0.0},
                    30: {"fills": 120, "taker_fills": 1, "worst_fill_usd": 0.5},
                    60: {"fills": 240, "taker_fills": 2, "worst_fill_usd": 0.5}},
        "series_10m": [-0.1, -0.2, -0.3],
        "baseline": {"net_bp_per_fill": 0.1, "p95_abs_fill_usd": 1.0,
                     "flatten_rate": 0.10, "fills": 500},
        "p25_spread_bp": 1.5,
        "legs": 0.5,
        "legs_cap": 2.0,
        "quote_ratio": 1.0,
        "quote_ratio_base": 1.0,
    }
    base.update(kw)
    return base


@pytest.mark.unit
def test_clean_snapshot_has_no_flags():
    """正常快照不得有任何标记（无标记 ⇒ 不调 LLM，这是成本与噪声控制的关键）。"""
    assert M.detect_flags(_snap()) == []


@pytest.mark.unit
def test_a1_is_disabled_after_replay():
    """A1 默认关闭 —— 回放验收结论：宽判据误报（XRP 13:40）、紧判据漏报（全 0）。"""
    assert M.A1_ENABLED is False
    # 即使构造"完美恶化"序列，也不应出 A1（因为关闭）
    s = _snap(series_10m=[-4.0, -5.0, -6.0])
    assert "A1" not in M.detect_flags(s)


@pytest.mark.unit
def test_a1_logic_still_works_when_enabled(monkeypatch):
    """A1 的逻辑本身要正确（留着待验证更长窗口），开启后应能命中。

    ⚠️ 必须打 `detect_flags.__globals__`：函数读的是**它自己模块的全局变量**，
    而不是 `importlib` 载入后我们拿到的那个命名空间 `M`
    （`M.A1_ENABLED = True` 不会影响函数内部的查找）。
    """
    monkeypatch.setitem(M.detect_flags.__globals__, "A1_ENABLED", True)
    # 单调下降 + 末窗 < −2 + **累计降幅 ≥ 3** + 首窗 ≤ 0 ⇒ 命中
    # ⚠️ 注意累计降幅是 `首窗 − 末窗`，不是"末窗的绝对值"：
    #    [-2, -4, -6] ⇒ 降幅 4.0 ✓  ；而 [-4, -5, -6] ⇒ 降幅只有 2.0 ✗
    #    （第一版测试用了后者，断言失败 —— 是**测试算错**，不是代码错。）
    assert "A1" in M.detect_flags(_snap(series_10m=[-2.0, -4.0, -6.0]))
    # 首窗为正（先涨后跌的噪声）⇒ 不命中
    assert "A1" not in M.detect_flags(_snap(series_10m=[+1.0, -5.0, -6.0]))
    # 累计降幅不足（4→5→6 只降 2.0）⇒ 不命中
    assert "A1" not in M.detect_flags(_snap(series_10m=[-4.0, -5.0, -6.0]))
    # 末窗未达 −2 ⇒ 不命中
    assert "A1" not in M.detect_flags(_snap(series_10m=[0.0, -1.0, -1.5]))


@pytest.mark.unit
def test_a2_fires_on_flatten_spike():
    """A2 是主力检测器：近 30min 付费成交占比 > 全时代强平率 × 2 ⇒ 命中。"""
    # 基线强平率 10% ⇒ 阈值 20%；窗口 120 笔里 40 笔付费 = 33% ⇒ 命中
    s = _snap(windows={10: {"fills": 30, "taker_fills": 10, "worst_fill_usd": 0.0},
                       30: {"fills": 120, "taker_fills": 40, "worst_fill_usd": 0.5},
                       60: {"fills": 240, "taker_fills": 50, "worst_fill_usd": 0.5}})
    assert "A2" in M.detect_flags(s)


@pytest.mark.unit
def test_a2_requires_min_cycles():
    """样本不足（< A2_MIN_CYCLES）不得判 A2 —— 样本不足不判。"""
    s = _snap(windows={10: {"fills": 2, "taker_fills": 2, "worst_fill_usd": 0.0},
                       30: {"fills": 3, "taker_fills": 3, "worst_fill_usd": 0.5},
                       60: {"fills": 5, "taker_fills": 3, "worst_fill_usd": 0.5}})
    assert "A2" not in M.detect_flags(s)


@pytest.mark.unit
def test_a3_fires_when_fill_ratio_collapses():
    s = _snap(quote_ratio=0.3, quote_ratio_base=1.0)
    assert "A3" in M.detect_flags(s)


@pytest.mark.unit
def test_a4_threshold_is_five_times_p95():
    """A4 阈值 5.0（回放里 3.0 早期误报多）。"""
    assert M.A4_P95_MULT == 5.0
    # p95 = $1.0 ⇒ 阈值 $5.0；$6 命中
    s = _snap(windows={10: {"fills": 30, "taker_fills": 0, "worst_fill_usd": 0.0},
                       30: {"fills": 120, "taker_fills": 1, "worst_fill_usd": 6.0},
                       60: {"fills": 240, "taker_fills": 2, "worst_fill_usd": 6.0}})
    assert "A4" in M.detect_flags(s)
    # $4 不命中（3.0 阈值时会误报）
    s2 = _snap(windows={10: {"fills": 30, "taker_fills": 0, "worst_fill_usd": 0.0},
                        30: {"fills": 120, "taker_fills": 1, "worst_fill_usd": 4.0},
                        60: {"fills": 240, "taker_fills": 2, "worst_fill_usd": 4.0}})
    assert "A4" not in M.detect_flags(s2)


@pytest.mark.unit
def test_a5_fires_on_spread_drift_both_sides():
    """结构漂移：太窄赚不到、太宽被穿，两侧都要判。"""
    assert "A5" in M.detect_flags(_snap(p25_spread_bp=0.5))
    assert "A5" in M.detect_flags(_snap(p25_spread_bp=6.0))
    assert "A5" not in M.detect_flags(_snap(p25_spread_bp=1.5))
    # 缺数据不判（不能凭缺失下结论）
    assert "A5" not in M.detect_flags(_snap(p25_spread_bp=None))


@pytest.mark.unit
def test_a6_fires_near_position_cap():
    """持仓堆积：腿数 ≥ 上限 × 0.8 ⇒ 命中（H179 的方向性敞口教训）。"""
    assert "A6" in M.detect_flags(_snap(legs=1.7, legs_cap=2.0))
    assert "A6" not in M.detect_flags(_snap(legs=1.0, legs_cap=2.0))


@pytest.mark.unit
def test_flags_can_combine():
    """多条标记可同时命中（回放里 SOL 15:20 是 A2+A4）。"""
    s = _snap(p25_spread_bp=0.5, legs=1.9, legs_cap=2.0, quote_ratio=0.1,
              quote_ratio_base=1.0)
    fl = M.detect_flags(s)
    assert "A5" in fl and "A6" in fl and "A3" in fl
