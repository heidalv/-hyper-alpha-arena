# -*- coding: utf-8 -*-
"""[F279 2026-09-16] 停机断点补齐契约（live↔model 行为一致性）：

缺陷：进程重启后 `mid_hist` 由运行态恢复（末尾停在停机前最后一 tick），而
`backfill_mid_hist`（F109）有 `len >= keep 就跳过` 的短路 ⇒ **不补**。于是重启后
第一条快照与停机前那条中价直接相邻 ⇒ `realized_vol_bp` 里出现一条跨越停机的
**伪收益** ⇒ `vol_regime_blocked` 把该币站开一整个窗口（15s × 20 期）⇒ 实盘报价
时间系统性少于模型（模型读连续快照、无伪收益）。

契约：
  · `mid_splice_on_gap=False`（默认）⇒ 行为与接线前**逐字一致**（可一键回退）；
  · True ⇒ 快照间隔 > 阈值时，把缺失快照补回 `mid_hist`（先补后追加）；
  · 补齐查不到数据 ⇒ 退化为**清空**（宁可冷启动，也不让伪收益进窗口 ✗）。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.market_maker import runner as mmrunner  # noqa: E402
from backend.services.market_maker.core import LaneRiskLimits, realized_vol_bp  # noqa: E402


def _runner(limits: LaneRiskLimits) -> mmrunner.ShadowRunner:
    r = mmrunner.ShadowRunner(lane_id="mm_asterdex", venue="asterdex",
                              symbols=["BTC"], limits=limits, params=None)
    return r


def _st(mids, last_ms: int) -> mmrunner.SymbolState:
    st = mmrunner.SymbolState(symbol="BTC")
    st.mid_hist = list(mids)
    st.last_mid_src_ms = int(last_ms)
    return st


def test_splice_is_off_by_default():
    """默认关闭：不得有任何补齐动作（旧行为逐字一致，可一键回退）。"""
    lim = LaneRiskLimits()
    assert getattr(lim, "mid_splice_on_gap", False) is False
    r = _runner(lim)
    st = _st([100.0, 101.0], 1_700_000_000_000)
    # 直接调补齐入口：关闭时**根本不会被调用**（由 tick 里的开关决定），
    # 这里锁定"开关默认关"这一事实即可（tick 侧见源码契约测试）。
    assert r.gap_splices == 0 and r.gap_splice_points == 0


def test_splice_fills_missing_points(monkeypatch):
    """补齐把停机期间的快照插回窗口 ⇒ 窗口里没有伪收益。"""
    r = _runner(LaneRiskLimits(mid_splice_on_gap=True))
    st = _st([100.0, 100.0], 1_700_000_000_000)
    # 伪造行情库返回：停机期间 3 个快照，价格 100.2 / 100.4 / 100.6（连续上升）
    monkeypatch.setattr(r, "_splice_mid_hist",
                        lambda st_, a, b: mmrunner.ShadowRunner._splice_mid_hist(
                            r, st_, a, b))
    import backend.database.connection as _dbc

    class _Rows:
        def mappings(self):
            return self

        def all(self):
            return [{"best_bid": 100.19, "best_ask": 100.21},
                    {"best_bid": 100.39, "best_ask": 100.41},
                    {"best_bid": 100.59, "best_ask": 100.61}]

    class _Db:
        def execute(self, *_a, **_k):
            return _Rows()

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    monkeypatch.setattr(_dbc, "MarketSessionLocal", lambda: _Db())
    pts = r._splice_mid_hist(st, 1_700_000_000_000, 1_700_000_300_000)
    assert pts == 3, pts
    assert len(st.mid_hist) == 5
    # 拼接后相邻收益都是"真实"小步（无跨停机大跳）
    rets = [abs(st.mid_hist[i] - st.mid_hist[i - 1]) / st.mid_hist[i - 1] * 1e4
            for i in range(1, len(st.mid_hist))]
    assert max(rets) < 30.0, rets


def test_splice_degrades_to_clear(monkeypatch):
    """补不到数据 ⇒ 清空窗口（冷启动），绝不让伪收益留在窗口里。"""
    r = _runner(LaneRiskLimits(mid_splice_on_gap=True))
    st = _st([100.0, 101.0], 1_700_000_000_000)
    import backend.database.connection as _dbc

    class _Rows:
        def mappings(self):
            return self

        def all(self):
            return []

    class _Db:
        def execute(self, *_a, **_k):
            return _Rows()

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    monkeypatch.setattr(_dbc, "MarketSessionLocal", lambda: _Db())
    pts = r._splice_mid_hist(st, 1_700_000_000_000, 1_700_000_300_000)
    assert pts == 0 and st.mid_hist == [], (pts, st.mid_hist)


def test_phantom_return_is_what_trips_vol_gate():
    """机理锁定：跨停机的那一条收益**就是**波动闸被触发的原因。

    同一段真实路径（合计 +22bp），两种口径：
      · 有断点（现行）：窗口里 100 → 100.22 相邻 ⇒ 20 步里 1 步 22bp；
      · 无断点（补齐）：同样 22bp 摊在 5 步上（每步 4.4bp）。
    阈值取实盘量级（基准 1.53bp × (1+0.7) = 2.60bp）。
    """
    base = 1.53
    thr = base * 1.7
    with_gap = [100.0] * 19 + [100.22]
    spliced = ([100.0] * 15
               + [100.044, 100.088, 100.132, 100.176, 100.22, 100.22])
    v_gap = realized_vol_bp(with_gap, 20)
    v_ok = realized_vol_bp(spliced, 20)
    assert v_gap > thr > v_ok, (v_gap, v_ok, thr)
