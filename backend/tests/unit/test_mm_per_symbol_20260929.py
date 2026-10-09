# -*- coding: utf-8 -*-
"""[h527 2026-09-29] **逐币几何 + 逐币规模**（保腿量、压尾部）的契约测试。

为什么要有它：h524 实测（当前时代 15.79h）单币 净bp/腿 相差 4 倍
（XRP +0.134 vs NEAR −3.526），逐币 taker 占比差 3 倍（BNB 14% vs ENA 42%），
而 NEAR+ARB+ENA 占 32% 的腿却占 90% 的止损腿。但引擎的挂宽与单币敞口
**只有全局一份** ⇒ 要么全做要么全不做，而腿量硬约束（≥60/h）恰好由亏损币撑着。

本改动给两个旋钮：`QuoteParams.per_symbol_spread_mult`（挂宽）与
`LaneRiskLimits.per_symbol_max_notional_ratio`（单笔规模/库存分母）。
**腿数是"事件数"不是名义额** ⇒ 按币缩小规模可以在腿数不变的前提下把尾部
USD 亏损等比压下去。

本测试守住四件事：
  1. **默认关闭时逐字不变**（缺失/空/非法 ⇒ 与改动前完全一致）——这是可回退的前提；
  2. 只影响**指定币**，其它币一字不动；
  3. 挂宽倍数同时作用于**进场侧与减仓侧**（否则减仓腿会按旧宽度挂，
     库存平不掉的失败模式已见过一次）；
  4. 登记表字典能**原样**穿过 `__dataclass_fields__` 过滤与 JSON 往返
     （否则热采用会静默把参数丢掉，F189「改了但没生效」）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.market_maker import runner as mmrunner  # noqa: E402
from backend.services.market_maker.core import (  # noqa: E402
    InventoryBook, LaneRiskLimits, Position, QuoteParams, check_lane_limits,
    check_side_allowed, compute_quote, symbol_lookup,
)

MID = 100.0
EQUITY = 300.0


def _quote(symbol="XRP", **kw):
    """在**价差相对模式**下算一笔报价（spread_mult=1 ⇒ 基准半宽 = 半价差）。"""
    args = dict(symbol=symbol, mid=MID, spread_bp=10.0)
    args.update(kw)
    return compute_quote(**args)


def _book(symbol="XRP", qty=1.0, px=MID):
    b = InventoryBook()
    b.positions[symbol] = Position(qty=qty, avg_px=px, avg_mid=px,
                                   opened_ts=0.0, last_ts=0.0)
    return b


# ── 1. 查表语义 ────────────────────────────────────────────────────────────────
def test_symbol_lookup_semantics():
    m = {"XRP": 0.7, "NEAR-USDT": 1.5, "arb": 2.0}
    assert symbol_lookup(m, "XRP", 1.0) == 0.7
    # 后缀剥离：XRP-USDT / XRP/USDT:USDT ⇒ XRP
    assert symbol_lookup(m, "XRP-USDT", 1.0) == 0.7
    assert symbol_lookup(m, "XRP/USDT:USDT", 1.0) == 0.7
    # 键带后缀、传入是裸币名 ⇒ 大小写回退也要命中
    assert symbol_lookup(m, "near-usdt", 1.0) == 1.5
    assert symbol_lookup(m, "ARB", 1.0) == 2.0          # 键小写、币大写
    # 未命中 / 非法 ⇒ default（旧行为）
    assert symbol_lookup(m, "BNB", 1.0) == 1.0
    assert symbol_lookup(None, "XRP", 0.42) == 0.42
    assert symbol_lookup({}, "XRP", 0.42) == 0.42
    assert symbol_lookup({"XRP": "abc"}, "XRP", 0.42) == 0.42
    assert symbol_lookup({"XRP": 0}, "XRP", 0.42) == 0.42      # ≤0 视为未配置
    assert symbol_lookup({"XRP": -1.0}, "XRP", 0.42) == 0.42
    assert symbol_lookup(["XRP"], "XRP", 0.42) == 0.42          # 非 dict


# ── 2. 默认关闭 ⇒ 逐字不变 ─────────────────────────────────────────────────────
def test_default_off_is_identical():
    # ⚠️ 基准必须与各变体**同一模式**：`QuoteParams()` 的 `spread_mult` 默认来自
    # 环境变量（0.0 ⇒ 绝对 bp 路径），而这里比的是价差相对模式，
    # 否则比的是两个分支而不是"有没有逐币倍数"（首版就写错在这一点上）。
    base = _quote(params=QuoteParams(spread_mult=1.0))
    for off in (None, {}, {"BNB": 0.5}, {"XRP": 0}):
        q = _quote(params=QuoteParams(spread_mult=1.0, per_symbol_spread_mult=off))
        assert q is not None and base is not None
        assert (q.bid, q.ask) == (base.bid, base.ask), off
        assert (q.w_bid_bp, q.w_ask_bp) == (base.w_bid_bp, base.w_ask_bp), off
        assert q.base_bp == base.base_bp and q.mode == base.mode, off


# ── 3. 只影响指定币 ───────────────────────────────────────────────────────────
def test_spread_mult_only_that_symbol():
    p = QuoteParams(spread_mult=1.0, per_symbol_spread_mult={"XRP": 0.5})
    xrp = _quote("XRP", params=p)
    bnb = _quote("BNB", params=p)
    assert abs(xrp.w_bid_bp - 2.5) < 1e-9 and abs(xrp.w_ask_bp - 2.5) < 1e-9
    assert abs(bnb.w_bid_bp - 5.0) < 1e-9 and abs(bnb.w_ask_bp - 5.0) < 1e-9


def test_spread_mult_widening_and_reduce_side():
    # 加宽：ARB ×1.5 ⇒ 基准半宽 7.5bp；且减仓侧同倍（inv_ratio>0 ⇒ ask 是减仓侧）
    p = QuoteParams(spread_mult=1.0, per_symbol_spread_mult={"ARB": 1.5},
                    spread_mult_reduce=0.5, k_inv=0.0)
    q = _quote("ARB", params=p, inv_ratio=1.0)
    # 未改前：进场侧 5.0、减仓侧 2.5；改后同乘 1.5 ⇒ 7.5 / 3.75
    assert abs(q.w_ask_bp - 3.75) < 1e-9, q.w_ask_bp     # ask = 减仓侧
    assert abs(q.w_bid_bp - 7.5) < 1e-9, q.w_bid_bp      # bid = 进场侧


# ── 4. 单币敞口：加仓侧被挡、减仓侧照旧豁免 ────────────────────────────────────
def test_exposure_cap_per_symbol():
    limits = LaneRiskLimits(max_net_directional_ratio=0.10,      # 全局 $30
                            per_symbol_max_notional_ratio={"NEAR": 0.02})  # NEAR $6
    b = _book("NEAR", qty=0.10, px=MID)          # $10 持仓 > $6 但 < $30
    ok, why = check_side_allowed(symbol="NEAR", side="buy", book=b,
                                 marks={"NEAR": MID}, equity=EQUITY, limits=limits)
    assert ok is False and why.startswith("symbol_exposure"), why
    # 减仓侧必须豁免（否则库存永久卡死 ⇒ 亏损搬去 taker 强平）
    ok2, why2 = check_side_allowed(symbol="NEAR", side="sell", book=b,
                                   marks={"NEAR": MID}, equity=EQUITY, limits=limits)
    assert ok2 is True and why2 == "reduce", why2
    # 未配置的币仍用全局 $30 ⇒ $10 放行
    b2 = _book("BNB", qty=0.10, px=MID)
    ok3, why3 = check_side_allowed(symbol="BNB", side="buy", book=b2,
                                   marks={"BNB": MID}, equity=EQUITY, limits=limits)
    assert ok3 is True, why3


def test_lane_limits_per_symbol_and_rollback():
    b = _book("NEAR", qty=0.10, px=MID)          # $10
    on = LaneRiskLimits(max_net_directional_ratio=0.10,
                        per_symbol_max_notional_ratio={"NEAR": 0.02})
    assert check_lane_limits(symbol="NEAR", book=b, marks={"NEAR": MID},
                             equity=EQUITY, limits=on)[0] is False
    # 回退：把键去掉（或置空）⇒ 回到全局 $30 ⇒ 放行
    for off in (None, {}):
        off_lim = LaneRiskLimits(max_net_directional_ratio=0.10,
                                 per_symbol_max_notional_ratio=off)
        assert check_lane_limits(symbol="NEAR", book=b, marks={"NEAR": MID},
                                 equity=EQUITY, limits=off_lim)[0] is True, off


# ── 5. 登记表往返（热采用不能把参数丢掉）──────────────────────────────────────
def test_registry_roundtrip_keeps_dict():
    stored = {"spread_mult": 1.0, "per_symbol_spread_mult": {"XRP": 0.7, "ARB": 1.5},
              "max_net_directional_ratio": 0.10,
              "per_symbol_max_notional_ratio": {"NEAR": 0.02, "ENA": 0.02},
              "per_symbol_size_mult": {"NEAR": 0.35},
              "unknown_key_should_be_dropped": 1}
    p = QuoteParams(**{k: v for k, v in stored.items()
                       if k in QuoteParams.__dataclass_fields__})
    lim = LaneRiskLimits(**{k: v for k, v in stored.items()
                            if k in LaneRiskLimits.__dataclass_fields__})
    assert p.per_symbol_spread_mult == {"XRP": 0.7, "ARB": 1.5}
    assert lim.per_symbol_max_notional_ratio == {"NEAR": 0.02, "ENA": 0.02}
    assert lim.per_symbol_size_mult == {"NEAR": 0.35}
    # JSON 往返（meta_json → Python 的必经路径）
    rt = json.loads(json.dumps({"psm": p.per_symbol_spread_mult,
                                "psr": lim.per_symbol_max_notional_ratio,
                                "psz": lim.per_symbol_size_mult}))
    assert rt["psm"] == {"XRP": 0.7, "ARB": 1.5}
    assert rt["psr"]["NEAR"] == 0.02 and rt["psz"]["NEAR"] == 0.35
    # 落表后仍能算对宽度（端到端：登记表值 → 报价宽度）
    q = _quote("XRP", params=p)
    assert abs(q.w_bid_bp - 3.5) < 1e-9, q.w_bid_bp     # 0.7 × 5.0bp


# ── 6. 逐币规模：只缩加仓腿，减仓腿**一字不动** ───────────────────────────────
def _plan(*, mid=1.0, seg_low=0.999, seg_high=1.001,
          seg_taker_sell=2000.0, seg_taker_buy=2000.0,
          fill_notional=100.0, limits=None, book=None, qty=0.0, symbol="SOLUSDT"):
    """驱动 `plan_tick` 的腿量（沿用 test_f338 的夹具口径）。

    `mid=1.0` ⇒ 目标腿量 = `fill_notional` 币（= $100）；`_avail×0.30 = 600` 币
    ⇒ **目标量是约束方**（否则测的是队列份额、不是规模倍数）。
    成交判定必须用 `judged_quote`（F176 的上一 tick 挂单），否则一条腿都不产生 ⇒
    断言会变成**空真**（test_f338 第 0 条测试专门防这个，这里照做）。
    """
    import inspect
    import time

    b = book if book is not None else InventoryBook()
    if qty:
        b.positions[symbol] = Position(qty=qty, avg_px=mid, avg_mid=mid,
                                       opened_ts=time.time() - 5.0, last_ts=0.0)
    st = mmrunner.SymbolState(symbol=symbol)
    kwargs = {
        "state": st, "mid": mid, "seg_low": seg_low, "seg_high": seg_high,
        "seg_taker_sell": seg_taker_sell, "seg_taker_buy": seg_taker_buy,
        "now_ts": time.time(),
        "params": QuoteParams(spread_mult=0.5, w_base_bp=5.0, min_width_bp=0.3),
        # ⚠️ 敞口上限放到 $2000：否则默认 0.10×1000=$100 会先把加仓腿挡掉，
        #    测到的是敞口闸而不是规模倍数（两者混淆过就分不清回退该退哪个）。
        "limits": limits if limits is not None else LaneRiskLimits(
            max_net_directional_ratio=2.0),
        "book": b, "equity": 1000.0, "fill_notional": fill_notional,
        "half_spread": 0.0005, "judged_quote": (0.9995, 1.0005, 1.0),
    }
    sig = inspect.signature(mmrunner.plan_tick).parameters
    kwargs = {k: v for k, v in kwargs.items() if k in sig}
    return mmrunner.plan_tick(**kwargs)


def _legs(dec):
    return [(f.side, f.qty, f.px) for f in (dec.fills or [])]


def test_size_mult_scales_add_leg_only():
    """① 前提自证：默认（无逐币键）时加仓腿确实达到目标 $100（否则下面的断言是空真）；
    ② 写 `{"SOLUSDT": 0.35}` ⇒ 加仓腿缩到 $35；
    ③ 只影响**指定币**；
    ④ **减仓腿一字不动**（否则残仓平不掉，亏损会搬去 taker 强平）。"""
    # ① 前提自证
    base = _legs(_plan()[0])
    assert base, "场景失效：一条腿都没有"
    biggest = max(q * p for _s, q, p in base)
    assert biggest >= 99.0, f"场景失效：最大腿只有 ${biggest:.2f}，目标应是 $100"

    # ② 加仓腿按倍数缩小
    lim = LaneRiskLimits(max_net_directional_ratio=2.0,
                         per_symbol_size_mult={"SOLUSDT": 0.35})
    dec, _ = _plan(limits=lim)
    add = [q * p for s, q, p in _legs(dec) if s == "buy"]
    assert add and max(add) <= 35.5, _legs(dec)
    assert max(add) >= 34.5, _legs(dec)

    # ③ 只影响指定币
    lim2 = LaneRiskLimits(max_net_directional_ratio=2.0,
                          per_symbol_size_mult={"OTHER": 0.35})
    d2, _ = _plan(limits=lim2)
    assert max(q * p for _s, q, p in _legs(d2)) >= 99.0, _legs(d2)

    # ④ 减仓腿**不被乘**：持仓 50 ⇒ 买腿缩到 35 后仓位是 85，
    #    卖腿必须**精确平掉 85**（F91 口径），而不是 0.35×85 = 29.75
    #    —— 后者会留下 55 的残仓，把亏损从价差搬到 taker 强平（已见过两次）。
    d3, _ = _plan(limits=lim, qty=50.0)
    lg = _legs(d3)
    buy = [q for s, q, _p in lg if s == "buy"]
    sell = [q for s, q, _p in lg if s == "sell"]
    assert buy and sell, lg
    assert abs(buy[0] - 35.0) < 0.51, lg
    assert abs(sell[0] - (50.0 + buy[0])) < 0.51, lg          # 整仓平掉 ✓
    assert abs(sell[0] - 0.35 * (50.0 + buy[0])) > 10.0, lg   # 绝不是被缩放的值 ✗

    # ⑤ 回退：把键去掉 ⇒ 回到 $100（且减仓腿仍精确平仓：100 + 50 = 150）
    d4, _ = _plan(limits=LaneRiskLimits(max_net_directional_ratio=2.0), qty=50.0)
    lg4 = _legs(d4)
    assert max(q for s, q, _p in lg4 if s == "buy") >= 99.0, lg4
    assert abs(max(q for s, q, _p in lg4 if s == "sell") - 150.0) < 0.51, lg4
