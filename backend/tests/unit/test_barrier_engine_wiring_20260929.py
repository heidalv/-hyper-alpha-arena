# -*- coding: utf-8 -*-
"""P1 引擎接线集成测试 2026-09-29：`_run_barrier_ladder_layer` 全链路（stub 引擎，无 DB）。

覆盖：初始化 SL（k×ATR）/ 存量仓沿用现有 SL / TP1 锁本 50% / TP2 放利 30% + Chandelier 线同步 /
Chandelier 全平 / 时间止损 / 状态持久化到 exit_state_json / 执行被拒时状态回滚。
"""
import json
import types

import pytest

from backend.services.paper_trading_engine import PaperTradingEngine


class _FakeDB:
    def commit(self):
        pass

    def rollback(self):
        pass


def _engine(monkeypatch, reject_once=False):
    eng = PaperTradingEngine.__new__(PaperTradingEngine)
    monkeypatch.setattr(eng, "_resolve_atr_pct", lambda pos, entry, price: 0.02)
    monkeypatch.setattr(eng, "safe_sl_price", lambda sl, side, market, entry: sl)
    calls = []
    state = {"reject_next": reject_once}

    def _close(self, db, account_id, symbol, side, reason="manual", quantity=None,
               strategy_id=None, fill_price_override=None, trigger_order_id=None,
               position_id=None, trade_nature=None):
        calls.append(dict(
            reason=reason, quantity=quantity, px=fill_price_override, position_id=position_id))
        if state["reject_next"]:
            state["reject_next"] = False
            return None                    # 模拟 exit_arbiter 让路 / minNotional 拒绝
        return dict(closed_fully=(quantity is None))

    monkeypatch.setattr(eng, "close_position", types.MethodType(_close, eng))
    return eng, calls


def _pos(**kw):
    import time as _t
    from datetime import datetime, timezone
    d = dict(
        id=1, account_id=14, symbol="BTC", side="long", entry_price=100.0,
        size=2.0, sl_price=0.0, tp_price=None, exit_state_json=None,
        strategy_id="s1", opened_at=datetime.now(timezone.utc),
        mark_price=100.0,
    )
    d.update(kw)
    p = types.SimpleNamespace(**d)
    es = d.get("es")
    p.exit_state_json = json.dumps(es, ensure_ascii=False) if es is not None else None
    return p


def test_init_and_lock_and_tp2_and_chand(monkeypatch):
    eng, calls = _engine(monkeypatch)
    db = _FakeDB()
    # 新仓（opened_at=now → _fresh=True）：k×ATR(2%)=2% SL → 98
    pos = _pos()
    es = {}
    r = eng._run_barrier_ladder_layer(db, pos, 100.0, 100.0, es, 0.0)
    assert r is False
    assert pos.sl_price == pytest.approx(98.0)
    st = json.loads(pos.exit_state_json)["barrier_ladder"]
    assert st["sl"] == pytest.approx(98.0) and st["stage"] == 0
    # TP1（102）触发：平 50% + 锁本
    r2 = eng._run_barrier_ladder_layer(db, pos, 100.0, 102.5, es, 3600.0)
    assert r2 is False
    assert calls[-1]["reason"] == "barrier:tp1"
    assert calls[-1]["quantity"] == pytest.approx(1.0)      # 50% of size 2.0
    assert calls[-1]["px"] == pytest.approx(102.0)
    assert pos.sl_price == pytest.approx(100.0)            # 锁本
    st = json.loads(pos.exit_state_json)["barrier_ladder"]
    assert st["stage"] == 1
    # 真实 close_position 会原地减 size：模拟之
    pos.size = 1.0
    # TP2（104）触发：平剩余 1.0 的 30% = 0.3
    r3 = eng._run_barrier_ladder_layer(db, pos, 100.0, 104.5, es, 7200.0)
    assert r3 is False
    assert calls[-1]["reason"] == "barrier:tp2"
    assert calls[-1]["quantity"] == pytest.approx(0.3)
    # 阶段2：Chandelier 线同步（peak=104.5，ATR=2 → trail=100.5）
    assert pos.sl_price == pytest.approx(100.5)
    # 回落到 trail 之下 → chand 全平（余量 0.7）
    r4 = eng._run_barrier_ladder_layer(db, pos, 100.0, 100.4, es, 10800.0)
    assert r4 is True
    assert calls[-1]["reason"] == "barrier:chand"
    assert calls[-1]["quantity"] is None                    # 全平
    assert calls[-1]["px"] == pytest.approx(100.5)


def test_time_stop(monkeypatch):
    eng, calls = _engine(monkeypatch)
    db = _FakeDB()
    pos = _pos()
    es = {}
    eng._run_barrier_ladder_layer(db, pos, 100.0, 100.0, es, 0.0)   # init
    r = eng._run_barrier_ladder_layer(db, pos, 100.0, 100.2, es, 25 * 3600.0)
    assert r is True
    assert calls[-1]["reason"] == "barrier:time"
    assert calls[-1]["quantity"] is None


def test_existing_position_keeps_old_sl(monkeypatch):
    from datetime import datetime, timedelta, timezone
    eng, calls = _engine(monkeypatch)
    db = _FakeDB()
    # 存量仓：开仓在模块装载前（opened_at 远早于现在）
    pos = _pos(sl_price=97.0, opened_at=datetime.now(timezone.utc) - timedelta(days=30))
    es = {}
    eng._run_barrier_ladder_layer(db, pos, 100.0, 100.0, es, 0.0)
    assert pos.sl_price == pytest.approx(97.0)   # 不移动存量止损
    st = json.loads(pos.exit_state_json)["barrier_ladder"]
    assert st["sl"] == pytest.approx(97.0)


def test_short_mirror_lock(monkeypatch):
    eng, calls = _engine(monkeypatch)
    db = _FakeDB()
    pos = _pos(side="short")
    es = {}
    eng._run_barrier_ladder_layer(db, pos, 100.0, 100.0, es, 0.0)
    assert pos.sl_price == pytest.approx(102.0)   # short SL 在上方
    # 跌到 TP1（98）→ 平 50% + 锁本（SL 下移到 entry）
    eng._run_barrier_ladder_layer(db, pos, 100.0, 97.5, es, 3600.0)
    assert calls[-1]["reason"] == "barrier:tp1"
    assert calls[-1]["px"] == pytest.approx(98.0)
    assert pos.sl_price == pytest.approx(100.0)


def test_rejected_fill_rolls_back_state(monkeypatch):
    """exit_arbiter 让路（close_position 返回 None）时，障碍状态不得被消费。"""
    eng, calls = _engine(monkeypatch, reject_once=True)
    db = _FakeDB()
    pos = _pos()
    es = {}
    eng._run_barrier_ladder_layer(db, pos, 100.0, 100.0, es, 0.0)   # init（stage 0）
    # TP1 触发但被拒绝 → 状态回滚到 stage 0，仓位未锁本
    r = eng._run_barrier_ladder_layer(db, pos, 100.0, 102.5, es, 3600.0)
    assert r is False
    st = json.loads(pos.exit_state_json)["barrier_ladder"]
    assert st["stage"] == 0 and st["qty"] == pytest.approx(1.0)
    assert pos.sl_price == pytest.approx(98.0)          # 仍是初始 SL，未锁本
    # 下一 tick 重试成功
    r2 = eng._run_barrier_ladder_layer(db, pos, 100.0, 102.5, es, 3700.0)
    assert r2 is False
    assert calls[-1]["reason"] == "barrier:tp1"
    st2 = json.loads(pos.exit_state_json)["barrier_ladder"]
    assert st2["stage"] == 1
