# -*- coding: utf-8 -*-
"""[F349 2026-09-18] 回测**因子名级归因**：把"测试绿但链路死"的两个坑钉死。

背景（实跑实测，非推测）：B4 phase 2 初版 **单测全绿**，实跑却恒为空 `by_name={}`。
原因有两个，都属本仓库反复出现的那一类"写了不生效"：

1. **旁路被磁盘缓存绕过**：因子**方向**序列在 `data/factor_dir_cache/` 命中时，
   `run()` 的预计算循环整体空转（`_compute_from = len(bars)`），
   `_compute_factor_direction_windowed` 一次都不调用 ⇒ 旁路永远为空。
   且旧实现**没有任何日志**，看起来像"这套参数没有因子信息"。
2. **值类型过滤写错**：`compute_all_factors` 的值是 `FactorValue` 对象（`.value/.has_data`），
   不是 float；旧实现 `isinstance(v, (int, float))` 恒为 False ⇒ 过滤后必为空。
   单测直接喂 float dict，**没经过真实 compute_all_factors**，所以照绿不误。

本文件的断言全部走**真实路径形状**（FactorValue 形状的假对象 + 模拟缓存命中的空转），
而不是直接喂理想化的 float dict。
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.live_pipeline_backtest_engine import (  # noqa: E402
    LivePipelineBacktestEngine,
    attribute_trades_by_factor,
)


class _FV:
    """`FactorValue` 的最小形状替身（只保留归因用得到的字段）。"""

    def __init__(self, value, has_data=True, is_directional=True):
        self.value = value
        self.has_data = has_data
        self.is_directional = is_directional

    def __repr__(self):
        return f"_FV({self.value})"


class _Trade:
    def __init__(self, entry_bar, pnl, qty=1.0, px=100.0):
        self.entry_bar = entry_bar
        self.pnl = pnl
        self.quantity = qty
        self.entry_price = px


# ───────────── ① 值类型：FactorValue 必须能取到数，且不当的项要排除 ─────────────

def test_floats_of_unwraps_factorvalue_objects():
    """核心回归：值对象必须解出 `.value`（旧实现按 float 过滤 ⇒ 恒空）。"""
    raw = {"f_a": _FV(-0.0054), "f_b": _FV(0.0014), "f_c": 0.7}
    out = LivePipelineBacktestEngine._floats_of(raw)
    assert out == {"f_a": -0.0054, "f_b": 0.0014, "f_c": 0.7}


def test_floats_of_excludes_no_data_and_nonfinite():
    """`has_data=False` 不得算"活跃"（管线对它权重置 0，记成投票会虚增覆盖率）。"""
    raw = {
        "ok": _FV(0.1),
        "no_data": _FV(0.9, has_data=False),
        "nan": _FV(float("nan")),
        "inf": _FV(float("inf")),
        "junk": _FV("not-a-number"),
    }
    out = LivePipelineBacktestEngine._floats_of(raw)
    assert set(out) == {"ok"}, out
    assert all(math.isfinite(v) for v in out.values())


def test_floats_of_handles_empty():
    assert LivePipelineBacktestEngine._floats_of(None) == {}
    assert LivePipelineBacktestEngine._floats_of({}) == {}


# ───────────── ② 旁路补算：缓存命中也必须覆盖开仓 bar ─────────────

def test_ensure_attr_sidecar_backfills_entry_bars(monkeypatch):
    """模拟"方向序列来自缓存、预计算循环空转"⇒ 补算仍须覆盖全部开仓 bar。"""
    monkeypatch.setattr(
        "backend.services.live_pipeline_backtest_engine._FACTOR_ATTR_ENABLED", True)
    eng = LivePipelineBacktestEngine(initial_capital=10000)
    # 缓存命中的典型状态：方向序列已就绪、旁路为空
    eng._factor_dir_series = [0] * 100
    eng._factor_attr_sidecar = {}
    calls = []

    def _fake_values_at(i, bars):
        calls.append(i)
        return {"f_x": _FV(0.01 * (i % 3 - 1))}

    monkeypatch.setattr(eng, "_factor_values_at", _fake_values_at, raising=False)
    trades = [_Trade(entry_bar=40, pnl=1.0), _Trade(entry_bar=55, pnl=-2.0),
              _Trade(entry_bar=70, pnl=3.0)]
    stat = eng._ensure_attr_sidecar([None] * 100, trades)
    assert stat == {"needed": 3, "computed": 3, "covered": 3}, stat
    assert set(eng._factor_attr_sidecar) == {40, 55, 70}
    assert sorted(calls) == [40, 55, 70]


def test_ensure_attr_sidecar_reuses_existing_and_is_idempotent(monkeypatch):
    monkeypatch.setattr(
        "backend.services.live_pipeline_backtest_engine._FACTOR_ATTR_ENABLED", True)
    eng = LivePipelineBacktestEngine(initial_capital=10000)
    eng._factor_attr_sidecar = {40: {"f_x": 0.5}}
    n = {"v": 0}

    def _fake_values_at(i, bars):
        n["v"] += 1
        return {"f_x": 0.1}

    monkeypatch.setattr(eng, "_factor_values_at", _fake_values_at, raising=False)
    stat = eng._ensure_attr_sidecar([None] * 100, [_Trade(40, 1.0), _Trade(55, 1.0)])
    assert stat == {"needed": 2, "computed": 1, "covered": 2}, stat
    assert n["v"] == 1, "已覆盖的 bar 不得重复计算"


def test_ensure_attr_sidecar_disabled_is_noop(monkeypatch):
    monkeypatch.setattr(
        "backend.services.live_pipeline_backtest_engine._FACTOR_ATTR_ENABLED", False)
    eng = LivePipelineBacktestEngine(initial_capital=10000)

    def _boom(*a, **k):
        raise AssertionError("关闸时不得计算因子值")

    monkeypatch.setattr(eng, "_factor_values_at", _boom, raising=False)
    assert eng._ensure_attr_sidecar([None] * 100, [_Trade(40, 1.0)]) == {
        "needed": 0, "computed": 0, "covered": 0}


def test_ensure_attr_sidecar_logs_when_coverage_incomplete(monkeypatch, caplog):
    """覆盖率不足必须留日志（禁止静默退化）——旧实现静默给出 `{}`。"""
    import logging
    monkeypatch.setattr(
        "backend.services.live_pipeline_backtest_engine._FACTOR_ATTR_ENABLED", True)
    eng = LivePipelineBacktestEngine(initial_capital=10000)
    monkeypatch.setattr(eng, "_factor_values_at", lambda i, bars: None, raising=False)
    with caplog.at_level(logging.WARNING):
        stat = eng._ensure_attr_sidecar([None] * 100, [_Trade(40, 1.0)])
    assert stat["covered"] == 0 and stat["needed"] == 1
    assert any("归因覆盖率不足" in r.message for r in caplog.records)


# ───────────── ③ 端到端：补算 → 归因表非空且口径正确 ─────────────

def test_sidecar_then_attribution_produces_rows(monkeypatch):
    """补算出来的旁路必须能喂出**非空**归因表（旧链路在此恒为空）。"""
    monkeypatch.setattr(
        "backend.services.live_pipeline_backtest_engine._FACTOR_ATTR_ENABLED", True)
    eng = LivePipelineBacktestEngine(initial_capital=10000)
    vals = {40: {"f_up": _FV(0.5), "f_dn": _FV(-0.5)},
            55: {"f_up": _FV(-0.5), "f_dn": _FV(0.5)}}
    monkeypatch.setattr(eng, "_factor_values_at", lambda i, bars: vals.get(i), raising=False)
    trades = [_Trade(40, pnl=10.0), _Trade(55, pnl=-10.0)]
    stat = eng._ensure_attr_sidecar([None] * 100, trades)
    assert stat["covered"] == 2
    by_name = attribute_trades_by_factor(trades, eng._factor_attr_sidecar)
    assert set(by_name) == {"f_up", "f_dn"}
    assert by_name["f_up"]["n_long"] == 1 and by_name["f_up"]["n_short"] == 1
    assert by_name["f_up"]["coverage"] == 1.0
    # 覆盖度归因的口径：同因子多空两侧各 1 笔，pnl 相抵
    assert by_name["f_up"]["pnl_long"] == pytest.approx(10.0)
    assert by_name["f_up"]["pnl_short"] == pytest.approx(-10.0)


def test_attribution_field_names_are_the_documented_ones():
    """锁字段口径：没有 `n`/`win_rate`（读侧曾按这两个键打印 ⇒ 真数据也显示成 0/None）。"""
    by_name = attribute_trades_by_factor(
        [_Trade(1, pnl=5.0)], {1: {"f": 0.3}})
    row = by_name["f"]
    assert "n_long" in row and "n_short" in row and "coverage" in row
    assert "n" not in row and "win_rate" not in row


# ───────────── ④ 结果对象必须能报出覆盖率 ─────────────

def test_backtest_result_has_coverage_field():
    from backend.services.backtest_evolution_engine import BacktestResult
    r = BacktestResult(run_id="t")
    assert r.factor_attr_coverage == {}
    assert r.factor_attr_by_name == {} and r.factor_attr_by_dir == {}


# ───────────── ⑤ 归因必须是**只观察**：不得改变方向判定 ─────────────

def test_direction_path_receives_raw_objects_not_floats(monkeypatch):
    """`generate_signals` 必须拿到**原始 FactorValue dict**（含 has_data/is_directional）。

    这是 F349 修复时最容易踩坏的地方：我为了取 float 抽了 `_floats_of()`，
    若误把它接到方向路径上，`generate_signals` 收到纯 float 会拿不到
    `has_data/is_directional`（管线对"无数据/无方向"因子的处置依赖它们）⇒
    **每一轮回测的方向都会静默变化**。本用例把"方向路径吃原样对象"钉死。
    """
    captured = {}

    class _FakeGen:
        def generate_signals(self, factor_values):
            captured["arg"] = factor_values

            class _C:
                direction = 1.0
                strength = 1.0
            return _C()

    import backend.services.factor_engine as _fe
    monkeypatch.setattr(_fe, "FactorSignalGenerator", _FakeGen, raising=False)

    eng = LivePipelineBacktestEngine(initial_capital=10000)
    raw = {"f": _FV(0.42)}
    monkeypatch.setattr(eng, "_factor_values_at", lambda i, bars: raw, raising=False)
    monkeypatch.setattr(
        "backend.services.live_pipeline_backtest_engine._FACTOR_ATTR_ENABLED", True)
    assert eng._compute_factor_direction_windowed(50, [None] * 100) == 1
    assert captured["arg"] is raw, "方向路径必须吃原样 dict（不是 _floats_of 的结果）"
    assert isinstance(captured["arg"]["f"], _FV), "值必须是对象，不能被降级成 float"


def test_attr_disabled_does_not_write_sidecar(monkeypatch):
    """关闸时不得产生旁路（默认路径零副作用）。"""
    class _FakeGen:
        def generate_signals(self, factor_values):
            class _C:
                direction = -1.0
                strength = 1.0
            return _C()

    import backend.services.factor_engine as _fe
    monkeypatch.setattr(_fe, "FactorSignalGenerator", _FakeGen, raising=False)
    monkeypatch.setattr(
        "backend.services.live_pipeline_backtest_engine._FACTOR_ATTR_ENABLED", False)
    eng = LivePipelineBacktestEngine(initial_capital=10000)
    monkeypatch.setattr(eng, "_factor_values_at", lambda i, bars: {"f": _FV(0.42)},
                        raising=False)
    assert eng._compute_factor_direction_windowed(50, [None] * 100) == -1
    assert getattr(eng, "_factor_attr_sidecar", None) in (None, {})
