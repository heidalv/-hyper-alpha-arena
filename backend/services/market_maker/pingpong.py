# -*- coding: utf-8 -*-
"""重复来回做市（ping-pong）：不猜涨跌、不卡点位，把一种可重复的来回做够。

设计（用户 2026-10-09）：
  1. 进场：空仓时买单挂在买一后面一档、卖单挂在卖一后面一档。
     永不高于买一、永不低于卖一。最小跳动用交易所已有的价格步进
     （`venue_filters` 的 tick_size）。
  2. 成交前撤单：进场单在前档挂单量掉到武装时的一小半、或中价已经穿过挂单价
     时撤掉。**离场单不撤**，避免仓位卡住。
  3. 一进一出：成交后只挂反向、同数量的平仓单，直到回到空仓；平仓单挂在
     对手方向后面一档，不改成对手价。空仓后歇约 15 秒再挂下一对。
     有仓时禁止同向加仓（结构上做不到：有仓分支根本不走进场武装）。
  4. 吃单只留两种：盘口死了（沿用 no_book 语义），或价格跳空到灾难止损档
     （`choose_exit` 的 taker_stop）。没有 8 分钟无条件市价强平。
     吃单进场不存在于本模块。
  5. 每笔一样大：开仓与平仓同一数量（平仓 qty = abs(pos)）；单笔名义沿用
     现有权益上限（notional_cap_usd + 总敞口剩余额度 + 可选硬百分比），
     不按信号强度放大。

挂单跟随语义（与旧路径的波动容差不同）：进场/离场都**精确跟随目标价**
（买一 − N tick / 卖一 + N tick），目标价不变就保持原单（队列进度不清零），
目标价一动就重挂到新档（刷新薄量参考与前排队列）。离场单没有队列闸
（ahead=0），重挂不会卡住成交。

验收：看 `lane_ledger.meta_json->>'rt_bp'`（开仓价到平仓价），不看旧的价差列；
满约 100 笔后按成功率 60% 附近 + 赚的幅度大于亏的幅度裁决继续或停止
（见 scripts/pp_scoreboard.py）。

回滚：`MM_PINGPONG=0` ⇒ active_flow_decision 逐字回到旧的单边流路径。
旋钮（优先级：env 覆盖 > 学习参数 data/flow_learn_params.json > 代码默认）：
MM_PP_REST_SEC（默认 15）/ MM_PP_THIN_FRAC（默认 0.5，0=关薄量检查）/
MM_PP_EXIT_TICKS（默认 1）/ pp_bucket_min_n（桶门最少样本，默认 20）。
桶级状态门（pp_situation.py）默认开启，`MM_PP_BUCKET_GATE=0` 关闭。
"""
from __future__ import annotations

import os
from typing import Any, Optional

from backend.services.market_maker.active_flow import _maker_touched
from backend.services.market_maker.flow_rules import (
    choose_exit,
    disaster_stop_bp,
    maker_roundtrip_bp,
    notional_cap_usd,
)
from backend.services.market_maker import pp_situation


def _env_float(name: str, default: float) -> Optional[float]:
    """env 覆盖优先；未设置返回 None（让调用方落回学习值/代码默认）。"""
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _rest_sec(learned: Optional[float]) -> float:
    v = _env_float("MM_PP_REST_SEC", 15.0)
    if v is None:
        v = float(learned) if learned is not None else 15.0
    return max(0.0, v)


def _thin_frac(learned: Optional[float]) -> float:
    v = _env_float("MM_PP_THIN_FRAC", 0.5)
    if v is None:
        v = float(learned) if learned is not None else 0.5
    return max(0.0, min(1.0, v))


def _exit_ticks(learned: Optional[float]) -> int:
    v = _env_float("MM_PP_EXIT_TICKS", 1.0)
    if v is None:
        v = float(learned) if learned is not None else 1.0
    return max(1, int(round(v)))


def _bucket_min_n(learned: Optional[float]) -> float:
    v = _env_float("MM_PP_BUCKET_MIN_N", 20.0)
    if v is None:
        v = float(learned) if learned is not None else 20.0
    return max(5.0, v)


def _bucket_gate_on() -> bool:
    return str(os.getenv("MM_PP_BUCKET_GATE", "1") or "1").strip().lower() not in (
        "0", "false", "no", "off")


def _leg_cap_pct() -> float:
    """与旧路径共用：单腿名义 ≤ 权益这个百分比（默认 0 = 交回风险模型）。"""
    try:
        v = float(os.getenv("MM_AF_LEG_CAP_PCT", "0") or 0.0)
    except ValueError:
        v = 0.0
    return max(0.0, v)


def _allow_r45() -> bool:
    """与旧路径共用：R4/R5 是否放行进场（MM_FLOW_EXPLORE_R45，默认 0）。"""
    return str(os.getenv("MM_FLOW_EXPLORE_R45", "0") or "0").strip() not in (
        "0", "false", "False", "")


def _tick_for(symbol: str) -> float:
    """交易所价格步进（tickSize）。缺失/异常 ⇒ 0（偏移退化为挂买一/卖一）。"""
    try:
        from backend.services.market_maker.venue_filters import filters_for
        return float(filters_for(symbol).get("tick_size") or 0.0)
    except Exception:  # noqa: BLE001
        return 0.0


def pingpong_decision(
    *,
    state: Any,
    mid: float,
    bb: float,
    ba: float,
    now_ts: float,
    taker_fee_bp: float,
    local_book: Any,
    dec: Any,
    PlannedFill: Any,
    regime: str = "",
    book_stale: bool = False,
    equity: float = 0.0,
    vol_300s_bp: float = 0.0,
    seg_low: float = 0.0,
    seg_high: float = 0.0,
    seg_sell: float = 0.0,
    seg_buy: float = 0.0,
    same_side_n: int = 1,
    stop_floor_bp: float = 15.0,
    stop_cap_bp: float = 40.0,
    loss_frac: float = 0.005,
    roundtrip_root: Any = None,
    bid_qty: float = 0.0,
    ask_qty: float = 0.0,
    vol_at_bid: float = 0.0,
    vol_at_ask: float = 0.0,
    probe_notional_usd: float = 0.0,
    flow_exit_only: bool = False,
    gross_notional_usd: float = 0.0,
    max_gross_notional_ratio: float = 0.0,
    # ── [2026-10-09 进化重挂] 学习参数（来自 flow_learn_params.json，runner 传入；
    # env 覆盖优先，见模块顶部旋钮说明）──
    pp_rest_sec: Optional[float] = None,
    pp_thin_frac: Optional[float] = None,
    pp_exit_ticks: Optional[float] = None,
    pp_bucket_min_n: Optional[float] = None,
    pp_sit_doc: Any = None,
) -> Optional[str]:
    """返回 None。跳过原因写在 dec.skip。

    与 active_flow_decision 同款入参（子集），由它按 MM_PINGPONG 委托进来；
    旧路径逐字保留在 active_flow.py 里作回滚。
    """
    from backend.services.market_maker.venue_filters import (
        passes as _vp, round_px as _vpx, round_qty as _vqty,
    )

    # ── 本拍决策初始化 ──────────────────────────────────────────────
    dec.bid = 0.0
    dec.ask = 0.0
    dec.bid_qty = 0.0
    dec.ask_qty = 0.0
    dec.bid_reduce = False
    dec.ask_reduce = False
    fills = getattr(dec, "fills", None)
    if fills is None:
        fills = []
        try:
            dec.fills = fills
        except Exception:  # noqa: BLE001
            pass
    bid = float(bb or 0.0)
    ask = float(ba or 0.0)
    mid = float(mid or 0.0)
    now = float(now_ts or 0.0)
    tick = _tick_for(state.symbol)

    # ── 工具 ────────────────────────────────────────────────────────
    def _sfx(side: str) -> str:
        return "bid" if side == "buy" else "ask"

    def _pp(side: str, name: str) -> float:
        return float(getattr(state, f"pp_{name}_{_sfx(side)}", 0.0) or 0.0)

    def _set_pp(side: str, name: str, value: float) -> None:
        try:
            setattr(state, f"pp_{name}_{_sfx(side)}", float(value or 0.0))
        except Exception:  # noqa: BLE001
            pass

    def _echo_quote(side: str, px: float, qty: float, reduce_only: bool) -> None:
        if side == "buy":
            dec.bid = px
            dec.bid_qty = qty
            dec.bid_reduce = bool(reduce_only)
        else:
            dec.ask = px
            dec.ask_qty = qty
            dec.ask_reduce = bool(reduce_only)

    def _clear_side(side: str) -> None:
        if side == "buy":
            state.quote_bid = 0.0
            dec.bid = 0.0
            dec.bid_qty = 0.0
        else:
            state.quote_ask = 0.0
            dec.ask = 0.0
            dec.ask_qty = 0.0
        _set_pp(side, "front0", 0.0)
        _set_pp(side, "ahead", 0.0)
        _set_pp(side, "cum", 0.0)

    def _arm_side(side: str, px: float, qty: float, *, ahead_qty: float,
                  front_qty: float, reduce_only: bool) -> None:
        """精确跟随目标价：目标价不变 ⇒ 保持原挂单价（队列进度与薄量参考
        不清零）；目标价一动 ⇒ 重挂到新档并刷新参考。"""
        prev = float(state.quote_bid if side == "buy" else state.quote_ask or 0.0)
        if prev > 0 and px > 0 and abs(prev - px) <= px * 1e-8:
            _echo_quote(side, prev, qty, reduce_only)
            return
        if side == "buy":
            state.quote_bid = px
        else:
            state.quote_ask = px
        state.quote_ts = now
        _set_pp(side, "front0", float(front_qty or 0.0))
        _set_pp(side, "ahead", max(float(ahead_qty or 0.0), 0.0))
        _set_pp(side, "cum", 0.0)
        if not reduce_only and mid > 0:
            # [2026-10-09 卫生] 记**挂单时刻**相对买一/卖一的偏移（bp）：
            # 构造上买 ≤ 买一 ⇒ ≤0、卖 ≥ 卖一 ⇒ ≥0；成交后盘口再怎么动，
            # 这个数都不变 —— 是「有没有插进价差」的真相源。
            _rel = ((px - bid) / mid * 1e4) if side == "buy" \
                else ((px - ask) / mid * 1e4)
            _set_pp(side, "arm_rel", float(_rel or 0.0))
        _echo_quote(side, px, qty, reduce_only)

    def _close_roundtrip(why: str, fee_bp: float, exit_px: float,
                         entry_px: float, qty_sign: float) -> None:
        if roundtrip_root is None:
            return
        try:
            from backend.services.market_maker.flow_rules import append_roundtrip
            y = maker_roundtrip_bp(entry_px, exit_px, qty_sign)
            if fee_bp:
                y = None if y is None else y - float(fee_bp)
            _ot = float(getattr(state, "opened_ts_true", 0.0) or 0.0)
            if _ot <= 1e9:
                _ot = float(getattr(state, "opened_ts", 0.0) or 0.0)
            _hold = round(float(now) - _ot, 1) if _ot > 0 else None
            # [2026-10-09 进化重挂] 入场语境一并记账：进化层按这些键分桶学习，
            # 不再需要方向标签（方向模型不再决定开哪一边）。
            _ctx = dict(getattr(state, "pp_entry_ctx", None) or {})
            append_roundtrip(roundtrip_root, {
                "ts": now, "symbol": state.symbol, "why": why,
                "y_bp": y, "fee_bp": float(fee_bp),
                "maker": fee_bp == 0.0, "liquidations": 0,
                "strategy": "PP",
                "hold_sec": _hold,
                "entry_px": float(entry_px),
                "exit_px": float(exit_px),
                # 入场语境（rt_bp 之外的分桶键）
                "entry_side": str(_ctx.get("side") or ""),
                "entry_spread_bp": float(_ctx.get("spread_bp") or 0.0),
                "entry_vol_bp": float(_ctx.get("vol_bp") or 0.0),
                "entry_front_usd": float(_ctx.get("front_usd") or 0.0),
                "entry_regime": str(_ctx.get("regime") or ""),
            })
        except Exception:  # noqa: BLE001
            return

    def _apply(side: str, px: float, qty: float, why: str, fee_bp: float,
               flatten: bool) -> bool:
        if qty <= 1e-12 or px <= 0:
            return False
        entry_px = float(state.avg_px or 0.0)
        _pre_qty = float(state.qty or 0.0)
        fd = local_book.apply_fill(
            symbol=state.symbol, side=side, qty=qty, fill_px=px, mid_px=mid,
            fee_rate=abs(float(fee_bp)) / 1e4, now_ts=now)
        state.qty = local_book.qty(state.symbol)
        row = local_book.positions.get(state.symbol)
        state.avg_px = row.avg_px if row else 0.0
        state.avg_mid = row.avg_mid if row else 0.0
        state.last_ts = now
        if abs(_pre_qty) <= 1e-12 and abs(state.qty) > 1e-12:
            state.opened_ts_true = float(now)
            state.flow_strategy = "PP"
            state.flow_hold_sec = 0.0
            # [2026-10-09 进化重挂] 记入场语境，平仓时写进往返账做分桶键。
            try:
                _front = float(bid_qty or 0.0) * bid if side == "buy" \
                    else float(ask_qty or 0.0) * ask
                state.pp_entry_ctx = {
                    "side": str(side or ""),
                    "spread_bp": ((ask - bid) / mid * 1e4) if mid > 0 and ask > bid else 0.0,
                    "vol_bp": float(vol_300s_bp or 0.0),
                    "front_usd": float(_front or 0.0),
                    "regime": str(regime or ""),
                    "arm_rel_bp": float(_pp(side, "arm_rel")),
                }
            except Exception:  # noqa: BLE001
                state.pp_entry_ctx = {}
        _rt_bp = None
        _hold_true = 0.0
        if abs(state.qty) <= 1e-12:
            _ot = float(getattr(state, "opened_ts_true", 0.0) or 0.0)
            if _ot > 1e9:
                _hold_true = max(0.0, float(now) - _ot)
            _rt_bp = maker_roundtrip_bp(entry_px, px, _pre_qty)
            if _rt_bp is not None and fee_bp:
                _rt_bp = float(_rt_bp) - float(fee_bp)
            _close_roundtrip(why, fee_bp, px, entry_px, _pre_qty)
            state.opened_ts = 0.0
            state.opened_ts_true = 0.0
            state.flow_hold_sec = 0.0
            state.flow_mu = 0.0
            state.quote_bid = state.quote_ask = state.quote_ts = 0.0
            # 空仓后歇约 pp_rest_sec 再挂下一对，避开刚被打穿的那一下。
            try:
                state.pp_rest_until = float(now) + _rest_sec(pp_rest_sec)
            except Exception:  # noqa: BLE001
                pass
        elif not flatten:
            state.opened_ts = now
            state.quote_bid = state.quote_ask = state.quote_ts = 0.0
        for _side in ("buy", "sell"):
            _set_pp(_side, "front0", 0.0)
            _set_pp(_side, "ahead", 0.0)
            _set_pp(_side, "cum", 0.0)
            _set_pp(_side, "arm_rel", 0.0)
        _fill = PlannedFill(
            symbol=state.symbol, side=side, qty=qty, px=px, mid=mid, ts=now,
            is_flatten=flatten or abs(state.qty) <= 1e-12,
            spread_usd=float(fd.get("spread_usd") or 0.0),
            price_usd=float(fd.get("price_usd") or 0.0),
            fee_usd=float(fd.get("fee_usd") or 0.0),
            position_id=str(fd.get("position_id") or ""),
        )
        if _rt_bp is not None:
            _fill.rt_bp = float(_rt_bp)
            _fill.rt_entry_px = float(entry_px)
            _fill.hold_sec_true = float(_hold_true)
        if not flatten:
            # [2026-10-09 卫生] 进场腿带挂单时刻偏移（插价差的真口径）
            _fill.pp_arm_rel_bp = float(_pp(side, "arm_rel") or 0.0)
        fills.append(_fill)
        dec.exit_path = why
        if flatten or abs(state.qty) <= 1e-12:
            dec.action = "flatten"
        return True

    def _entry_cancel(side: str, px: float) -> bool:
        """进场单的穿透前撤单：前档变薄（相对武装时）或中价已穿过挂单价。"""
        if px <= 0:
            return False
        ref = _pp(side, "front0")
        cur = float(bid_qty if side == "buy" else ask_qty or 0.0)
        frac = _thin_frac(pp_thin_frac)
        if frac > 0 and ref > 0 and cur + ref * 1e-9 < ref * frac:
            return True
        if side == "buy":
            return mid > 0 and mid <= px * (1.0 + 1e-12)
        return mid > 0 and mid >= px * (1.0 - 1e-12)

    def _entry_cap(px: float) -> float:
        """单笔名义上限链：与旧路径完全一致（权益风险模型 + 可选硬百分比
        + 账户总敞口剩余额度 + 探针上限）。不按信号强度放大。"""
        stop = disaster_stop_bp(vol_300s_bp, floor_bp=stop_floor_bp,
                                cap_bp=stop_cap_bp)
        cap = notional_cap_usd(equity, stop, same_side_n, loss_frac)
        if _leg_cap_pct() > 0 and float(equity or 0.0) > 0:
            cap = min(cap, float(equity) * _leg_cap_pct() / 100.0)
        if float(max_gross_notional_ratio or 0.0) > 0 and float(equity or 0.0) > 0:
            _room = (float(equity) * float(max_gross_notional_ratio)
                     - max(0.0, float(gross_notional_usd or 0.0)))
            if _room <= 0.0:
                return 0.0
            cap = min(cap, _room)
        if float(probe_notional_usd or 0.0) > 0:
            cap = min(cap, float(probe_notional_usd))
        return max(0.0, cap)

    # ══ 结算：先处理本拍被逐笔打到的挂单（每拍至多成交一侧，买单优先）══
    pos0 = float(state.qty or 0.0)
    resting: list = []
    if float(state.quote_bid or 0.0) > 0:
        resting.append("buy")
    if float(state.quote_ask or 0.0) > 0:
        resting.append("sell")
    filled = False
    for side in resting:
        if filled:
            break
        px = float(state.quote_bid if side == "buy" else state.quote_ask or 0.0)
        if px <= 0:
            continue
        if abs(pos0) > 1e-12:
            # 有仓：只结算离场侧；同侧残留挂单（不该有）清掉，禁止加仓。
            is_exit = (pos0 > 0 and side == "sell") or (pos0 < 0 and side == "buy")
            if not is_exit:
                _clear_side(side)
                continue
            if _maker_touched(side, px, float(seg_low or 0.0),
                              float(seg_high or 0.0),
                              float(seg_sell or 0.0), float(seg_buy or 0.0)):
                _apply(side, _vpx(state.symbol, px), abs(pos0), "pp_exit",
                       0.0, True)
                dec.skip = "pp_exit"
                filled = True
            continue
        # 空仓 ⇒ 两张都是进场单：先做穿透前撤单检查，再判成交。
        if _entry_cancel(side, px):
            _clear_side(side)
            dec.skip = f"pp_cancel({side})"
            continue
        if not _maker_touched(side, px, float(seg_low or 0.0),
                              float(seg_high or 0.0),
                              float(seg_sell or 0.0), float(seg_buy or 0.0)):
            continue
        ahead = _pp(side, "ahead")
        vol_at = float(vol_at_bid if side == "buy" else vol_at_ask or 0.0)
        if ahead > 0:
            cum = _pp(side, "cum") + max(vol_at, 0.0)
            _set_pp(side, "cum", cum)
            queue_ok = cum > ahead
        else:
            queue_ok = True
        if not queue_ok:
            continue
        cap = _entry_cap(px)
        qty = (cap / px) if px > 0 and cap > 0 else 0.0
        qty = _vqty(state.symbol, qty)
        if qty > 0:
            _apply(side, _vpx(state.symbol, px), qty, "pp_entry", 0.0, False)
            dec.skip = "pp_entry"
            filled = True

    pos = float(state.qty or 0.0)

    # ══ 有仓：一进一出 ──────────────────────────────────────────────
    if abs(pos) > 1e-12:
        # 灰尘清扫（沿用旧口径：名义低于交易所最小下单额 $5 的残渣归零）
        if mid > 0 and abs(pos) * mid < 5.0:
            state.qty = 0.0
            state.avg_px = state.avg_mid = state.opened_ts = 0.0
            state.opened_ts_true = 0.0
            state.quote_bid = state.quote_ask = state.quote_ts = 0.0
            state.flow_mu = 0.0
            state.flow_hold_sec = 0.0
            try:
                state.pp_rest_until = float(now) + _rest_sec(pp_rest_sec)
            except Exception:  # noqa: BLE001
                pass
            dec.skip = "dust_swept"
            dec.action = "pause"
            return None
        # 吃单判定：只认「盘口死了」与「真跳空（灾难止损档）」两种。
        # 其余（时间/优势/止盈/风险阶梯）一律忽略 —— 就是继续挂那张反向平仓单。
        act = choose_exit(
            qty=pos, entry_px=float(state.avg_px or 0.0), bid=bid, ask=ask,
            now_ts=float(now), opened_ts=float(state.opened_ts or 0.0),
            max_hold_sec=0.0, mu=0.0, vol_300s_bp=float(vol_300s_bp or 0.0),
            regime=str(regime or ""), book_stale=bool(book_stale),
            stop_floor_bp=stop_floor_bp, stop_cap_bp=stop_cap_bp, tp_bp=0.0,
        )
        if act == "no_book":
            if float(state.opened_ts or 0.0) > 0 \
                    and float(now) - float(state.opened_ts) > 300.0 \
                    and float(state.avg_mid or 0.0) > 0:
                side = "sell" if pos > 0 else "buy"
                _apply(side, _vpx(state.symbol, float(state.avg_mid)), abs(pos),
                       "taker_no_book", float(taker_fee_bp or 0.0), True)
                return None
            dec.skip = "exit_no_book"
            return None
        if act == "taker_stop":
            side = "sell" if pos > 0 else "buy"
            px = bid if side == "sell" else ask
            if px <= 0:
                dec.skip = "exit_no_book"
                return None
            _apply(side, _vpx(state.symbol, px), abs(pos), "taker_stop",
                   float(taker_fee_bp or 0.0), True)
            return None
        # 反向平仓单：多头 → 卖 @ 卖一 + N tick；空头 → 买 @ 买一 − N tick。
        # 不改成对手价；离场单不撤（穿透前撤单规则只对进场单生效）。
        _tick_n = tick * _exit_ticks(pp_exit_ticks) if tick > 0 else 0.0
        if pos > 0:
            side, px_exit = "sell", (ask + _tick_n if _tick_n > 0 else ask)
            if px_exit < ask * (1.0 - 1e-12):
                px_exit = ask
        else:
            side, px_exit = "buy", (bid - _tick_n if _tick_n > 0 else bid)
            if px_exit > bid * (1.0 + 1e-12):
                px_exit = bid
        px_exit = _vpx(state.symbol, px_exit)
        qty_exit = _vqty(state.symbol, abs(pos))
        if px_exit > 0 and qty_exit > 0 and _vp(
                state.symbol, px_exit, qty_exit, mid, reduce_only=True)[0]:
            _arm_side(side, px_exit, qty_exit, ahead_qty=0.0, front_qty=0.0,
                      reduce_only=True)
            dec.action = "quote"
            if not filled:
                dec.skip = "pp_exit"
                dec.exit_path = "pp_exit"
            return None
        # 挂不了（venue 拒）⇒ 保留已有挂单原样；没有则暂停等下一拍。
        if float(state.quote_bid or 0.0) > 0 and pos < 0:
            _echo_quote("buy", float(state.quote_bid), qty_exit, True)
        if float(state.quote_ask or 0.0) > 0 and pos > 0:
            _echo_quote("sell", float(state.quote_ask), qty_exit, True)
        dec.action = "quote" if (dec.bid or dec.ask) else "pause"
        if not filled:
            dec.skip = "pp_holding"
        return None

    # ══ 空仓：歇息 / 闸门 / 双侧后一档进场 ──────────────────────────
    try:
        _rest_until = float(getattr(state, "pp_rest_until", 0.0) or 0.0)
    except (TypeError, ValueError):
        _rest_until = 0.0
    if _rest_until > float(now):
        state.quote_bid = state.quote_ask = 0.0
        dec.action = getattr(dec, "action", "") or "pause"
        if not filled:
            dec.skip = "pp_rest"
        return None
    if flow_exit_only:
        state.quote_bid = state.quote_ask = 0.0
        dec.action = getattr(dec, "action", "") or "pause"
        if not filled:
            dec.skip = "flow_exit_only(pp)"
        return None
    if (regime in ("R4", "R5") and not _allow_r45()) or book_stale:
        state.quote_bid = state.quote_ask = 0.0
        if not filled:
            dec.skip = f"regime_{regime or 'stale'}_no_entry"
        return None
    if bid <= 0 or ask <= 0 or mid <= 0:
        state.quote_bid = state.quote_ask = 0.0
        if not filled:
            dec.skip = "no_book(pp)"
        return None
    px_buy = _vpx(state.symbol, bid - tick) if tick > 0 else _vpx(state.symbol, bid)
    px_sell = _vpx(state.symbol, ask + tick) if tick > 0 else _vpx(state.symbol, ask)
    # 永不进价差：任何舍入都不允许买高于买一、卖低于卖一。
    if px_buy > bid * (1.0 + 1e-12):
        px_buy = _vpx(state.symbol, bid)
    if px_sell < ask * (1.0 - 1e-12):
        px_sell = _vpx(state.symbol, ask)
    # ── [2026-10-09 进化重挂] 桶级状态门：这种盘口状态这一侧历史为负 ⇒
    # 该侧这一拍休息（做/歇口径，不猜方向）。文件缺失/样本不足 fail-open。
    _spread_bp = (ask - bid) / mid * 1e4 if mid > 0 and ask > bid else 0.0
    _min_n = _bucket_min_n(pp_bucket_min_n)
    skip = ""
    blocked: list = []
    for side, px_q, front in (("buy", px_buy, bid_qty), ("sell", px_sell, ask_qty)):
        if px_q <= 0:
            continue
        if _bucket_gate_on():
            try:
                _ahead_usd = float(front or 0.0) * (bid if side == "buy" else ask)
                if pp_situation.side_blocked(pp_sit_doc, state.symbol, float(now),
                                             _spread_bp, _ahead_usd, side,
                                             min_n=_min_n):
                    blocked.append(f"pp_bucket_block({side})")
                    continue
            except Exception:  # noqa: BLE001
                pass
        cap = _entry_cap(px_q)
        if cap <= 0:
            continue
        qty = _vqty(state.symbol, cap / px_q)
        if qty <= 0:
            continue
        if not _vp(state.symbol, px_q, qty, mid)[0]:
            skip = "venue_filter(pp)"
            continue
        _arm_side(side, px_q, qty, ahead_qty=float(front or 0.0),
                  front_qty=float(front or 0.0), reduce_only=False)
        skip = "pp_entry"
    dec.action = "quote" if (dec.bid or dec.ask) else (
        getattr(dec, "action", "") or "pause")
    # 结算里留下的撤单标记优先保留（观测穿透前撤单频率），否则写本拍结论。
    # 桶门拦截与挂单并存时两者都可见（"+".join）。
    if not str(dec.skip or "").startswith("pp_cancel"):
        _final_skip = "+".join(blocked) if blocked else (skip or "pp_no_quote")
        dec.skip = _final_skip
    if not filled:
        dec.exit_path = ""
    return None
