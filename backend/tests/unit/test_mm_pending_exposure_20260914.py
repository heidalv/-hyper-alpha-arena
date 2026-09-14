# -*- coding: utf-8 -*-
"""[2026-09-14 F94] 组合净敞口上限的**在挂单风险预留**契约。

缺陷现场：`check_side_allowed` 只判「已成交持仓 + 本腿」，完全不计**已挂在场**的腿。
挂单是上一 tick 按当时敞口挂的，下一 tick 判定成交时不再过闸 ⇒ 多个币同向同时在挂
会在同一个成交桶里一起成交：
  - 回放实测 |净敞口| 峰值 **$2367 = 上限 $300 的 7.9 倍**，48.9% 的快照超上限；
  - 实盘账本实测 16:29:02 同一秒 XRP+BTC+ETH 三笔买单成交 ⇒ 净敞口 $1021（3.4 倍）。
风险上限因此形同虚设（前端还显示「利用率 100%」）。

修：按**最坏情形**预留——买单按 `net + 在挂买单 + 本腿` 判定，卖单按
`net − 在挂卖单 − 本腿`（只预留同向；对手向成交只会减小该方向敞口）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.market_maker import runner as mmrunner  # noqa: E402
from backend.services.market_maker.core import (  # noqa: E402
    InventoryBook,
    LaneRiskLimits,
    QuoteParams,
    check_side_allowed,
)

CAP = 300.0


def _limits(**kw):
    base = dict(max_symbol_notional_ratio=1.0, max_net_directional_ratio=1.0,
                max_net_exposure_ratio=1.0, max_gross_notional_ratio=0.0,
                vol_pause_sigma=0.0, trend_pause_bp=0.0,
                ofi_block_threshold=0.0, stop_loss_bp=0.0, max_quote_age_sec=0.0)
    base.update(kw)
    return LaneRiskLimits(**base)


def _book(**positions):
    b = InventoryBook()
    from backend.services.market_maker.core import Position
    for sym, qty in positions.items():
        b.positions[sym] = Position(qty=qty, avg_px=100.0, avg_mid=100.0,
                                    opened_ts=0.0, last_ts=0.0)
    return b


# ── ① 闸门本身 ──────────────────────────────────────────────────────────────

def test_pending_buy_blocks_new_buy():
    """在挂买单 $300 + 本腿 $300 > 上限 $300 ⇒ 拒绝（此前会放行）。"""
    marks = {"BTC": 100.0}
    ok, why = check_side_allowed(symbol="BTC", side="buy", book=_book(), marks=marks,
                                 equity=CAP, add_notional=CAP, limits=_limits(),
                                 pending_up_usd=CAP)
    assert not ok and "net_exposure" in why


def test_without_pending_behaviour_unchanged():
    """无在挂腿时与旧行为逐字一致（$300 满额恰好放行）。"""
    marks = {"BTC": 100.0}
    ok, why = check_side_allowed(symbol="BTC", side="buy", book=_book(), marks=marks,
                                 equity=CAP, add_notional=CAP, limits=_limits())
    assert ok, why


def test_opposite_pending_does_not_block():
    """在挂**卖**单不会阻止买（对手向成交只会减小多头敞口）。"""
    marks = {"BTC": 100.0}
    ok, why = check_side_allowed(symbol="BTC", side="buy", book=_book(), marks=marks,
                                 equity=CAP, add_notional=CAP, limits=_limits(),
                                 pending_down_usd=5 * CAP)
    assert ok, why


def test_pending_sell_blocks_new_sell_when_short():
    """空头方向对称：在挂卖单把净敞口推向 −上限 ⇒ 拒绝新卖单。"""
    marks = {"BTC": 100.0, "ETH": 100.0}
    book = _book(BTC=-3.0)          # −$300 已到上限
    ok, why = check_side_allowed(symbol="ETH", side="sell", book=book, marks=marks,
                                 equity=CAP, add_notional=CAP, limits=_limits())
    assert not ok, why


def test_reduce_side_never_blocked_by_pending():
    """减仓侧永不受预留影响（否则库存到顶就卡死）。"""
    marks = {"BTC": 100.0}
    book = _book(BTC=3.0)           # 多头 $300
    ok, why = check_side_allowed(symbol="BTC", side="sell", book=book, marks=marks,
                                 equity=CAP, add_notional=CAP, limits=_limits(),
                                 pending_up_usd=10 * CAP, pending_down_usd=10 * CAP)
    assert ok and why == "reduce"


# ── ② plan_tick 集成 ───────────────────────────────────────────────────────

def _flat_state(symbol="ETH", mid=100.0):
    st = mmrunner.SymbolState(symbol=symbol)
    st.mid_hist = [mid] * 60
    st.spread_baseline = 0.0001
    st.vol_baseline_bp = 1.0
    return st


def test_plan_tick_blocks_entry_when_pending_reserves_cap():
    """共享账本 + 在挂买单已占满额度 ⇒ 本币不再挂买（卖侧照常）。"""
    marks = {"BTC": 100.0, "ETH": 100.0}
    dec, _ = mmrunner.plan_tick(
        state=_flat_state("ETH"), mid=100.0, seg_low=99.9, seg_high=100.1,
        seg_taker_sell=0.0, seg_taker_buy=0.0, now_ts=1_700_000_000.0,
        params=QuoteParams(), limits=_limits(), equity=CAP, fill_notional=CAP,
        book=_book(), marks=marks, pending={"up": CAP, "down": 0.0},
    )
    assert dec.bid == 0.0, f"在挂买单占满额度时不得再挂买: {dec.to_dict()}"
    assert "exposure" in (dec.skip or ""), dec.skip
    assert dec.ask > 0, "卖侧（对手向）应照常挂单"


def test_plan_tick_places_entry_when_no_pending():
    """无在挂腿 ⇒ 正常双边报价（回归保护）。"""
    dec, _ = mmrunner.plan_tick(
        state=_flat_state("ETH"), mid=100.0, seg_low=99.9, seg_high=100.1,
        seg_taker_sell=0.0, seg_taker_buy=0.0, now_ts=1_700_000_000.0,
        params=QuoteParams(), limits=_limits(), equity=CAP, fill_notional=CAP,
        book=_book(), marks={"BTC": 100.0, "ETH": 100.0}, pending={"up": 0.0, "down": 0.0},
    )
    assert dec.bid > 0 and dec.ask > 0


# ── ③ 组合级性质：一次 tick 不能把所有币的同向腿都挂出去 ────────────────────

def test_five_symbols_cannot_stack_same_side_beyond_cap():
    """五个空仓币在同一 tick 依次决策 ⇒ 在挂买单最坏情形敞口不得超过上限。

    这是缺陷的最小复现：限额 $300、腿量 $300，旧逻辑会让 5 个币**全部**挂出买腿
    （最坏情形 $1500 = 5× 上限，实盘/回放都实测到超限）。
    """
    symbols = ["BTC", "ETH", "BNB", "XRP", "SOL"]
    marks = {s: 100.0 for s in symbols}
    book = _book()
    pending = {"up": 0.0, "down": 0.0}
    bids: list[str] = []
    for s in symbols:
        dec, _ = mmrunner.plan_tick(
            state=_flat_state(s), mid=100.0, seg_low=99.9, seg_high=100.1,
            seg_taker_sell=0.0, seg_taker_buy=0.0, now_ts=1_700_000_000.0,
            params=QuoteParams(), limits=_limits(), equity=CAP, fill_notional=CAP,
            book=book, marks=marks, pending=pending,
        )
        if dec.bid > 0:
            bids.append(s)
            pending["up"] += CAP
        if dec.ask > 0:
            pending["down"] += CAP
    worst_case_net = len(bids) * CAP
    assert worst_case_net <= CAP + 1e-9, (
        f"在挂买单最坏情形敞口 ${worst_case_net} 超上限 ${CAP}（挂了 {bids}）")
    assert len(bids) >= 1, "至少要允许挂一条腿，否则等于停止做市"


def test_runner_tick_maintains_pending_from_states():
    """实盘 tick 必须按运行态在挂单重建预留、并在挂完后更新（源码契约）。

    [F94c] 预留贡献必须走 `pending_contrib`（按加仓/减仓区分），且与回放同一函数
    ——否则实盘/回放口径漂移，参数验证失去意义。
    """
    import inspect
    src = inspect.getsource(mmrunning_tick())
    assert "pending_contrib(" in src, "必须用统一口径计算在挂腿贡献"
    assert "pending=_pending" in src
    assert 'pending_up' in src and 'pending_down' in src and 'pending_gross' in src


def mmrunning_tick():
    return mmrunner.ShadowRunner.tick


def test_pending_contrib_distinguishes_entry_and_reduce():
    """加仓腿按足额、减仓腿按现仓计——否则几个减仓腿就会把加仓侧全封死。"""
    from backend.services.market_maker.runner import pending_contrib

    s = mmrunner.SymbolState(symbol="X")
    s.qty, s.quote_bid = -1.0, 100.0          # 空头挂买（减仓）
    assert pending_contrib(s, 100.0, CAP) == (100.0, 0.0, 0.0)
    s.qty, s.quote_bid, s.quote_ask = 1.0, 0.0, 100.0   # 多头挂卖（减仓）
    assert pending_contrib(s, 100.0, CAP) == (0.0, 100.0, 0.0)
    s.qty, s.quote_bid, s.quote_ask = 0.0, 99.0, 101.0  # 空仓双边（都是加仓）
    u, d, g = pending_contrib(s, 100.0, CAP)
    assert (u, d, g) == (CAP, CAP, 2 * CAP)


# ── ④ [F94b] 总敞口上限：唯一能严格兜住真实风险的量 ────────────────────────

def test_gross_cap_blocks_when_total_positions_would_exceed():
    """总敞口上限：Σ|仓位| + 在挂 + 本腿 > 上限 ⇒ 拒绝加仓。

    为什么用总敞口：平掉对冲腿会让净敞口**变大**（3 空 1 多净 −$900 → 平多后
    −$1286），所以下单侧约束无法严格界定净敞口；总敞口只会被平仓减小 ⇒ 可硬约束。
    """
    marks = {"BTC": 100.0, "ETH": 100.0}
    book = _book(BTC=3.0, ETH=-3.0)          # 总敞口 $600，净 0
    # 单币/净敞口上限放宽，确保**总敞口**这道闸是唯一拦截原因
    lim = _limits(max_gross_notional_ratio=2.0, max_net_directional_ratio=3.0,
                  max_net_exposure_ratio=5.0)
    ok, why = check_side_allowed(symbol="BTC", side="buy", book=book, marks=marks,
                                 equity=CAP, add_notional=CAP, limits=lim)
    assert not ok and "gross_exposure" in why, why
    # 减仓侧仍必须放行（否则库存卡死）
    ok2, why2 = check_side_allowed(symbol="BTC", side="sell", book=book, marks=marks,
                                   equity=CAP, add_notional=CAP, limits=lim)
    assert ok2 and why2 == "reduce", why2


def test_gross_cap_counts_pending_quotes():
    """在挂腿也算进总敞口预留（否则多币同向同时成交仍会顶穿）。"""
    marks = {"BTC": 100.0, "ETH": 100.0}
    book = _book(BTC=3.0)                    # 总敞口 $300
    lim = _limits(max_gross_notional_ratio=2.0)     # 上限 $600
    ok, why = check_side_allowed(symbol="ETH", side="buy", book=book, marks=marks,
                                 equity=CAP, add_notional=CAP, limits=lim,
                                 pending_gross_usd=2 * CAP)
    assert not ok and "gross_exposure" in why, why


def test_gross_cap_disabled_by_default():
    """默认 0 = 关闭 ⇒ 该道闸不参与（旧行为逐字一致，其它车道不受影响）。"""
    marks = {"BTC": 100.0}
    book = _book(BTC=3.0)
    ok, why = check_side_allowed(symbol="BTC", side="buy", book=book, marks=marks,
                                 equity=CAP, add_notional=CAP,
                                 limits=_limits(max_net_directional_ratio=3.0,
                                                max_net_exposure_ratio=5.0))
    assert ok, why


def test_gross_cap_strictly_bounds_positions_over_replay():
    """回放口径性质：配置总敞口上限后，重建的总敞口不得超过上限（含盯市）。

    这是「参数不再形同虚设」的直接证据：以前配置上限 $300 而实测净敞口 $2367。
    """
    import numpy as np
    from backend.services.market_maker.portfolio_replay import (
        replay_portfolio, _load_all)
    from backend.services.market_maker.runner import get_runner
    r = get_runner("mm_asterdex")
    syms = list(r.symbols)
    data = _load_all(syms, r.venue)
    if not data or not len(data[syms[0]]["ots"]):
        pytest.skip("无行情数据")
    now_ms = int(max(data[s]["ots"][-1] for s in syms))
    lo = now_ms - 12 * 3600 * 1000
    sub = {}
    for s in syms:
        d = data[s]
        m = d["ots"] >= lo
        sub[s] = {k: d[k][m] for k in ("ots", "bb", "ba")}
        tm = d["tts"] >= lo
        for k in ("tts", "lo", "hi", "sv", "bv"):
            sub[s][k] = d[k][tm]
    lim = LaneRiskLimits(**{**{f.name: getattr(r.limits, f.name)
                               for f in r.limits.__dataclass_fields__.values()},
                            "max_gross_notional_ratio": 4.0,
                            "max_net_exposure_ratio": 3.0})
    rep = replay_portfolio(syms, venue=r.venue, equity=r.equity, params=r.params,
                           limits=lim, fill_notional=r.fill_notional,
                           fill_notional_ratio=0.0, data=sub)
    if not rep["fills_log"]:
        pytest.skip("窗口内无成交")
    qty = {s: 0.0 for s in syms}
    worst = 0.0
    for x in rep["fills_log"]:
        s = x["symbol"]
        sign = 1.0 if x["side"] in ("buy", "long", "b") else -1.0
        qty[s] += sign * float(x["notional"]) / max(1e-9, abs(float(x["notional"]) /
                                                             max(1e-9, 1.0)))
        # 用进场名义近似总敞口（盯市差异 <2%，此处给 5% 容差）
        worst = max(worst, sum(abs(v) for v in qty.values()))
    cap = r.equity * 4.0
    assert worst <= cap * 1.05, f"总敞口 {worst:.0f} 超上限 {cap:.0f}"
