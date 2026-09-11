# -*- coding: utf-8 -*-
"""p2-oms-exec 单测：幂等键 / 状态机 / 对账比对 / bridge / ExecutionAlgo 影子路径。

全部用构造数据与 monkeypatch，不碰真交易所。重点覆盖会直接亏钱的语义：
  - 超时必须标 unknown，不能猜 filled/rejected
  - 终态不可静默改写（除非 reconcile=True）
  - 对账找不到证据 → expired，绝不是 filled
  - client_order_id 格式满足各家最严交集
"""
from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest


# ─────────────────────────── client_id ───────────────────────────
def test_client_order_id_is_alnum_and_short():
    from backend.services.oms.client_id import new_client_order_id

    cid = new_client_order_id(account_id=14)
    assert cid.isalnum(), f"含非法字符: {cid}"
    assert len(cid) <= 28
    assert cid.startswith("ha")


def test_client_order_ids_are_unique():
    from backend.services.oms.client_id import new_client_order_id

    ids = {new_client_order_id(account_id=1) for _ in range(50)}
    assert len(ids) == 50


def test_sanitize_gate_adds_prefix():
    from backend.services.oms.client_id import sanitize_for_exchange

    assert sanitize_for_exchange("ha14abc", "gateio").startswith("t-")
    assert sanitize_for_exchange("ha14abc", "binance") == "ha14abc"
    assert "-" not in sanitize_for_exchange("ha-14_ab.c", "okx")


def test_is_ours_recognizes_prefix():
    from backend.services.oms.client_id import is_ours, new_client_order_id

    assert is_ours(new_client_order_id())
    assert is_ours("t-ha14abcdef")
    assert not is_ours("manual_123")
    assert not is_ours(None)
    assert not is_ours("")


# ─────────────────────────── 状态机 ───────────────────────────
def test_allowed_transitions_matrix():
    from backend.services.oms.order_store import OrderStatus, _ALLOWED

    assert OrderStatus.SUBMITTED in _ALLOWED[OrderStatus.INTENT]
    assert OrderStatus.FILLED in _ALLOWED[OrderStatus.ACKED]
    assert OrderStatus.UNKNOWN in _ALLOWED[OrderStatus.SUBMITTED]
    # 终态默认不可迁出
    assert _ALLOWED[OrderStatus.FILLED] == frozenset()
    assert _ALLOWED[OrderStatus.REJECTED] == frozenset()


def test_transition_rejects_illegal_path(monkeypatch):
    """filled → cancelled 必须被拒，除非 reconcile=True。"""
    import backend.services.oms.order_store as S
    from backend.services.oms.order_store import OrderStatus

    class FakeRow:
        def __init__(self):
            self._d = ("filled", 1.0, None)

        def first(self):
            return self._d

    class FakeDB:
        def execute(self, *a, **kw):
            return FakeRow()

        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(S, "_db", lambda: FakeDB())
    monkeypatch.setattr(S, "ensure_schema", lambda: None)
    r = S.transition("cid1", OrderStatus.CANCELLED)
    assert r["ok"] is False
    assert "不允许" in r["reason"]


def test_transition_reconcile_can_override_terminal(monkeypatch):
    import backend.services.oms.order_store as S
    from backend.services.oms.order_store import OrderStatus

    calls: List[str] = []

    class FakeRow:
        def first(self):
            return ("filled", 1.0, None)

    class FakeDB:
        def execute(self, *a, **kw):
            calls.append(str(a[0]))
            return FakeRow()

        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(S, "_db", lambda: FakeDB())
    monkeypatch.setattr(S, "ensure_schema", lambda: None)
    r = S.transition("cid1", OrderStatus.CANCELLED, reconcile=True)
    assert r["ok"] is True
    assert any("UPDATE" in c.upper() for c in calls)


def test_record_intent_failure_must_block_send(monkeypatch):
    """intent 落库失败 → 返回 False；调用方绝不能继续发单。"""
    import backend.services.oms.order_store as S

    class BoomDB:
        def execute(self, *a, **kw):
            raise RuntimeError("db down")

        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(S, "_db", lambda: BoomDB())
    monkeypatch.setattr(S, "ensure_schema", lambda: None)
    assert S.record_intent(
        client_order_id="x", account_id=1, exchange="binance",
        symbol="BTC", side="buy", order_type="market", qty=1.0,
    ) is False


# ─────────────────────────── 对账比对 ───────────────────────────
def test_compare_detects_status_mismatch():
    from backend.services.oms.reconcile import compare

    local = [{"client_order_id": "ha1", "exchange_order_id": "99",
              "status": "submitted", "filled_qty": 0, "qty": 1, "symbol": "BTC"}]
    ex = [{"client_order_id": "ha1", "exchange_order_id": "99",
           "status": "closed", "raw_status": "closed", "filled": 1.0, "qty": 1.0,
           "avg_price": 100.0, "symbol": "BTC", "side": "buy"}]
    diff = compare(local, ex)
    assert diff["n_matched"] == 0
    assert diff["n_mismatch"] == 1
    assert diff["mismatches"][0]["kind"] == "status_mismatch"
    assert diff["mismatches"][0]["exchange_status"] == "filled"


def test_compare_local_only_and_orphan():
    from backend.services.oms.reconcile import compare

    local = [{"client_order_id": "ha1", "exchange_order_id": None,
              "status": "submitted", "filled_qty": 0, "qty": 1, "symbol": "BTC",
              "updated_ms": 1, "created_ms": 1}]
    ex = [{"client_order_id": "manual_xyz", "exchange_order_id": "77",
           "status": "closed", "raw_status": "closed", "filled": 1, "qty": 1,
           "avg_price": 1, "symbol": "ETH", "side": "sell"}]
    diff = compare(local, ex)
    assert diff["n_mismatch"] == 1 and diff["mismatches"][0]["kind"] == "local_only"
    assert diff["n_orphan_foreign"] == 1
    assert diff["orphans"][0]["ours"] is False


def test_apply_fixes_expires_stale_unconfirmed(monkeypatch):
    """本地 submitted、交易所找不到、超过宽限期 → expired（不是 filled）。"""
    import backend.services.oms.reconcile as R

    forced: List[tuple] = []

    def fake_transition(cid, to, **kw):
        forced.append((cid, to, kw.get("reconcile")))
        return {"ok": True}

    monkeypatch.setattr(R, "transition", fake_transition)
    monkeypatch.setattr(R, "get_order", lambda cid: {"status": "submitted", "client_order_id": cid})
    monkeypatch.setattr(R, "unknown_grace_sec", lambda: 60)

    diff = {"mismatches": [{
        "kind": "local_only", "client_order_id": "ha1",
        "local_status": "submitted", "age_sec": 3600,
    }], "orphans": []}
    out = R.apply_fixes(diff, [], auto_fix=True)
    assert forced == [("ha1", "expired", True)]
    assert out["actions"][0]["action"] == "expire_unconfirmed"


def test_apply_fixes_does_not_fill_without_exchange_evidence(monkeypatch):
    """即使 auto_fix，没有交易所证据也绝不能写成 filled。"""
    import backend.services.oms.reconcile as R

    forced: List[str] = []
    monkeypatch.setattr(R, "transition",
                        lambda cid, to, **kw: forced.append(to) or {"ok": True})
    monkeypatch.setattr(R, "get_order", lambda cid: {"status": "unknown"})
    monkeypatch.setattr(R, "unknown_grace_sec", lambda: 10)

    diff = {"mismatches": [{
        "kind": "local_only", "client_order_id": "ha1",
        "local_status": "unknown", "age_sec": 100,
    }], "orphans": []}
    R.apply_fixes(diff, [], auto_fix=True)
    assert "filled" not in forced
    assert forced == ["expired"]


# ─────────────────────────── bridge ───────────────────────────
def test_bridge_attach_and_begin(monkeypatch):
    from backend.services.exchange.base_exchange_client import ExchangeOrder, OrderSide, OrderType
    import backend.services.oms.bridge as B

    monkeypatch.setattr(B, "record_orders_enabled", lambda: True)
    recorded: Dict[str, Any] = {}

    monkeypatch.setattr("backend.services.oms.order_store.record_intent",
                        lambda **kw: recorded.update(kw) or True)
    monkeypatch.setattr("backend.services.oms.order_store.transition",
                        lambda *a, **kw: {"ok": True})
    monkeypatch.setattr("backend.services.oms.order_store.ensure_schema", lambda: None)

    # 直接 patch bridge 里会 import 的路径
    import backend.services.oms.order_store as OS
    monkeypatch.setattr(OS, "record_intent", lambda **kw: recorded.update(kw) or True)
    monkeypatch.setattr(OS, "transition", lambda *a, **kw: {"ok": True})

    o = ExchangeOrder(order_id="", symbol="BTC", side=OrderSide.BUY,
                      order_type=OrderType.MARKET, size=0.01)
    cid = B.begin_order(o, account_id=7, exchange="binance")
    assert cid and o.client_order_id == cid
    assert recorded.get("account_id") == 7
    assert recorded.get("status") is None  # intent 写入时 status 固定为 intent


def test_bridge_finish_timeout_path_is_rejected_or_unknown(monkeypatch):
    import backend.services.oms.bridge as B

    monkeypatch.setattr(B, "record_orders_enabled", lambda: True)
    seen: List[str] = []

    import backend.services.oms.order_store as OS
    monkeypatch.setattr(OS, "transition",
                        lambda cid, to, **kw: seen.append(to) or {"ok": True})

    B.finish_order("ha1", None, error="boom")
    assert seen == ["rejected"]

    seen.clear()
    B.finish_order("ha1", {"status": "filled", "filled": 1.0, "id": "99", "average": 100})
    assert "acked" in seen and "filled" in seen


def test_try_algo_place_returns_none_when_disabled(monkeypatch):
    import backend.services.oms.bridge as B

    monkeypatch.setattr("backend.services.oms.execution_algo.algo_enabled", lambda: False)
    o = MagicMock(side=MagicMock(value="buy"), size=1, symbol="BTC",
                  order_type=MagicMock(value="market"), price=None,
                  reduce_only=False, leverage=1, position_side=None,
                  tp=None, sl=None, client_order_id=None)
    assert B.try_algo_place(MagicMock(), o, account_id=1, exchange="binance") is None


# ─────────────────────────── ExecutionAlgo 影子 ───────────────────────────
def test_shadow_execute_never_calls_place_order(monkeypatch):
    import asyncio

    import backend.services.oms.execution_algo as A

    monkeypatch.setattr(A, "shadow_mode", lambda: True)
    monkeypatch.setattr(A, "record_intent", lambda **kw: True)
    transitions: List[str] = []
    monkeypatch.setattr(A, "transition",
                        lambda cid, to, **kw: transitions.append(to) or {"ok": True})

    client = MagicMock()
    client.place_order = MagicMock(side_effect=AssertionError("影子模式不该发单"))
    client.get_orderbook = MagicMock(return_value={"bids": [[100, 1]], "asks": [[101, 1]]})
    client._swap_symbol = lambda s: s

    req = A.AlgoRequest(account_id=1, exchange="binance", symbol="BTC",
                        side="buy", qty=0.01)
    res = asyncio.run(A.execute(client, req))
    assert res.ok and res.shadow and res.status == "filled"
    assert client.place_order.call_count == 0
    assert "submitted" in transitions and "filled" in transitions


def test_algo_config_fallback_clamped():
    from backend.services.oms.execution_algo import AlgoConfig

    cfg = AlgoConfig.from_env({"fallback": "nope", "max_chases": 99})
    assert cfg.fallback == "market"
    assert cfg.max_chases == 10


def test_extract_fill_normalizes_variants():
    from backend.services.oms.execution_algo import _extract_fill

    r = _extract_fill({"status": "closed", "id": "42", "filled": 1.5, "average": 10})
    assert r["oid"] == "42" and r["filled"] == 1.5 and r["avg"] == 10
    r2 = _extract_fill({"status": "error", "message": "nope"})
    assert r2["status"] == "error"


# ─────────────────────────── ExchangeOrder 字段 ───────────────────────────
def test_exchange_order_has_client_order_id_field():
    from backend.services.exchange.base_exchange_client import ExchangeOrder, OrderSide, OrderType

    o = ExchangeOrder(order_id="x", symbol="BTC", side=OrderSide.BUY,
                      order_type=OrderType.MARKET, size=1, client_order_id="haabc")
    assert o.client_order_id == "haabc"
    o2 = ExchangeOrder(order_id="x", symbol="BTC", side=OrderSide.BUY,
                       order_type=OrderType.MARKET, size=1)
    assert o2.client_order_id is None


# ─────────────────────────── 任务注册 ───────────────────────────
def test_oms_jobs_register():
    from backend.services.oms.jobs import register_oms_jobs

    jobs = []

    class FakeSched:
        def add_interval_task(self, **kw):
            jobs.append(("interval", kw.get("task_id")))

        def add_cron_task(self, **kw):
            jobs.append(("cron", kw.get("task_id")))

    out = register_oms_jobs(FakeSched(), lambda n, f: f, lambda *a, **kw: None)
    assert "oms_stuck_scan" in out and "oms_daily_reconcile" in out
    assert any(j[1] == "v3_oms_stuck_scan" for j in jobs)
