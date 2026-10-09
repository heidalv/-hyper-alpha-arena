# -*- coding: utf-8 -*-
"""[F246 2026-09-16] 前端重置车道专属模拟账户：runner.reset_account 契约。

用户需求："模拟账户需要我在前端就能重置和指定金额"、"每车道单独账户、不共享"。
本测试锁三条硬契约：
  ① 内存持仓/挂单清零，但**标定数据保留**（mid_hist/vol_baseline —— 清掉会失明 ✗）；
  ② 挂单历史清空（F230：旧挂单不得再被延迟判定成交 ✗）；
  ③ DB 更新语句必须同时改账户行与分所行（否则前端又出现两个「可用」✗，F220）。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.market_maker import runner as mr  # noqa: E402
from backend.services import lane_registry as lr  # noqa: E402


class _NullCtx:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeDB:
    def __init__(self, calls):
        self.calls = calls

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def commit(self):
        pass


def _runner():
    r = mr.ShadowRunner(lane_id="t_f246", venue="asterdex", symbols=["BTC"],
                        equity=300.0, account_id=101, fill_notional=30.0)
    st = r.states["BTC"]
    st.qty = 1.5
    st.avg_px = 100.0
    st.avg_mid = 100.0
    st.opened_ts = time.time()
    st.quote_bid = 99.0
    st.quote_ask = 101.0
    st.quote_ts = time.time()
    st.toxic_streak = 5
    st.stop_since = 123.0
    st.mid_hist = [100.0] * 30
    st.vol_baseline_bp = 1.5
    r._quote_hist["BTC"] = [{"basis": 1.0, "bid": 99.0, "ask": 101.0,
                             "mid": 100.0, "ts": 1.0}]
    return r


def test_reset_clears_positions_keeps_calibration(monkeypatch):
    r = _runner()
    r.save_states = lambda: None
    calls = []
    monkeypatch.setattr("backend.core.tenant.system_identity", lambda: _NullCtx())
    monkeypatch.setattr("backend.database.connection.SessionLocal",
                        lambda: _FakeDB(calls))
    monkeypatch.setattr(lr, "get_lane", lambda lid: {"meta": {
        "shadow_equity": 300.0, "stats_since": "old", "ops_changes": []}})
    monkeypatch.setattr(lr, "update_meta", lambda lid, meta: True)

    res = r.reset_account(500.0)
    st = r.states["BTC"]
    assert res["ok"] and res["balance"] == 500.0 and res["equity"] == 500.0
    assert st.qty == 0 and st.avg_px == 0 and st.avg_mid == 0 and st.opened_ts == 0
    assert st.quote_bid == 0 and st.quote_ask == 0 and st.quote_ts == 0
    assert st.toxic_streak == 0 and st.stop_since == 0
    assert len(st.mid_hist) == 30 and st.vol_baseline_bp == 1.5, "标定数据必须保留"
    assert r._quote_hist == {}, "挂单历史必须清空（F230）"
    assert r.equity == 500.0
    # DB：账户行与分所行都要更新（F220：两个「可用」的教训）
    sqls = " | ".join(str(s) for s, _ in calls)
    assert "arbitrage_paper_accounts" in sqls
    assert "arbitrage_paper_exchange_balances" in sqls
    assert any(p and "b" in p and "ex" in p for _, p in calls), \
        "分所行必须按 venue 更新"


def test_reset_writes_reconcile_alignment(monkeypatch):
    """[F262] 重置后自动对账：运行态清零 ⇒ 写零盈亏校正行让账本重建同步归零，
    避免前端「账本与运行态分叉」幽灵警告（实测 SOL +0.568 = $55.41）。"""
    r = _runner()
    r.save_states = lambda: None
    monkeypatch.setattr("backend.core.tenant.system_identity", lambda: _NullCtx())
    monkeypatch.setattr("backend.database.connection.SessionLocal", lambda: _FakeDB([]))
    monkeypatch.setattr(lr, "get_lane", lambda lid: {"meta": {
        "shadow_equity": 300.0, "stats_since": "old", "ops_changes": []}})
    monkeypatch.setattr(lr, "update_meta", lambda lid, meta: True)
    from backend.services.market_maker import reconcile as rc
    monkeypatch.setattr(rc, "latest_marks", lambda syms, venue: {"BTC": 100.0})
    monkeypatch.setattr(rc, "scope_residues", lambda **kw: {"BTC": 1.5, "XRP": 0.0})
    written = {}

    def fake_record_fill(**kw):
        written.update(kw)
        return True
    monkeypatch.setattr("backend.services.lane_ledger.record_fill", fake_record_fill)

    res = r.reset_account(500.0)
    assert res["ok"]
    assert written.get("symbol") == "BTC", "残差币种必须写校正行"
    assert written.get("side") == "sell" and written.get("qty") == 1.5, \
        "方向=平掉残差（ledger 多 1.5 ⇒ sell）"
    assert written.get("fill_px") == written.get("mid_px") == 100.0, "零盈亏：fill=mid"
    assert written.get("fee_rate") == 0.0
    assert (written.get("meta") or {}).get("source") == "reconcile"
    assert (written.get("meta") or {}).get("reason") == "reset_align"
    assert written.get("ts") is not None, "ts=重置时刻（落在新时代内）"


def test_reset_rejects_invalid_balance(monkeypatch):
    r = _runner()
    try:
        r.reset_account(0.0)
        raise AssertionError("balance=0 必须拒绝")
    except ValueError:
        pass
    r2 = mr.ShadowRunner(lane_id="t2", venue="x", symbols=["BTC"],
                         equity=300.0, account_id=None, fill_notional=30.0)
    try:
        r2.reset_account(100.0)
        raise AssertionError("未绑定账户必须拒绝")
    except ValueError:
        pass
