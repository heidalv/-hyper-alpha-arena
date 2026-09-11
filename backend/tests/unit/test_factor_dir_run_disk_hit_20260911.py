# -*- coding: utf-8 -*-
"""[F38e] engine.run() 磁盘缓存命中分支契约测试（合成数据，不跑真因子引擎）。

用 monkeypatch 替换 _compute_factor_direction_windowed 为确定性函数，
预置磁盘缓存后跑 run()，验证：命中 → 只补算未覆盖尾部；并写回合并缓存。
"""
import os
import time

import pytest

import backend.services.live_pipeline_backtest_engine as eng
from backend.services.live_pipeline_backtest_engine import Bar, LivePipelineBacktestEngine


def _bars(n, ts0=1700000000, step=3600):
    out = []
    for i in range(n):
        out.append(Bar(
            timestamp=ts0 + i * step, dt_str="", o=100.0, h=101.0, l=99.0,
            c=100.5, v=1000.0, idx=i,
        ))
    return out


@pytest.fixture()
def _env(monkeypatch, tmp_path):
    monkeypatch.setattr(eng, "_FACTOR_DIR_DISK_DIR", tmp_path)
    monkeypatch.setattr(eng, "_FACTOR_DIR_DISK_ENABLED", True)
    monkeypatch.setattr(eng, "_FACTOR_DIR_CACHE", {})
    return tmp_path


def test_run_hits_disk_and_computes_only_tail(monkeypatch, tmp_path, _env):
    n = 120
    bars = _bars(n)
    ts0 = bars[0].timestamp
    # 预置磁盘缓存：锚定同一 ts0，已覆盖前 60 根（warmup=30 → 有效值 30..59）
    cached_series = [0] * 30 + [1] * 30
    cached_tss = [b.timestamp for b in bars[:60]]
    eng._factor_dir_disk_save("TST", "4h", cached_tss, cached_series)

    calls = []
    monkeypatch.setattr(
        eng.LivePipelineBacktestEngine,
        "_compute_factor_direction_windowed",
        lambda self, i, bs: (calls.append(i), -1)[1],
    )
    # 关闭真因子权重路径之外的慢计算：只测预计算分支，跑 3 根让主循环快速过
    engine = LivePipelineBacktestEngine(initial_capital=10000)
    params = dict(eng.DEFAULT_PIPELINE_PARAMS) if hasattr(eng, "DEFAULT_PIPELINE_PARAMS") else {}
    params.update({
        "factor_signal_weight": 0.3,
        "max_trades_per_day": 0,      # 禁止开仓 → 主循环快速空跑
        "position_size_pct": 0.0,
    })
    engine.run(bars, params, tier="mid", symbol="TST", timeframe="4h")
    # 期望：只补算了未覆盖尾部（60..119），磁盘已覆盖的 30..59 直接复用
    assert calls == list(range(60, n))
    # 缓存已写回合并（tss 长度 = bars 长度）
    got = eng._factor_dir_disk_load("TST", "4h")
    assert got is not None
    gtss, gseries = got
    assert len(gtss) == n and len(gseries) == n
    assert gseries[35] == 1   # 磁盘覆盖段保留原值
    assert gseries[70] == -1  # 尾部新算值


def test_run_full_compute_saves_disk(monkeypatch, tmp_path, _env):
    n = 120
    bars = _bars(n)
    monkeypatch.setattr(
        eng.LivePipelineBacktestEngine,
        "_compute_factor_direction_windowed",
        lambda self, i, bs: 1,
    )
    engine = LivePipelineBacktestEngine(initial_capital=10000)
    params = dict(eng.DEFAULT_PIPELINE_PARAMS) if hasattr(eng, "DEFAULT_PIPELINE_PARAMS") else {}
    params.update({"factor_signal_weight": 0.3, "max_trades_per_day": 0, "position_size_pct": 0.0})
    engine.run(bars, params, tier="mid", symbol="TST2", timeframe="4h")
    got = eng._factor_dir_disk_load("TST2", "4h")
    assert got is not None
    gtss, gseries = got
    assert len(gtss) == n
    assert all(v == 1 for v in gseries[30:])
