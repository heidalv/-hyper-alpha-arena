# -*- coding: utf-8 -*-
"""
[F38] 因子方向序列缓存行为回归测试（离线，不连 DB、不起服务）

背景：实测每周进化 8 个模板、每个模板开头都重跑一次 ~6.7h 的全量预计算
（整轮 ≈54h，而真正的适应度计算仅约 10min）。修复后缓存键含
(symbol, timeframe, 首时间戳, 长度)，配合 _load_bars 的按天对齐 cutoff，
跨模板可复用。

本测试用 stub 替换 _compute_factor_direction_windowed 以隔离因子数学，
只验证缓存键与复用/失效逻辑（不依赖 DB、不依赖真实因子）。
"""
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]  # backend/tests/unit/x.py -> repo root
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.services import live_pipeline_backtest_engine as eng  # noqa: E402
from backend.services.live_pipeline_backtest_engine import (  # noqa: E402
    Bar,
    LivePipelineBacktestEngine,
    _FACTOR_DIR_CACHE,
)

PARAMS = {"factor_signal_weight": 0.5}  # 保证进入预计算分支
WARMUP = 30


def _make_bars(n: int, ts0: int = 1_700_000_000, step: int = 3600):
    return [
        Bar(timestamp=ts0 + i * step, dt_str="", o=100.0, h=101.0, l=99.0,
            c=100.0 + (i % 7) * 0.1, v=10.0, idx=i)
        for i in range(n)
    ]


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """每个用例独立：清空缓存、替换窗口计算为计数器、恢复 TTL。"""
    calls = {"n": 0}

    def _stub(self, i, bars):
        calls["n"] += 1
        return 1.0 if i % 2 == 0 else -1.0

    monkeypatch.setattr(LivePipelineBacktestEngine, "_compute_factor_direction_windowed", _stub)
    _FACTOR_DIR_CACHE.clear()
    yield calls
    _FACTOR_DIR_CACHE.clear()


def _run(bars, symbol="BTC", timeframe="1h"):
    return LivePipelineBacktestEngine().run(bars, PARAMS, symbol=symbol, timeframe=timeframe)


def test_first_call_precomputes_full_series(_isolate):
    _run(_make_bars(100))
    assert _isolate["n"] == 100 - WARMUP
    assert len(_FACTOR_DIR_CACHE) == 1
    assert next(iter(_FACTOR_DIR_CACHE))[:2] == ("BTC", "1h")


def test_prefix_extension_only_computes_new_tail(_isolate):
    _run(_make_bars(100))
    _isolate["n"] = 0
    _run(_make_bars(110))
    assert _isolate["n"] == 10  # 只补算新增的 10 个窗口
    assert any(k[3] == 110 for k in _FACTOR_DIR_CACHE)


def test_different_symbol_does_not_reuse(_isolate):
    """旧键 (_ts0, len) 会让不同币种串用同一方向序列——必须不复用。"""
    bars = _make_bars(110)
    _run(bars, symbol="BTC")
    _isolate["n"] = 0
    _run(bars, symbol="ETH")
    assert _isolate["n"] == 110 - WARMUP


def test_different_timeframe_does_not_reuse(_isolate):
    bars = _make_bars(110)
    _run(bars, symbol="BTC", timeframe="1h")
    _isolate["n"] = 0
    _run(bars, symbol="BTC", timeframe="4h")
    assert _isolate["n"] == 110 - WARMUP


def test_same_key_reuses_without_recompute(_isolate):
    bars = _make_bars(110)
    _run(bars)
    _isolate["n"] = 0
    _run(bars)
    assert _isolate["n"] == 0


def test_ttl_expiry_forces_recompute(_isolate, monkeypatch):
    bars = _make_bars(110)
    _run(bars)
    monkeypatch.setattr(eng, "_FACTOR_DIR_TTL", 0)
    _isolate["n"] = 0
    _run(bars)
    assert _isolate["n"] == 110 - WARMUP


def test_ttl_default_covers_whole_round(_isolate):
    """默认 TTL 必须覆盖一轮进化（历史实测单模板 ≈6.7h），否则跨模板必 miss。"""
    assert eng._FACTOR_DIR_TTL >= 6 * 3600


def test_cache_write_time_is_fresh(_isolate):
    _run(_make_bars(100))
    written_at = next(iter(_FACTOR_DIR_CACHE.values()))[0]
    assert time.time() - written_at < 5
