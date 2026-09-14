# -*- coding: utf-8 -*-
"""[2026-09-14 F109] 冷启动必须补齐 `mid_hist`：滑动窗口类闸门不能"失明"。

缺陷（实测）：趋势闸 / 波动闸（σ 的输入）/ 冻结闸（决定挂宽档位 3bp↔w_base）全部读
`state.mid_hist`。进程重启后该窗口是空的（实测 20:30 实盘 186 条 vs 同刻回放 240 条）
⇒ 重启后头一小时这些闸门用**空/短窗口**运行 ⇒ 冻结闸在趋势行情下会误判"冻结"⇒ 挂
3bp 窄单 ⇒ 挂宽档位与回放分叉（F106/F107 已经证明挂宽直接决定成交数与净收益）。

契约：
  1. `ShadowRunner.backfill_mid_hist` 存在，且在 `get_runner` 里被调用；
  2. 每快照一条（与 tick 循环 F102 的追加口径一致），并按时间升序；
  3. `last_mid_src_ms` 锚到最后一条 ⇒ 下一 tick 不会重复追加同一条；
  4. 窗口已经足够时不覆盖（不打断正在积累的真实窗口）。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


class _FakeDB:
    """只应答 backfill 的那条 SQL（按 symbol 返回预置行）。"""

    def __init__(self, by_symbol, calls):
        self.by_symbol = by_symbol
        self.calls = calls

    def execute(self, sql, params=None):
        self.calls.append(dict(params or {}))
        sym = (params or {}).get("s")
        return _Rows(self.by_symbol.get(sym, []))

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _patch_db(monkeypatch, by_symbol, calls):
    import backend.database.connection as conn

    monkeypatch.setattr(conn, "MarketSessionLocal", lambda: _FakeDB(by_symbol, calls))
    import backend.core.tenant as ten

    monkeypatch.setattr(ten, "system_identity", lambda: __import__("contextlib")
                        .nullcontext())


def _runner():
    from backend.services.market_maker.runner import ShadowRunner

    return ShadowRunner(lane_id="test_f109", symbols=["BTC"], equity=300.0)


def test_backfill_fills_window_in_time_order_and_anchors_src(monkeypatch):
    calls = []
    # DESC 顺序返回（最新在前）——实现必须反转成时间升序
    rows = [{"timestamp": 1000 + i * 15000, "best_bid": 99.0 + i, "best_ask": 101.0 + i}
            for i in range(9, -1, -1)]
    _patch_db(monkeypatch, {"BTC": rows}, calls)

    r = _runner()
    n = r.backfill_mid_hist(keep=10)
    assert n == 1
    st = r.states["BTC"]
    assert len(st.mid_hist) == 10
    assert st.mid_hist[0] < st.mid_hist[-1], "必须按时间升序"
    assert abs(st.mid_hist[0] - 100.0) < 1e-9, "中价 = (bid+ask)/2"
    assert st.last_mid_src_ms == 1000 + 9 * 15000, "锚到最后一条快照标签"


def test_backfill_keeps_full_window_untouched(monkeypatch):
    calls = []
    _patch_db(monkeypatch, {"BTC": [{"timestamp": 1, "best_bid": 1.0, "best_ask": 3.0}]}, calls)
    r = _runner()
    r.states["BTC"].mid_hist = [float(i) for i in range(240)]
    r.states["BTC"].last_mid_src_ms = 12345
    assert r.backfill_mid_hist(keep=240) == 0, "窗口已满不得覆盖"
    assert r.states["BTC"].mid_hist[-1] == 239.0
    assert r.states["BTC"].last_mid_src_ms == 12345
    assert not calls, "窗口已满时不该查询 DB"


def test_backfill_is_wired_into_get_runner():
    """源码契约：get_runner 重建运行态后必须补齐窗口（否则重启即失明）。"""
    import inspect
    from backend.services.market_maker import runner as mmrunner

    _fn = mmrunner.get_runner
    src = inspect.getsource(_fn)
    assert "backfill_mid_hist(" in src, "get_runner 必须调用 backfill_mid_hist"
    i_load = src.index("r.load_states()")
    i_bf = src.index("backfill_mid_hist(")
    assert i_load < i_bf, "补齐必须发生在 load_states 之后"


def test_backfill_uses_snapshot_mid_one_per_row():
    """源码契约：每快照一条（与 F102 tick 追加口径一致）+ last_mid_src_ms 防重复。"""
    import inspect
    from backend.services.market_maker.runner import ShadowRunner

    src = inspect.getsource(ShadowRunner.backfill_mid_hist)
    assert "market_orderbook_snapshots" in src
    assert "best_bid>0 AND best_ask>best_bid" in src, "与实盘取值口径一致（过滤坏盘口）"
    assert "last_mid_src_ms" in src, "必须锚定来源快照，避免下一 tick 重复追加"
    assert "ORDER BY timestamp DESC" in src, "取最近 N 条"
