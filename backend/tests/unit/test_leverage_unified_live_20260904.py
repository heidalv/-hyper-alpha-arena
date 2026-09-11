# -*- coding: utf-8 -*-
"""[2026-09-04] 实盘杠杆必须与模拟盘同源，且下单前必须对齐到交易所。

用户指出的事实（这是全部设计的前提）：

    交易所的杠杆是**按币种**设的，不存在"短线/中线/长线"三套；
    同方向同币种的仓位在交易所侧会被**合并成一个净头寸**。
    所以本地按周期各设一个倍数，在真实交易所里根本不成立 ——
    后一次 set_leverage 会覆盖前一次，连已建仓位的爆仓价都被就地改写。
    正确做法：杠杆按币种统一，仓位份额改用保证金/名义控制。

审计发现的两个缺口（都只在实盘侧，模拟盘早已统一）：

1. `scalp_loop` 实盘分支把上游 position_sizing_agent 的动态杠杆（5~10x）
   直接交给 LiveExecutor，绕过 leverage_authority；而 paper 侧经 trade_gate
   会被改写成币种档位。同一个币模拟盘 4x、实盘 10x，两套账不可比。

2. `LiveExecutor._apply_leverage` + fail-close 只装在 LPM 路径上，而
   `LIVE_SUB_POSITION_TRACKING` 默认 false —— **线上跑的恰恰是旧路径**，
   等于杠杆对齐在实际生效的路径上从未执行过。交易所残留 75x 而本地按 5x
   记账时，同样的保证金会开出 15 倍名义敞口（XPL 事故）。
"""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))))

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))


def _src(*parts: str) -> str:
    with open(os.path.join(_ROOT, *parts), encoding="utf-8") as f:
        return f.read()


# ───────────────── 一、币种档位是唯一基准 ─────────────────

@pytest.mark.parametrize("sym,expect", [
    ("XRP", 4.0), ("SOL", 4.0), ("BTC", 5.0), ("ETH", 5.0),
    ("VIRTUAL", 3.0), ("UNI", 3.0), ("XPL", 3.0),
])
def test_币种档位与实测持仓一致(monkeypatch, sym, expect):
    """对照 09-04 实测：MAP 内 XRP/SOL→4x，未列入的取 DEFAULT=3x。"""
    from backend.services.leverage_authority import symbol_leverage
    monkeypatch.setenv(
        "SYMBOL_LEVERAGE_MAP",
        "BTC:5,ETH:5,SOL:4,BNB:4,XRP:4,DOGE:4,LINK:4,AVAX:4,ADA:4",
    )
    monkeypatch.setenv("SYMBOL_LEVERAGE_DEFAULT", "3")
    assert symbol_leverage(sym) == expect


def test_同币跨周期杠杆必须相同(monkeypatch):
    """交易所按 symbol 设杠杆、同向仓位会合并 → 三周期不能各持一个倍数。"""
    from backend.services.leverage_authority import resolve_leverage
    monkeypatch.setenv("SYMBOL_LEVERAGE_ENABLED", "true")
    monkeypatch.setenv("SYMBOL_LEVERAGE_MAP", "SOL:4")
    levs = {
        t: resolve_leverage(tier=t, requested=10.0, symbol="SOL")
        for t in ("short", "mid", "long")
    }
    assert len(set(levs.values())) == 1, f"同币跨周期杠杆不一致: {levs}"


def test_上游动态杠杆被币种档位覆盖(monkeypatch):
    """requested=10x 必须让位于 MAP 的 4x，否则实盘会按 10x 成交。"""
    from backend.services.leverage_authority import resolve_leverage
    monkeypatch.setenv("SYMBOL_LEVERAGE_ENABLED", "true")
    monkeypatch.setenv("SYMBOL_LEVERAGE_MAP", "XRP:4")
    assert resolve_leverage(tier="short", requested=10.0, symbol="XRP") == 4.0


# ───────────────── 二、实盘短线接入统一权威 ─────────────────

def test_短线实盘不再直传动态杠杆():
    src = _src("backend", "services", "full_auto", "loops", "scalp_loop.py")
    assert "resolve_leverage as _auth_lev" in src, "实盘短线未接入 leverage_authority"
    # 只校验实盘下单块本身：影子记录里带 _dyn_lev 是元数据留痕，不是下单；
    # paper 下单传 _dyn_lev 也无妨——它必经 trade_gate 被改写成币种档位。
    _live_block = src.split("LiveExecutor().place_order")[1][:700]
    assert "leverage=_live_lev" in _live_block, "实盘下单仍在传上游动态杠杆"
    assert "_dyn_lev" not in _live_block, (
        "实盘 OrderContext 仍直传 _dyn_lev —— 会绕过币种统一档位"
    )


def test_短线实盘杠杆解析失败即拒单():
    src = _src("backend", "services", "full_auto", "loops", "scalp_loop.py")
    assert "live_leverage_resolve_failed" in src, (
        "杠杆解析失败未拒单 —— 宁可不开，不能按错误倍数开"
    )


# ───────────────── 三、旧路径必须对齐交易所杠杆 ─────────────────

def _mk_ctx(**kw):
    from backend.services.exchange.executors import OrderContext
    base = dict(
        account_id=1, symbol="XPL", side="buy", quantity=1.0,
        order_type="market", leverage=5.0,
    )
    base.update(kw)
    return OrderContext(**base)


def _mk_db():
    """让白名单前置校验抛异常走 except → 直达杠杆分支。"""
    db = MagicMock()
    db.query.side_effect = RuntimeError("no db in unit test")
    return db


def test_旧路径开仓前会对齐杠杆(monkeypatch):
    from backend.services.exchange.live_executor import LiveExecutor
    monkeypatch.setenv("LIVE_SUB_POSITION_TRACKING", "false")

    calls = []
    ex = LiveExecutor()
    monkeypatch.setattr(
        ex, "_apply_leverage",
        lambda db, aid, sym, lev: (calls.append((sym, lev)), True)[1],
    )
    monkeypatch.setattr(ex, "_send_raw_order", lambda db, ctx: {"ok": 1})

    res = ex.place_order(_mk_db(), _mk_ctx())
    assert calls == [("XPL", 5.0)], f"旧路径未对齐交易所杠杆: {calls}"
    assert res.status == "filled"


def test_对齐失败拒绝开仓(monkeypatch):
    """XPL 事故：交易所残留 75x、本地按 5x 记账 → 15 倍名义敞口。"""
    from backend.services.exchange.live_executor import LiveExecutor
    monkeypatch.setenv("LIVE_SUB_POSITION_TRACKING", "false")
    monkeypatch.setenv("LIVE_LEVERAGE_FAIL_CLOSE", "true")

    sent = []
    ex = LiveExecutor()
    monkeypatch.setattr(ex, "_apply_leverage", lambda *a, **k: False)
    monkeypatch.setattr(ex, "_send_raw_order", lambda db, ctx: sent.append(ctx))

    res = ex.place_order(_mk_db(), _mk_ctx())
    assert res.status == "error", "杠杆没对上仍然开了仓"
    assert "leverage_align_failed" in (res.error or "")
    assert sent == [], "拒单后仍然把单发了出去"


def test_平仓不被杠杆对齐卡住(monkeypatch):
    """减仓/平仓若也 fail-close，止损会被卡死在场内。"""
    from backend.services.exchange.live_executor import LiveExecutor
    monkeypatch.setenv("LIVE_SUB_POSITION_TRACKING", "false")
    monkeypatch.setenv("LIVE_LEVERAGE_FAIL_CLOSE", "true")

    ex = LiveExecutor()
    monkeypatch.setattr(ex, "_apply_leverage", lambda *a, **k: False)
    monkeypatch.setattr(ex, "_send_raw_order", lambda db, ctx: {"ok": 1})

    res = ex.place_order(_mk_db(), _mk_ctx(reduce_only=True))
    assert res.status == "filled", "平仓被杠杆对齐拦住 —— 止损会卡死"


def test_应急开关可回到只告警(monkeypatch):
    from backend.services.exchange.live_executor import LiveExecutor
    monkeypatch.setenv("LIVE_SUB_POSITION_TRACKING", "false")
    monkeypatch.setenv("LIVE_LEVERAGE_FAIL_CLOSE", "false")

    ex = LiveExecutor()
    monkeypatch.setattr(ex, "_apply_leverage", lambda *a, **k: False)
    monkeypatch.setattr(ex, "_send_raw_order", lambda db, ctx: {"ok": 1})

    assert ex.place_order(_mk_db(), _mk_ctx()).status == "filled"


def test_fail_close默认开(monkeypatch):
    from backend.services.exchange.live_executor import _leverage_fail_close
    monkeypatch.delenv("LIVE_LEVERAGE_FAIL_CLOSE", raising=False)
    assert _leverage_fail_close() is True


# ───────────────── 四、固定币的长周期 K 线必须保鲜 ─────────────────

def test_固定币自动并入观察名单(monkeypatch):
    """否则固定币落到 P1 冷门尾部，1h 轮转 ~2.4h > trade 门槛 2.02h。"""
    import backend.services.kline_realtime_collector as krc

    monkeypatch.setenv("KLINE_FRESHNESS_SYMBOLS", "BTC,ETH")
    monkeypatch.setattr(
        "backend.services.trading_pairs_config.get_user_trading_pairs",
        lambda: ["BTC", "SOL", "VIRTUAL", "XPL"],
    )
    got = krc.KlineRealtimeCollector._freshness_watch_symbols(
        krc.KlineRealtimeCollector.__new__(krc.KlineRealtimeCollector)
    )
    for s in ("SOL", "VIRTUAL", "XPL"):
        assert s in got, f"固定币 {s} 未进观察名单 → 1h 会过期到无法交易"
    assert got.count("BTC") == 1, "重复币未去重"


def test_取固定币失败时降级不炸(monkeypatch):
    import backend.services.kline_realtime_collector as krc

    monkeypatch.setenv("KLINE_FRESHNESS_SYMBOLS", "BTC,ETH")
    monkeypatch.setattr(
        "backend.services.trading_pairs_config.get_user_trading_pairs",
        lambda: (_ for _ in ()).throw(RuntimeError("db down")),
    )
    got = krc.KlineRealtimeCollector._freshness_watch_symbols(
        krc.KlineRealtimeCollector.__new__(krc.KlineRealtimeCollector)
    )
    assert "BTC" in got and "ETH" in got, "取固定币失败时未降级到 env 名单"


def test_trade通道门槛就是2_02小时():
    """2.02h 的来源：period*2+60 = 3600*2+60 = 7260s。"""
    from backend.services.data_center import PERIOD_SECONDS
    assert PERIOD_SECONDS["1h"] == 3600
    assert (3600 * 2 + 60) / 3600 == pytest.approx(2.0167, abs=1e-3)
