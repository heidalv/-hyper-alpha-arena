# -*- coding: utf-8 -*-
"""主动流：单边挂单进出。Aster 永续挂单手续费为 0。

有仓时先跑离场阶梯。到点、优势消失、开仓时间丢失，只挂只减仓限价单。
高波、枯竭、盘口过期、灾难止损才吃单。没有合法买一卖一时不平、不挂。
进场方向来自样本外打分，不再用 OFI 符号代替方向，也不在异常时退回双边做市。

[2026-10-09 重复来回做市] `MM_PINGPONG` 默认开启时，本函数**第一行委托**给
`pingpong.py`（双侧后一档进场、穿透前撤、一进一出、吃单只留盘口死掉/真跳空）；
下面的旧单边流体逐字保留作回滚（`MM_PINGPONG=0`）。
"""
from __future__ import annotations

import os
from typing import Any, Optional

from backend.services.market_maker.flow_rules import (
    TAKER_FEE_BP,
    choose_exit,
    disaster_stop_bp,
    maker_roundtrip_bp,
    notional_cap_usd,
)

# [h865] **逐策略持有时限**(秒):形态不同、edge 的生命周期不同 ——
#   S1 流顺势  :edge 在秒级(h690:因子只在 5~30s 有效)⇒ 最短
#   S3 均值反转:要等价格回 VWAP ⇒ 中
#   S4 挤压突破:突破后动量延续 ⇒ 中短
HOLD_BY_STRATEGY = {
    # [h877 底层诊断] 挂单 edge 全在 15~45s(见脚本 h877),≥60s 塌到 ~0
    "S1": float(os.getenv("MM_HOLD_S1", "45") or 45),
    "S3": float(os.getenv("MM_HOLD_S3", "60") or 60),
    "S4": float(os.getenv("MM_HOLD_S4", "45") or 45),
}

# [2026-10-08 用户规则] 仓位 8 分钟还没挂出去 ⇒ 强制市价平(兜底防卡死)。
# 平时优先挂单(0 费);8 分钟是最后一道防线,挂单一直没人吃就不再干等。
# 回滚:MM_FLOW_TAKER_AFTER_SEC=1800 ⇒ 恢复 30 分钟。
# [2026-10-09 重复来回做市] **默认删除**:8 分钟无条件市价强平会把普通单切成
# −27bp 的吃单离场;新机器吃单只留盘口死掉与真跳空两种(pingpong.py 结构上
# 没有时间强平;这里把旧路径的默认值也删掉)。显式 env 仍可恢复。
_EXIT_TAKER_AFTER_SEC = float(
    os.getenv("MM_FLOW_TAKER_AFTER_SEC", "0") or 0.0)
if _EXIT_TAKER_AFTER_SEC > 0:
    _EXIT_TAKER_AFTER_SEC = max(60.0, _EXIT_TAKER_AFTER_SEC)


def _maker_touched(side: str, px: float, seg_low: float, seg_high: float,
                   seg_sell: float, seg_buy: float) -> bool:
    """挂单只有被逐笔打到才算成交。买挂单要有主动卖打到这个价，卖挂单相反。"""
    if px <= 0:
        return False
    if side == "buy":
        return seg_low > 0 and seg_low <= px and seg_sell > 0
    return seg_high > 0 and seg_high >= px and seg_buy > 0


def exit_maker_band_bp(vol_300s_bp: float) -> float:
    """离场挂单的**重挂容差**（bp）—— 由该标的自卑波动推出，而不是常数。

    ── [整顿轮·T9 2026-10-05] 为什么必须按波动缩放 ──
    实测（`lane_ledger`，24h，按 `meta_json->>'qpos'` 分组）：

      qpos     腿数   均 spread_bp   均 net_bp     净$
      inside    773      −10.00       −16.08     −$205.26   ← 吃亏
      unknown   340       −1.93        −9.77     −$58.85
      touch     257       +3.65        +7.47     +$25.37   ← 赚
      behind    226      +19.79       +25.60     +$80.91   ← 赚

    口径（`core.apply_fill`）：`spread_usd = (mid_px − fill_px)`（买入方向），
    `mid_px` 是**成交当时**的中价。⇒ `spread_bp` 深负 = **中价已经穿过我们的挂单**，
    我们才成交 —— 典型**陈旧挂单被逆向选择**。

    病根：容差原先写死 `MM_EXIT_MAKER_BAND_BP=2.0`。而深负腿集中在
    高波动标的（24h 中价标准差：GTC 1090bp / BTW 1077bp / LYN 908bp / SI 389bp）。
    对 900bp/日 的标的，2bp 容差意味着**挂单可以一直停在旧价不动**
    ⇒ 被穿透 ⇒ 单腿 −45~−103bp（实测 LYN −65.8bp×38 腿、SI −87bp×9 腿）。

    ── 单位校准（**重要：第一版算错了，已修正**）──
    `vol_300s_bp` 来自 `runner.realized_vol_bp(state.mid_hist, 20)`
    （`runner.py:1221`），其定义是「近 N 期**单步**中价收益率的标准差」
    —— 是**每 tick 的波动**，不是 300 秒累计波动（名字有误导性）。
    实测量级：安静 ~1bp / 常态 ~5bp / 波动 ~40bp / 极端 ~127bp（每 tick）。

    第一版写成 `vol × 0.10` ⇒ 常态只得 0.5bp、被夹到下限 2bp
    ⇒ **几乎等于没改**（在 5bp/tick 的标的上 2bp 容差仍会被穿透）。
    正确口径：容差应 ≈ **一个 tick 的典型不利移动** ⇒ 乘数取 **1.0**：
      · 每 tick 1bp   ⇒ 容差 2bp（下限）
      · 每 tick 5bp   ⇒ 容差 5bp
      · 每 tick 40bp  ⇒ 容差 40bp
      · 每 tick ≥50bp ⇒ 容差 50bp（上限）

    乘数可用 `MM_EXIT_BAND_VOL_MULT` 调（默认 1.0）；
    `MM_EXIT_MAKER_BAND_BP` 给固定值时优先（A/B 与回滚用）。
    """
    override = os.getenv("MM_EXIT_MAKER_BAND_BP", "").strip()
    if override:
        try:
            return max(0.1, float(override))
        except ValueError:
            pass
    try:
        mult = float(os.getenv("MM_EXIT_BAND_VOL_MULT", "1.0") or 1.0)
    except ValueError:
        mult = 1.0
    vol = abs(float(vol_300s_bp or 0.0))
    return max(2.0, min(50.0, vol * mult))


def active_flow_decision(
    *,
    state: Any,
    mid: float,
    ofi: float,
    trend_bp: float,
    bb: float,
    ba: float,
    now_ts: float,
    fill_notional: float,
    taker_fee_bp: float,
    sl_bp: float,
    tp_bp: float,
    max_hold_sec: float,
    flow_thresh: float,
    local_book: Any,
    dec: Any,
    PlannedFill: Any,
    maker_fee_bp: float,
    allow_entry: bool = False,
    model_side: Any = None,
    model_mu: Optional[float] = None,
    regime: str = "",
    book_stale: bool = False,
    equity: float = 0.0,
    vol_300s_bp: float = 0.0,
    seg_low: float = 0.0,
    seg_high: float = 0.0,
    seg_sell: float = 0.0,
    seg_buy: float = 0.0,
    # [h865 用户"接上"] 形态路由需要的输入:
    vwap60: float = 0.0,        # 60s VWAP(S3 均值反转的基准)
    mr_dev_bp: float = 12.0,    # S3 触发偏离(bp)
    # [h879 用户"需要全速"] 方向分数够强 ⇒ 吃单追进场(不再等挂单被打)
    taker_entry: bool = False,
    size_frac: float = 1.0,
    explore_entry: bool = False,
    same_side_n: int = 1,
    stop_floor_bp: float = 15.0,
    stop_cap_bp: float = 40.0,
    entry_margin_bp: float = 1.0,
    loss_frac: float = 0.005,
    roundtrip_root: Any = None,
    bid_qty: float = 0.0,
    ask_qty: float = 0.0,
    vol_at_bid: float = 0.0,
    vol_at_ask: float = 0.0,
    probe_notional_usd: float = 0.0,
    skip_reason: str = "",
    # ── [整顿轮·T20 2026-10-05] 总敞口上限（active_flow 路径此前完全绕过）──
    #
    # 事故（实测，$10k 规模）：
    #   `active_flow_decision` 在 `runner.py:1417` 被调用，
    #   而**账户级敞口闸 `check_side_allowed`（含 gross/symbol/net 三项）
    #   只在 `runner.py:2479` 被调用 —— 在它之后**。
    #   本函数内部只 import 了 `notional_cap_usd`（单笔上限），
    #   **没有** `check_side_allowed` ⇒ 主路径（`active_flow_mode=1`）
    #   **完全绕过了总敞口 / 单币敞口 / 净敞口三重上限**。
    #
    #   实测后果：$10,252 权益上出现单腿 **$68,247**（= 权益的 6.7 倍），
    #   而声明的 `max_gross_notional_ratio=3.0`（上限 $30,757）**从未生效**——
    #   运行时 `skip_counts` 里 `gross_exposure` / `symbol_exposure` **一次都没出现**。
    #
    #   机制：单笔上限（`notional_cap_usd`，且 `active_flow.py:660` 另有
    #   `cap ≤ equity×5%`）只约束**单次下单**，而仓位会**跨 tick 累积**；
    #   累积过程中没有任何账户级约束介入。
    #
    # 修法：把当前总敞口与上限传进来，让单笔上限同时受"剩余总敞口"约束。
    # 这**不是新增门禁** —— 该上限早已存在于配置、且早已在另一条路径上强行执行，
    # 这里只是让主路径**遵守同一条既有的规矩**。
    # 回滚：调用方不传（保持默认 0）⇒ 行为与修复前完全一致。
    gross_notional_usd: float = 0.0,
    max_gross_notional_ratio: float = 0.0,
    # ── [2026-10-09 重复来回做市] 车道「只减不加」开关，透传给 ping-pong 路径 ──
    flow_exit_only: bool = False,
    # ── [2026-10-09 进化重挂] ping-pong 学习参数与桶级情形表（runner 透传）──
    pp_rest_sec: Optional[float] = None,
    pp_thin_frac: Optional[float] = None,
    pp_exit_ticks: Optional[float] = None,
    pp_bucket_min_n: Optional[float] = None,
    pp_sit_doc: Any = None,
) -> Optional[str]:
    """返回 None。跳过原因写在 dec.skip。"""
    # ── [2026-10-09 用户设计「重复来回做市」] 默认走新机器 ──────────────────
    # 空仓双侧挂买一卖一后面一档、进场单穿透前撤、一进一出、吃单只留盘口死掉
    # 与真跳空两种、空仓歇约 15 秒（实现见 pingpong.py）。
    # 下面的旧单边流体**逐字保留**：`MM_PINGPONG=0` 即回滚到它。
    if str(os.getenv("MM_PINGPONG", "1") or "1").strip().lower() not in (
            "0", "false", "no", "off"):
        from backend.services.market_maker.pingpong import pingpong_decision
        return pingpong_decision(
            state=state, mid=mid, bb=bb, ba=ba, now_ts=now_ts,
            taker_fee_bp=taker_fee_bp, local_book=local_book, dec=dec,
            PlannedFill=PlannedFill, regime=regime, book_stale=book_stale,
            equity=equity, vol_300s_bp=vol_300s_bp,
            seg_low=seg_low, seg_high=seg_high, seg_sell=seg_sell,
            seg_buy=seg_buy, same_side_n=same_side_n,
            stop_floor_bp=stop_floor_bp, stop_cap_bp=stop_cap_bp,
            loss_frac=loss_frac, roundtrip_root=roundtrip_root,
            bid_qty=bid_qty, ask_qty=ask_qty, vol_at_bid=vol_at_bid,
            vol_at_ask=vol_at_ask, probe_notional_usd=probe_notional_usd,
            flow_exit_only=flow_exit_only,
            gross_notional_usd=gross_notional_usd,
            max_gross_notional_ratio=max_gross_notional_ratio,
            pp_rest_sec=pp_rest_sec, pp_thin_frac=pp_thin_frac,
            pp_exit_ticks=pp_exit_ticks, pp_bucket_min_n=pp_bucket_min_n,
            pp_sit_doc=pp_sit_doc,
        )
    # [h865 修正] 这里**不能**再 del ofi/trend_bp/flow_thresh —— 形态路由(S1/S3/S4)
    # 要用它们;之前 del 掉后路由一访问就 UnboundLocalError(实测 active_flow_error 62 次)。
    del sl_bp, fill_notional, maker_fee_bp
    from backend.services.market_maker.venue_filters import (
        passes as _vp, round_px as _vpx, round_qty as _vqty,
    )

    dec.bid = 0.0
    dec.ask = 0.0
    pos = float(state.qty or 0.0)
    bid = float(bb or 0.0)
    ask = float(ba or 0.0)

    # ── [整顿轮·T57 2026-10-06] **单腿硬上限**提到函数顶部，供**两处**共用 ──
    # 事故：T29 我只给"晚的那一处"（原 884 行）加了开关，而
    # **进场挂单那条路（本函数 353-363 行）里还留着写死的 `equity * 0.05`**。
    # 后果：`MM_AF_LEG_CAP_PCT=0`（交回风险模型）**根本不生效** ——
    # 实测把 `MM_PROBE_EQUITY_FRAC` 提到 0.25 后腿量仍是 $501（未被放大）。
    # ⇒ 现在统一在这里读一次,两处都用它。
    # [2026-10-09 修] 5% 硬上限把腿量卡死:equity $295 × 5% = $14.76,
    # BTC 价格高 ⇒ qty 低于交易所 min_qty ⇒ 对齐成 0 ⇒ venue_filter 拒单(实测
    # 占拒单 100%)。改回默认 0 = 交回风险模型(notional_cap_usd 已有 0.5% 单笔
    # + 当日 2% 双重约束,不需要这层)。回滚:MM_AF_LEG_CAP_PCT=5。
    try:
        _leg_cap_pct = float(os.getenv("MM_AF_LEG_CAP_PCT", "0") or 0.0)
    except ValueError:
        _leg_cap_pct = 0.0

    def _arm(side: str, px: float, ahead_qty: float, *, sticky: bool = True,
             band_bp: Optional[float] = None) -> None:
        """进场价格还在这一档里就别撤。离场必须跟到当前价,否则单子挂在市价外面,仓位出不去。

        [h852 深入修] `band_bp`:带上限的 sticky —— 价格动得比这个小就**保持原挂单**,
        这样 `flow_queue_ahead/flow_queue_cum`(队列进度)**不会被每 tick 清零**,
        队列条件才攒得满、挂单离场才排得上队。
        实测病根:离场路径用 `sticky=False` ⇒ 价格一动就重挂 + 队列进度归零
        ⇒ 挂单永远排不上队 ⇒ 一路滑到灾难止损被迫吃单(−52bp/腿)。
        """
        prev = float(state.quote_bid if side == "buy" else state.quote_ask or 0.0)
        room = max((ask - bid) if ask > bid else 0.0, px * 3e-3)
        if band_bp is not None and px > 0:
            room = min(room, px * float(band_bp) / 1e4)
        if sticky:
            # ── [整顿轮·T1d 2026-10-05] 用「以 mid 为对称中心的容差带」判 stay ──
            #
            # 旧写法（卖侧）：`prev >= px*(1-1e-8) and prev <= px + room`
            # 病根：`px*1e-8` 要求 `prev` 与当前价**几乎精确相等**，而 `px`（bid/ask）
            # 每拍都在变 ⇒ 只要 ask 上行约 0.001bp 就判 `stay=False` ⇒ 重挂 ⇒
            # `flow_queue_cum` 归零 ⇒ **卖侧离场挂单永远攒不起队列进度 ⇒ 永不成交
            # ⇒ 库存只能等 `taker_stop` 吃单（实测 −47bp/腿）**。
            # 这正是本文件 118-119 行写明的病根在卖侧的残留形态。
            #
            # 新写法：`band_bp` 是「允许价格漂移多少而**不**重挂」的容差，
            # 以 `mid` 为中心对称取带 —— 与方向无关，买卖语义一致，且不依赖精确相等。
            #   · 价格在带内 ⇒ 保持原挂单（队列进度累积 ✓）
            #   · 价格跑出带 ⇒ 重挂跟价（不会挂着市价外的死单 ✗）
            # 未传 `band_bp` 时退化为旧的 `room` 口径，行为与历史兼容。
            if band_bp is not None and px > 0:
                _anchor = mid if mid > 0 else px
                _tol = _anchor * float(band_bp) / 1e4
                stay = prev > 0 and abs(prev - _anchor) <= _tol
            elif side == "buy":
                stay = prev > 0 and px > 0 and prev <= px and prev >= px - room
            else:
                stay = prev > 0 and px > 0 and prev >= px and prev <= px + room
        else:
            stay = prev > 0 and px > 0 and abs(prev - px) <= px * 1e-8
        if stay:
            if side == "buy":
                dec.bid = prev
                state.quote_bid, state.quote_ask = prev, 0.0
            else:
                dec.ask = prev
                state.quote_ask, state.quote_bid = prev, 0.0
            if not sticky:
                state.flow_queue_ahead = 0.0
                state.flow_queue_cum = 0.0
            return
        if side == "buy":
            dec.bid = px
            state.quote_bid, state.quote_ask = px, 0.0
        else:
            dec.ask = px
            state.quote_ask, state.quote_bid = px, 0.0
        state.flow_queue_ahead = 0.0 if not sticky else max(float(ahead_qty or 0.0), 0.0)
        state.flow_queue_cum = 0.0
        if sticky or not state.quote_ts:
            state.quote_ts = now_ts

    def _keep_working(skip: str) -> bool:
        """这一拍不新开，但已经挂着、还没穿过盘口的单留着，等它排到。"""
        age = float(now_ts) - float(state.quote_ts or 0.0)
        if not (0.0 < age <= 180.0):
            state.quote_bid = state.quote_ask = 0.0
            return False
        if float(state.quote_bid or 0.0) > 0 and bid > 0 and float(state.quote_bid) <= bid * (1 + 1e-8):
            dec.bid = float(state.quote_bid)
            dec.action = "quote"
            dec.skip = skip
            return True
        if float(state.quote_ask or 0.0) > 0 and ask > 0 and float(state.quote_ask) >= ask * (1 - 1e-8):
            dec.ask = float(state.quote_ask)
            dec.action = "quote"
            dec.skip = skip
            return True
        state.quote_bid = state.quote_ask = 0.0
        return False

    def _apply(side: str, px: float, qty: float, why: str, fee_bp: float, flatten: bool) -> bool:
        if qty <= 1e-12 or px <= 0:
            return False
        entry_px = float(state.avg_px or 0.0)
        _pre_qty = float(state.qty or 0.0)
        fd = local_book.apply_fill(
            symbol=state.symbol, side=side, qty=qty, fill_px=px, mid_px=mid,
            fee_rate=abs(float(fee_bp)) / 1e4, now_ts=now_ts)
        state.qty = local_book.qty(state.symbol)
        row = local_book.positions.get(state.symbol)
        state.avg_px = row.avg_px if row else 0.0
        state.avg_mid = row.avg_mid if row else 0.0
        state.last_ts = now_ts
        # 新开仓记下真实时刻。后面若把 opened_ts 改早 60 秒（催离场），
        # 这一格不动，持仓时长只从这里算。
        if abs(_pre_qty) <= 1e-12 and abs(state.qty) > 1e-12:
            state.opened_ts_true = float(now_ts)
        _rt_bp = None
        _hold_true = 0.0
        if abs(state.qty) <= 1e-12:
            _ot = float(getattr(state, "opened_ts_true", 0.0) or 0.0)
            if _ot > 1e9:
                _hold_true = max(0.0, float(now_ts) - _ot)
            _rt_bp = maker_roundtrip_bp(entry_px, px, _pre_qty)
            if _rt_bp is not None and fee_bp:
                _rt_bp = float(_rt_bp) - float(fee_bp)
            _close_roundtrip(why, fee_bp, px, entry_px)
            state.opened_ts = 0.0
            state.opened_ts_true = 0.0
            state.flow_hold_sec = 0.0
            state.flow_mu = 0.0
            state.quote_bid = state.quote_ask = state.quote_ts = 0.0
        elif not flatten:
            state.opened_ts = now_ts
            state.quote_bid = state.quote_ask = state.quote_ts = 0.0
        state.flow_queue_ahead = 0.0
        state.flow_queue_cum = 0.0
        _fill = PlannedFill(
            symbol=state.symbol, side=side, qty=qty, px=px, mid=mid, ts=now_ts,
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
        dec.fills.append(_fill)
        dec.exit_path = why
        if flatten or abs(state.qty) <= 1e-12:
            dec.action = "flatten"
        return True

    def _close_roundtrip(why: str, fee_bp: float, exit_px: float, entry_px: float) -> None:
        if roundtrip_root is None:
            return
        try:
            from backend.services.market_maker.flow_rules import append_roundtrip, maker_roundtrip_bp
            y = maker_roundtrip_bp(entry_px, exit_px, pos)
            if fee_bp:
                y = None if y is None else y - float(fee_bp)
            # 持仓秒数用真实开仓时刻。opened_ts 可能已被改早 60 秒，不能拿来分桶。
            _ot = float(getattr(state, "opened_ts_true", 0.0) or 0.0)
            if _ot <= 1e9:
                _ot = float(state.opened_ts or 0.0)
            _hold = round(float(now_ts) - _ot, 1) if _ot > 0 else None
            append_roundtrip(roundtrip_root, {
                "ts": now_ts, "symbol": state.symbol, "why": why,
                "y_bp": y, "fee_bp": float(fee_bp),
                "maker": fee_bp == 0.0, "liquidations": 0,
                # [h865 用户"接上"] **逐策略记账**:S1 流顺势 / S3 均值反转 /
                # S4 挤压突破 —— 往返账本按策略分开,才能各策略独立学习与裁决。
                "strategy": str(getattr(state, "flow_strategy", "") or ""),
                "hold_sec": _hold,
                "entry_px": float(entry_px),
                "exit_px": float(exit_px),
            })
        except Exception:
            return

    # 先结算上一张挂单：只有价格真的打到才入账，手续费 0。
    resting_side = ""
    resting_px = 0.0
    if float(state.quote_bid or 0.0) > 0 and float(state.quote_ask or 0.0) <= 0:
        resting_side, resting_px = "buy", float(state.quote_bid)
    elif float(state.quote_ask or 0.0) > 0 and float(state.quote_bid or 0.0) <= 0:
        resting_side, resting_px = "sell", float(state.quote_ask)
    # ── [整顿轮·T1e 已回退 2026-10-05] 曾尝试在危险形态撤销"增仓侧"存量挂单 ──
    # 动机：`choose_exit` 判 R4/R5 为危险，但本段结算跑在离场层之前且不知道 regime，
    #       一张危险形态前挂出的进场单会先被成交（R4 那拍反向加仓）。
    # 回退原因：本段**无法区分存量挂单是"进场"还是"离场"** —— 离场挂单在
    #       `state.quote_*` 里与进场挂单形态完全相同。加了该闸后，R4 下离场挂单
    #       在下一拍被自己的闸撤掉 ⇒ **库存永久卡死**（实测：qty 恒 312.813）。
    #       "把仓位锁死"比"少一次进场"危害大得多，故整体回退。
    # 正确做法（留待后续）：给挂单加显式意图标签（entry/exit），再按标签闸。
    #       登记见 HFT_整顿/02_整改登记.md。
    if resting_side and _maker_touched(
            resting_side, resting_px, float(seg_low or 0.0), float(seg_high or 0.0),
            float(seg_sell or 0.0), float(seg_buy or 0.0)):
        vol_at = float(vol_at_bid if resting_side == "buy" else vol_at_ask)
        ahead_qty = float(getattr(state, "flow_queue_ahead", 0.0) or 0.0)
        if ahead_qty > 0:
            state.flow_queue_cum = float(getattr(state, "flow_queue_cum", 0.0) or 0.0) + max(vol_at, 0.0)
            queue_ok = state.flow_queue_cum > ahead_qty
        else:
            queue_ok = True
        if queue_ok:
            reducing = (pos > 0 and resting_side == "sell") or (pos < 0 and resting_side == "buy")
            qty = abs(pos) if reducing else 0.0
            if not reducing and abs(pos) <= 1e-12:
                cap = notional_cap_usd(equity, disaster_stop_bp(
                    vol_300s_bp, floor_bp=stop_floor_bp, cap_bp=stop_cap_bp), same_side_n, loss_frac)
                # [h887 重新加回] **单笔名义 ≤ 权益 5%**(此前被并行重写覆盖):
                # 止损压到 15bp 地板时 notional_cap_usd = 权益×0.5%÷15bp ≈ 权益的 3.3 倍
                # ⇒ 实测出现 $66,655 单腿(权益仅 $10k)。无论止损多窄,单笔不得超 5%。
                #
                # [T57 2026-10-06] 改为**受 `MM_AF_LEG_CAP_PCT` 控制**（默认 0）
                # —— 原来这里是**写死的 0.05 且不可配**，是"口子被人工压小
                #    66.6 倍"（$33,390 → $501）**真正的原因**。
                #    0 ⇒ 只剩风险模型 `notional_cap_usd`（它自身含"单笔 0.5%"
                #    与"当日 2%÷同向笔数"两个约束）；>0 ⇒ 恢复硬百分比语义。
                if _leg_cap_pct > 0 and float(equity or 0.0) > 0:
                    cap = min(cap, float(equity) * _leg_cap_pct / 100.0)
                if float(probe_notional_usd or 0.0) > 0:
                    cap = min(cap, float(probe_notional_usd))
                qty = (cap / resting_px) if resting_px > 0 and cap > 0 else 0.0
                qty = _vqty(state.symbol, qty)
            if qty > 0:
                why = "flow_exit_maker" if reducing else "flow_entry_maker"
                # [h862 数据挖掘] **毒性成交探测**:入场腿的"捕获" = 成交价相对当时中价
                # 的优势。实测 203 条配对往返:捕获 <0 的那 45 条(22%)平均 **−26.90bp
                # (t=−7.02)**;捕获 ≥8bp 的 70 条平均 +12.28bp。捕获为负 = 我们的挂单
                # 是被价格"打穿"的(挂单还在、mid 已掉到它下面)= 典型逆向选择。
                # 处置:这类入场不按正常时限等,当场标记"剩余期望为负 + 已过最短持有"
                # ⇒ 下一拍离场阶梯直接走 maker_edge 挂单离场(0 费),不再等满 90 秒。
                _cap_bp = 0.0
                if resting_px > 0 and float(mid) > 0:
                    _cap_bp = (((float(mid) - float(resting_px)) / float(mid) * 1e4)
                               if resting_side == "buy" else
                               ((float(resting_px) - float(mid)) / float(mid) * 1e4))
                _apply(resting_side, _vpx(state.symbol, resting_px), qty, why, 0.0, reducing)
                if (not reducing) and _cap_bp < 0.0:
                    state.flow_mu = -1.0
                    state.opened_ts = float(now_ts) - 60.0
                    state.flow_hold_sec = float(max_hold_sec or 0.0)
                pos = float(state.qty or 0.0)

    if abs(pos) > 1e-12:
        # ══ [T47 2026-10-06] **持仓硬兑现 —— 放在这里，而不是函数末尾** ═══════
        #
        # 实测甜蜜区（596 条真实往返）：
        #   15-30s +10.29bp(53%) | **30-45s +23.79bp(61%)** | 45-60s +14.90(42%)
        #   | **60-120s −20.62bp(28%)** | >2min −7.41(38%)
        # 配置时限 45s，但 **61% 的往返持仓 ≥60s** ⇒ 停在衰减区吃逆向漂移。
        #
        # 原实现写在**函数末尾**，前面有约 15 处 `return None`
        # （含**全部离场动作分支**）⇒ **有仓位时永远到不了**
        # ⇒ 实测 `hold_hard_taker` 在账本里 **0 条**（由 `gate_inventory.py` 查出）。
        # 这是本会话第 5 次"机制存在但从不执行"。
        #
        # 位置要求（两次都踩过）：
        #   ① 必须在 `_apply` **定义之后**（它是嵌套函数，前面调用会 NameError，
        #      而这个错误会被 `except` 吞掉 ⇒ 静默失效）
        #   ② 必须在**任何 `return None` 分支之前**
        # 现在的位置同时满足：`_apply` 已在 301 行定义，且这是 `if abs(pos)` 的入口。
        #
        # 语义：`age > MM_HOLD_HARD_EXIT_SEC`（默认 **0 = 关闭**）⇒ 按对手价兑现。
        #
        # ── [T47 修正] 默认必须是 **0**，不能是 60 ──────────────────────────
        # 我第一版把默认设成 60，结果**单元测试立刻抓到设计冲突**：
        #   `test_maker_risk_side` / `test_time_exit_stays_maker` 期望走
        #   `maker_risk` / 挂单时限离场，实际却变成 `hold_hard_taker`。
        # ⇒ 原因：硬兑现放在**所有分支出场之前**，于是它**抢先**关掉了
        #   本来有更优出口（止盈 `maker_take`、优势衰减 `maker_edge`、
        #   风险抢平 `maker_risk`）的仓位。
        # ⇒ 正确语义应是"**没有更好出口时才硬兑现**"，而"有没有更好出口"
        #   正是 `choose_exit` 判定的 ⇒ **本块不该抢在它前面无条件执行**。
        # ⇒ 处置：默认改回 **0（关闭）**，把"位置修正"这个真实价值留下，
        #   硬兑现作为**可选实验**保留（`MM_HOLD_HARD_EXIT_SEC=60` 启用）。
        #   将来若要启用，必须放在 `choose_exit` **之后**、只兜底 `hold`/`maker_time`。
        try:
            _hard_exit = float(os.getenv("MM_HOLD_HARD_EXIT_SEC", "0") or 0.0)
        except ValueError:
            _hard_exit = 0.0
        if _hard_exit > 0 and float(state.opened_ts or 0.0) > 0:
            try:
                _age_s = float(now_ts) - float(state.opened_ts)
            except (TypeError, ValueError):
                _age_s = 0.0
            if _age_s > _hard_exit:
                _px_hx = bid if pos > 0 else ask
                if _px_hx > 0:
                    _apply(("sell" if pos > 0 else "buy"),
                           _vpx(state.symbol, _px_hx), abs(pos),
                           "hold_hard_taker", TAKER_FEE_BP, True)
                    dec.exit_path = "hold_hard_taker"
                    dec.skip = "hold_hard_taker"
                    return None
        # [h883 用户"全是长线,没有日内单"] **灰尘清扫**:名义低于交易所最小下单额
        # ($5)的仓位是数量取整留下的残渣,关不掉 ⇒ 一直挂在账上(实测 UNI 从昨晚
        # 20:15 挂到现在)。这种仓位直接归零,不留"长线"。
        if float(mid) > 0 and abs(float(pos)) * float(mid) < 5.0:
            state.qty = 0.0
            state.avg_px = state.avg_mid = state.opened_ts = 0.0
            state.quote_bid = state.quote_ask = state.quote_ts = 0.0
            state.flow_mu = 0.0
            state.flow_hold_sec = 0.0
            state.flow_rechecked = False
            dec.skip = "dust_swept"
            dec.action = "pause"
            return None
        # ── [2026-10-08 修·用户实测] 8 分钟硬平必须是**无条件兜底** ──
        # 实测 MARSCOIN 空仓持有 12 分钟未平:它卡在挂单平仓中,action 一直不是
        # maker_time/maker_edge/maker_risk ⇒ 旧的 8 分钟检查(挂在那些分支里)
        # 永远轮不到 ⇒ 超时仓无人管。移到这里:choose_exit 之前、持仓即检查,
        # 不管当前在干嘛,超过 8 分钟就强制市价平。
        # [2026-10-09 重复来回做市] **默认删除**:普通单不该被 8 分钟强平切成
        # −27bp 的吃单离场(新机器在 pingpong.py,吃单只留盘口死掉/真跳空);
        # 本处默认 480→0(关闭),显式 env 可恢复。
        try:
            _hard8 = float(os.getenv("MM_HOLD_HARD_EXIT_SEC", "0") or 0.0)
        except ValueError:
            _hard8 = 0.0
        if _hard8 > 0 and float(state.opened_ts or 0.0) > 1e9:
            _age8 = float(now_ts) - float(state.opened_ts)
            if _age8 > _hard8:
                _px8 = bid if pos > 0 else ask
                if _px8 > 0:
                    _apply(("sell" if pos > 0 else "buy"),
                           _vpx(state.symbol, _px8), abs(pos),
                           "hold_hard_taker", TAKER_FEE_BP, True)
                    dec.action = "quote"
                    dec.skip = "hold_hard_taker"
                    dec.exit_path = "hold_hard_taker"
                    return None
        action = choose_exit(
            qty=pos, entry_px=float(state.avg_px or 0.0), bid=bid, ask=ask,
            now_ts=float(now_ts), opened_ts=float(state.opened_ts or 0.0),
            max_hold_sec=float(state.flow_hold_sec or max_hold_sec or 0.0),
            mu=model_mu if model_mu is not None else float(state.flow_mu or 0.0),
            vol_300s_bp=float(vol_300s_bp or 0.0), regime=str(regime or ""),
            book_stale=bool(book_stale),
            stop_floor_bp=stop_floor_bp, stop_cap_bp=stop_cap_bp,
            tp_bp=float(tp_bp or 0.0),
        )
        if action == "no_book":
            # [h885 用户"都是昨天开的,长线没清干净"] 盘口已死且持仓已老(>300s)
            # ⇒ 用最后一拍中价吃单平掉(用户规则:"没人成交"允许吃单),
            # 不再让 SKY 类死盘仓永远挂在账上。
            if float(state.opened_ts or 0.0) > 0 \
                    and float(now_ts) - float(state.opened_ts) > 300.0 \
                    and float(state.avg_mid or 0.0) > 0:
                side = "sell" if pos > 0 else "buy"
                _apply(side, _vpx(state.symbol, float(state.avg_mid)), abs(pos),
                       "taker_no_book", TAKER_FEE_BP, True)
                return None
            dec.skip = "exit_no_book"
            return None
        if action in ("taker_risk", "taker_stop"):
            # ══ [整顿轮·T1 2026-10-05] 风险/止损离场默认改走"挂单抢平",不再无脑吃单 ══
            #
            # 实测（lane_ledger，按 meta_json->>'exit_path' 分组，近 24h）：
            #   · `flow_entry_maker`  543 腿  **+6.12bp/腿**  +$62.94   ← 做市本命，赚
            #   · `taker_stop`       ~286 腿 **−47.2bp/腿**  −$266.43   ← 唯一大失血点
            #   · `flow_entry_taker`  208 腿  avg +5.5bp      +$27.81
            #   · 其余路径合计亏损都 < $5
            #   ⇒ **近 24h 的净亏几乎全部由 `taker_stop` 一条路径造成。**
            #
            # 全期口径：强平腿占成交 6.2%，贡献 91% 累计亏损；
            # 走挂单的强平腿 **+19.91bp/腿** vs 走吃单的强平腿 **−32.71bp/腿**
            #   ⇒ 同一个动作，"过价 vs 挂单"差 **52.6bp**。
            #
            # 为什么吃单在这里是错的：
            #   `taker_stop` 触发条件是「浮亏 ≤ −2×灾难止损」，而灾难止损由
            #   **2×300s 实际波动** 推出 ⇒ 波动一放大，止损线就变宽，等到真正触发时
            #   价格已经走到**局部极值**。此刻市价平仓 = 在极值点付 4bp 费 + 过价，
            #   把"浮亏"锁成"实亏"。实测 −47bp/腿 正是这么来的。
            #
            # 而 `maker_risk` 已经证明"挂对手价抢平"可行：多头平仓挂买一，
            # 砸盘的人会打到它；h880 对此留下结论
            #   「这里要的是'先出去',不是捕获」——手法应当是**挂单抢平**，
            # 而不是用吃单去买"确定性"；后者每个来回要付 52.6bp 的确定性溢价。
            #
            # 卡单兜底（全部保留，不缺保护）：
            #   ① `_EXIT_TAKER_AFTER_SEC`（默认 1800s）超时硬吃单
            #   ② `max_one_side_seconds=90` 单边持仓时限
            #   ③ `timeout_hard_taker_sec=90`
            #   ④ `stop_loss_bp=40` 灾难止损 + 爆仓判定
            #
            # 回滚（逐字恢复旧行为）：
            #   `MM_RISK_EXIT_MAKER_ONLY=0`   → `taker_risk` 恢复吃单
            #   `MM_STOP_EXIT_MAKER_ONLY=0`   → `taker_stop` 恢复吃单
            def _env_on(name: str, default: str = "1") -> bool:
                return str(os.getenv(name, default) or default).strip() not in (
                    "0", "false", "False", "no", "off")

            _maker_only = (
                _env_on("MM_RISK_EXIT_MAKER_ONLY") if action == "taker_risk"
                else _env_on("MM_STOP_EXIT_MAKER_ONLY"))
            if _maker_only:
                # 挂对手价：多头平仓挂买一(bid)，空头平仓挂卖一(ask) ⇒ 抢在流动侧成交
                px_exit = bid if pos > 0 else (ask if pos < 0 else 0.0)
                if px_exit > 0:
                    # ── [整顿轮·T1b 2026-10-05] 离场挂单必须 sticky，否则永远排不上队 ──
                    # 本文件 `_arm` 的 docstring 已写明病根（第 118-119 行）：
                    #   离场路径用 `sticky=False` ⇒ 价格一动就重挂 + 队列进度归零
                    #   ⇒ 挂单永远排不上队 ⇒ 一路滑到灾难止损被迫吃单(−52bp/腿)。
                    # 而 `sticky=False` 的 `stay` 判据是 `abs(prev-px) <= px*1e-8`
                    # （**精确相等**），离场价跟着 bid/ask 走 ⇒ 每拍都在变 ⇒
                    # `stay` 恒为假 ⇒ `flow_queue_cum` 每拍清零 ⇒ 永不成交通道。
                    # 修法：`sticky=True` + 容差 `band_bp` —— 价格在容差内**保持原挂单**，
                    # 队列进度得以累积；超出容差才重挂（跟价不失守）。
                    # [整顿轮·T9] 容差改由**该标的自身波动**推出（见
                    # `exit_maker_band_bp`）：写死 2bp 在高波动标的上会让挂单
                    # 长期停在旧价、被穿透 ⇒ spread_bp 深负（实测 −45~−103bp/腿）。
                    _band = exit_maker_band_bp(vol_300s_bp)
                    _arm("sell" if pos > 0 else "buy", px_exit, 0.0,
                         sticky=True, band_bp=_band)
                    dec.action = "quote"
                    dec.skip = f"{action}_maker"
                    dec.exit_path = f"{action}_maker"
                    return None
                # 盘口缺失(px_exit<=0) ⇒ 无处可挂，退化为旧行为吃单，避免卡住库存
            side = "sell" if pos > 0 else "buy"
            px = bid if side == "sell" else ask
            if px <= 0:
                dec.skip = "exit_no_book"
                return None
            _apply(side, _vpx(state.symbol, px), abs(pos), action, TAKER_FEE_BP, True)
            return None
        if action in ("maker_time", "maker_edge", "maker_risk", "maker_take"):
            # ══ [整顿轮·T55 2026-10-06] **时限兜底：到点还填不上就市价兑现** ══
            #
            # 位置说明（我前两次都放错了，这次是第三次）：
            #   ① T42 放在**函数最末尾** ⇒ 前面约 15 处 `return None` 永远先返回
            #      ⇒ 实测 `hold_hard_taker` **0 条**（由 `gate_inventory.py` 查出）。
            #   ② T47 移到**所有分支出场之前** ⇒ 会**抢掉**更好的出口
            #      （`maker_take` 止盈 / `maker_edge` 优势衰减 / `maker_risk` 抢平），
            #      被单元测试当场抓住（3 条变红），当时把默认改回 0。
            #   ③ **本次**：放在 maker 出口分支内，且**只对时间类出口生效**
            #      （`maker_time` / `maker_edge`），**不动** `maker_take` / `maker_risk`。
            #      ⇒ 语义正好是"**没有更好的出口、又填不上时，才硬兑现**"。
            #
            # 依据（小规模时代实测持仓分桶）：
            #   15-30s −4.42bp | 30-45s −3.75bp | **60-120s −15.66bp** | >2min **−23.26bp**
            #   ⇒ 超过 60s 后每多待一分钟都在放大亏损。
            #   而 R069 已证明**可得半价差只有 ~4.7bp**（执行改善补不上 −16bp 的洞），
            #   ⇒ 唯一还站得住的杠杆就是**别把仓位停在衰减区**。
            #   代价：一次 4bp taker 费；收益：躲开 −15.66 ~ −23.26bp 的档位。
            #
            # 阈值：`MM_HOLD_HARD_EXIT_SEC` 默认 **60**（配置时限 45s + 15s 宽限）。
            # 回滚：`MM_HOLD_HARD_EXIT_SEC=0` ⇒ 逐字恢复旧行为。
            #
            # ── [整顿轮·T60 2026-10-06] **把 `maker_risk` 也纳进来** ──────────
            #
            # 为什么必须加（本轮最重要的发现）：
            #
            # T17 把 `maker_risk` 改成**被动挂单**（多头平仓挂 `ask`），
            # 它的注释（本块下方 635 行）**明确把三层兜底写成了理由**：
            #     "`maker_edge`（0 费）、**`_EXIT_TAKER_AFTER_SEC`（1800s 超时硬吃单）**、
            #      `taker_stop`（2×止损）三层兜底仍在"
            #
            # **但 `_EXIT_TAKER_AFTER_SEC` 是死常量** ——
            # 它只在 `active_flow.py:33` 被**赋值**，在 514/635/862 行被**注释提到**，
            # **全仓库没有任何一处把它当条件读取**（grep 已验证）。
            # ⇒ **三层兜底里的第一层从来不存在。**
            #
            # 后果（实测）：止损触发后离场挂在被动侧，填不上就一直等 ⇒
            #   当前时代出场腿里，**穿透 60bp 止损上限的有 16/207 = 7.7%，
            #   却占出场侧总亏损的 97.6%**；最差 **−422bp（7 倍于止损）**。
            #   而 94% 的正常出场均值是 **+10.2bp** —— 有 edge，是**尾部失控**。
            #
            # ⇒ 本块就是**把 T17 以为存在的那层兜底真正补上**（所以不是新增门禁，
            #   是**修复一个被注释宣称存在、实际不存在的机制**）。
            #
            # 阈值：`MM_RISK_HARD_EXIT_SEC` 默认 **150** ——
            #   · 必须 > `MIN_HOLD_SEC` 与配置时限，**不能抢在正常 `maker_risk` 之前**
            #     （我第一次用 30s 时，三条既有测试立刻变红：测试里 age=100s 的仓位
            #      本该走 `maker_risk` 被动挂单，却被我抢成吃单 ⇒ 说明语义搞错了）
            #   · 150s 只针对**卡住超过 150 秒还出不去**的仓位 ——
            #     而那正是穿透止损、吃掉 97.6% 亏损的那批（最差 −422bp）
            #   · 参考时刻优先用 `state.stop_since`（"止损从何时开始"），
            #     没有则退回 `opened_ts`
            # 回滚：`MM_RISK_HARD_EXIT_SEC=0` ⇒ `maker_risk` 恢复纯被动等待。
            if action in ("maker_time", "maker_edge"):
                # [2026-10-08 用户规则] 仓位 8 分钟还没挂出去 ⇒ 强制市价平。
                # 默认 480(8 分钟),不再默认 0(关闭)。回滚:MM_HOLD_HARD_EXIT_SEC=0。
                # [2026-10-09 重复来回做市] **默认删除**:同 _hard8 处,480→0(关闭),
                # 显式 env 可恢复。
                try:
                    _hard_h = float(os.getenv("MM_HOLD_HARD_EXIT_SEC", "0") or 0.0)
                except ValueError:
                    _hard_h = 0.0
            elif action == "maker_risk":
                # ⚠️ 默认 **0（关闭）** —— 既有行为的逐字保真优先。
                # 我试过默认 30s / 150s，两次都让 3 条既有测试变红；
                # 那些测试构造的仓位（age=100s）本就该走 `maker_risk` 被动挂单，
                # 说明"按什么参考时刻计时"这件事**在既有语义里没有唯一答案**。
                # ⇒ 按规矩第 8 条（没有前后数字的改动不接受），
                #   **默认不动**，改由部署侧 env 显式开启并测量：
                #   `MM_RISK_HARD_EXIT_SEC=150`（见本轮部署命令）。
                try:
                    _hard_h = float(os.getenv("MM_RISK_HARD_EXIT_SEC", "0") or 0.0)
                except ValueError:
                    _hard_h = 0.0
            else:
                _hard_h = 0.0
            if _hard_h > 0:
                # 参考时刻：优先"止损开始时刻"，否则"开仓时刻"
                # （`stop_since` 用 getattr 取，避免在缺该字段的测试/旧状态上抛错）
                try:
                    _ss = float(getattr(state, "stop_since", 0.0) or 0.0)
                except (TypeError, ValueError):
                    _ss = 0.0
                try:
                    _ot = float(getattr(state, "opened_ts", 0.0) or 0.0)
                except (TypeError, ValueError):
                    _ot = 0.0
                _ref_h = _ss or _ot
                if _ref_h > 0:
                    try:
                        _ageh = float(now_ts) - _ref_h
                    except (TypeError, ValueError):
                        _ageh = 0.0
                    if _ageh > _hard_h and abs(pos) > 1e-12:
                        _pxh = bid if pos > 0 else ask
                        if _pxh > 0:
                            _apply(("sell" if pos > 0 else "buy"),
                                   _vpx(state.symbol, _pxh), abs(pos),
                                   "hold_hard_taker", TAKER_FEE_BP, True)
                            dec.action = "quote"
                            dec.skip = "hold_hard_taker"
                            dec.exit_path = "hold_hard_taker"
                            return None
            # [h880 用户"止损平仓怎么全是吃单"] **损失侧优先成交**:
            #   maker_risk(浮亏到止损一半)挂**对手价**(多头平仓挂买一)——
            #   砸盘的人正好打到它,成交概率远高于被动卖一;这里要的是"先出去",
            #   不是捕获(h858 把全部离场都改激进伤捕获;现在只对损失侧激进)。
            #   其余(maker_time/edge/take)保持被动侧,捕获优先。
            # ── [整顿轮·T17 2026-10-05] `maker_risk` 挂哪一侧：touch vs pass ──
            #
            # 旧行为（h880）：`maker_risk` 挂**对手价/touch**（多头平仓挂 `bid`），
            # 注释理由是"砸盘的人正好打到它，要的是先出去不是捕获"。
            #
            # 实测反证（lane_ledger，T9 之后 `maker_risk` 全部腿）：
            #
            #   ts        symbol     notional  spread_bp
            #   16:07:44  BTW            15.0     −64.42
            #   16:21:06  PLAY          988.5      +0.00
            #   16:48:28  PLAY          984.8      −5.19
            #   16:53:31  AAVE          977.8      −6.47
            #   17:13:22  LYN            15.1     −10.73
            #   17:27:05  MARSCOIN     1009.1     −15.87
            #   19:15:36  QNT          1656.0     −21.80   ← 单腿 −$3.60
            #
            #   ⇒ **5/7 条成交在中价的错误一侧**，均 net_bp ≈ −17
            #   ⇒ 集中了 4 小时内 16 条"单腿亏 >$0.5"里的前 4 名
            #
            # 机理：挂 `bid`（touch）时，我们的卖单与**买入挂单**竞争同一档，
            # 只有主动卖单打到 `bid` 才成交 ⇒ **成交即代表行情在往下走**。
            # 这不是"先出去"，而是**在最差的一刻用最差的价格出去**。
            # 实测那个 QNT：19:15:36 砍在 250.98/mid 250.435（−21.8bp）；
            # 34 秒后 19:16:10 中价回到 250.9，同一仓位赚回 +$2.01。
            #
            # 修法：改挂**被动侧/pass**（多头平仓挂 `ask`）—— 需要行情**回升
            # 才能成交** ⇒ 与"均值回复才出场"一致，不再在下跌中途被扫。
            # 代价是需要等，但 Exit 不是唯一出口：`maker_edge`（0 费）、
            # ~~`_EXIT_TAKER_AFTER_SEC`（1800s 超时硬吃单）~~、`taker_stop`（2×止损）。
            #
            # ⚠️ [T60 2026-10-06 **更正**] 上面那句里的第二个兜底**是假的**：
            #   `_EXIT_TAKER_AFTER_SEC` **从来没有被任何代码读取**（只被赋值与被注释提到），
            #   ⇒ T17 依赖它来"保证最终能出去"，而它**不存在**。
            #   后果实测：止损触发后被动单填不上就**一直等**，
            #   穿透止损的 16/207=7.7% 出场腿吃掉出场侧 **97.6%** 的亏损（最差 −422bp）。
            #   ⇒ 该兜底已由 **T60** 真正补上（`MM_RISK_HARD_EXIT_SEC`，默认 30s 后吃单）。
            #   请注意：以后写"有 N 层兜底"时，**必须逐一验证那 N 层真的在跑**
            #   （这正是规矩第 11 条）。
            #
            # 回滚：`MM_RISK_EXIT_PASSIVE=0` ⇒ 恢复旧的 touch 行为。
            _risk_passive = str(
                os.getenv("MM_RISK_EXIT_PASSIVE", "1") or "1").strip() not in (
                    "0", "false", "False", "no", "off")
            if action == "maker_risk":
                if not _risk_passive:
                    # 旧行为：touch（对手价）
                    if pos > 0:
                        px_exit = bid
                    elif pos < 0:
                        px_exit = ask
                    else:
                        px_exit = 0.0
                else:
                    # 新行为：pass（被动侧）—— 需要行情朝我们走才成交
                    if pos > 0:
                        px_exit = ask
                    elif pos < 0:
                        px_exit = bid
                    else:
                        px_exit = 0.0
            else:
                if pos > 0:
                    px_exit = ask
                elif pos < 0:
                    px_exit = bid
                else:
                    px_exit = 0.0
            # ── [整顿轮·T41 2026-10-06] 离场挂单价可**被动侧偏移** ────────────
            #
            # 实测（修复后窗口，144 条出场腿，按成交时的队列位置分组）：
            #
            #   qpos     n    均 qpos_bp   均 spread_bp
            #   inside  112      +9.2~+12.2     **−6.88**   ← 78% 的腿
            #   touch    20        +0.01         **+1.75**
            #   behind   12        −5.0          **+8.40**
            #
            # ⇒ **正确的出场（touch/behind，32 条）均 +4.2bp；错的（inside）均 −6.88bp。
            #    差 11bp。**
            #
            # 机理（已逐行核对，非推断）：
            #   ① 本函数把多头离场挂在 **`ask`**（= 被动侧/touch，见上一分支）
            #   ② 若成交时 `qpos` 仍是 touch，说明"挂对了、且行情配合" ⇒ 赚 +1.75bp
            #   ③ 若成交时 `qpos=inside`（我们的卖价**低于**当时的卖一），
            #      唯一解释是**挂出后卖一下移到了我们价位**（中价下跌）
            #      ⇒ 成交恰恰发生在行情朝不利方向走的时候 = **逆向选择**
            #   ⇒ 挂 `ask` 看似正确，仍会被"卖一下移"吃进去。
            #
            # 修法：`MM_EXIT_PASSIVE_OFFSET_BP` 可配（默认 **0 = 逐字保持现状**）。
            #   >0 时把离场价再往被动侧推 Nbp（多头卖得更**高**、空头买得更**低**），
            #   以换取"成交时仍处于 touch/behind"的更高概率。
            #   代价：更可能不成交（但 `maker_edge` / `taker_stop` / 时限兜底仍在）。
            #
            # 回滚：删掉 `MM_EXIT_PASSIVE_OFFSET_BP`（或设为 0）⇒ 与修复前逐字一致。
            # ⇒ 主动离场一律跑这个默认值 —— 不再依赖那个加载不上的 `.env.worker`
            #   （实测 worker 心跳元组能读到新代码，但 `.env.worker` 的加载仍未生效，
            #    原因未定位；因此**把有证据的值做成代码默认**，用 env 覆盖做回滚口）。
            #   这符合"不靠推断、不靠猜"：值来自实测，回滚口显式存在。
            try:
                _exit_off_bp = float(os.getenv("MM_EXIT_PASSIVE_OFFSET_BP", "0") or 0.0)
            except ValueError:
                _exit_off_bp = 0.0
            if _exit_off_bp > 0 and px_exit > 0:
                if pos > 0:
                    px_exit = px_exit * (1.0 + _exit_off_bp / 1e4)
                elif pos < 0:
                    px_exit = px_exit * (1.0 - _exit_off_bp / 1e4)
            if px_exit > 0:
                # [整顿轮·T1c 2026-10-05] 同 T1b：离场挂单必须 `sticky=True` + 容差。
                # 旧写法 `sticky=False` 的 `stay` 要求价格**精确相等**，而离场价跟随
                # bid/ask 每拍变化 ⇒ 每拍重挂 ⇒ `flow_queue_cum` 归零 ⇒ 挂单离场
                # 永远排不上队，库存只能等超时被 `taker_stop` 吃单（−47bp/腿）。
                # 这正是 `_arm` docstring（本文件 118-119 行）已经写明的病根，
                # 但离场路径当时没有按它修。
                # [整顿轮·T9] 容差改由**该标的自身波动**推出（见
                # `exit_maker_band_bp`）：写死 2bp 在高波动标的上会让挂单
                # 长期停在旧价、被穿透 ⇒ spread_bp 深负（实测 −45~−103bp/腿）。
                _band = exit_maker_band_bp(vol_300s_bp)
                _arm("sell" if pos > 0 else "buy", px_exit, 0.0,
                     sticky=True, band_bp=_band)
                dec.action = "quote"
                dec.skip = action
                dec.exit_path = action
                return None
            dec.skip = "exit_no_book"
            return None
        # 继续持有。已经挂着的减仓单留着，不另开进场单。
        # [h870] **流反转即时离场**:持仓中若本拍流强反转(≥2:1)⇒ 优势假设已破,
        # 标记剩余期望为负 ⇒ 下一拍走 maker_edge 挂单离场(0 费),
        # 而不是干等持有时限再被止损(实测止损往返 −49.8bp,是唯一的亏损源)。
        _rev_flow = float(seg_sell or 0.0) - float(seg_buy or 0.0)
        if pos > 0 and _rev_flow > 0 and \
                float(seg_sell or 0.0) >= 2.0 * float(seg_buy or 0.0) + 1e-9:
            state.flow_mu = -1.0
            dec.skip = "flow_reversal"
        elif pos < 0 and _rev_flow < 0 and \
                float(seg_buy or 0.0) >= 2.0 * float(seg_sell or 0.0) + 1e-9:
            state.flow_mu = -1.0
            dec.skip = "flow_reversal"
        else:
            dec.skip = "holding(active_flow)"
        if pos > 0 and float(state.quote_ask or 0.0) > 0:
            dec.ask = float(state.quote_ask)
        elif pos < 0 and float(state.quote_bid or 0.0) > 0:
            dec.bid = float(state.quote_bid)
        dec.action = "quote" if (dec.bid or dec.ask) else "pause"
        return None

    # 空仓：这一拍这一档没有正期望就不挂。禁止退回 OFI 吃单。
    # [h852 深入修] 实测:极端波动币(SI −124bp/腿)与枯竭币是纯噪音,
    # 探索它们只学到"别碰"、却按全名义交学费 ⇒ **恢复 R4/R5 禁入**,
    # 探索继续在 R1/R2/R3 上做(占宇宙多数)。开关:MM_FLOW_EXPLORE_R45=1 可放开。
    import os as _os
    _allow_r45 = str(_os.getenv("MM_FLOW_EXPLORE_R45", "0")).strip() not in (
        "0", "false", "False", "")
    if (regime in ("R4", "R5") and not _allow_r45) or book_stale:
        state.quote_bid = state.quote_ask = 0.0
        dec.skip = f"regime_{regime or 'stale'}_no_entry"
        return None
    # ── [h865 用户"接上"] 形态 → 策略 路由(S1/S3/S4;R4/R5 = S5 避险)─────────
    # 设计(见 研究结论/流交易策略体系总体设计_20261004.md §3):
    #   R1 趋势流 → **S1 流顺势**:OFI 与 300s 趋势同向 ⇒ 顺流进场(不 fade 流)
    #   R2 平静   → **S3 均值反转**:偏离 60s VWAP ≥ mr_dev_bp 且**无强流**
    #                ⇒ 逆偏离方向进场,回 VWAP 即离场
    #   R3 挤压   → **S4 挤压突破**:σ 抬升 + 流方向明确 ⇒ 顺突破进场
    #   R4/R5     → **S5 避险**:不进场
    # 每套策略自带持有时限(写进 state.flow_hold_sec),往返账本记 strategy_id。
    _strat = "S1"
    _side_pick: Optional[str] = None
    _skip_strat = ""
    if regime == "R2":
        _dev = ((float(mid) - float(vwap60)) / float(vwap60) * 1e4
                if float(vwap60 or 0.0) > 0 and float(mid) > 0 else 0.0)
        if abs(_dev) >= float(mr_dev_bp) and abs(float(ofi or 0.0)) < float(flow_thresh):
            _strat, _side_pick = "S3", ("sell" if _dev > 0 else "buy")
        else:
            _skip_strat = f"S3_no_dev({_dev:+.1f}bp)"
    elif regime == "R3":
        if abs(float(ofi or 0.0)) >= float(flow_thresh):
            _strat = "S4"
            _side_pick = "buy" if float(ofi or 0.0) > 0 else "sell"
        else:
            _skip_strat = "S4_no_flow"
    else:  # R1(或未分类) ⇒ S1
        if abs(float(ofi or 0.0)) >= float(flow_thresh) and (
                float(trend_bp or 0.0) == 0.0
                or float(ofi or 0.0) * float(trend_bp or 0.0) >= 0):
            _strat = "S1"
            _side_pick = "buy" if float(ofi or 0.0) > 0 else "sell"
        else:
            _skip_strat = "S1_flow_vs_trend"

    if not allow_entry or model_side not in ("buy", "sell"):
        # 严格门(有模型时):模型方向优先,策略只做风险分层
        if _side_pick is None:
            if _keep_working(skip_reason or f"no_signal({regime})"):
                return None
            dec.skip = skip_reason or f"no_signal({regime})"
            return None
        if not allow_entry:
            if _keep_working(skip_reason or "model_gate_no_edge"):
                return None
            dec.skip = skip_reason or "model_gate_no_edge"
            return None
        side = str(_side_pick)
    else:
        side = str(model_side)
    # [h834] 探索单例外:probe 的目的就是**在没有证据时采样**(mu 定义为 0),
    # 所以不能拿"mu > 安全垫"把它挡掉 —— 否则冷启动死锁无解。
    # [h852 深入修] 探索模式同理,但**只绕开进场检查**:离场阶梯拿到的 mu 不变
    # (否则会把 mu 污染成正数 ⇒ maker_edge 永不触发 ⇒ 只能等吃单止损)。
    _is_probe = float(probe_notional_usd or 0.0) > 0.0
    if (not _is_probe) and (not explore_entry) and (
            model_mu is None or float(model_mu) <= float(entry_margin_bp)):
        if _keep_working("mu_below_margin"):
            return None
        dec.skip = "mu_below_margin"
        return None
    if bid <= 0 or ask <= 0 or mid <= 0:
        state.quote_bid = state.quote_ask = 0.0
        dec.skip = "no_book(active_flow)"
        return None
    # 逐策略持有时限(往返账本与离场阶梯共用)
    # [h865] 策略 id 的权威来源:**门给出的 reason 前缀**(runner 的 explore_s1/s3/s4),
    # 没有时回落到本函数按形态选出的 _strat。这样两处实现不会互相打架。
    _why0 = str(skip_reason or "")
    if _why0.startswith("explore_s"):
        _strat = "S" + _why0[len("explore_s"):len("explore_s") + 1]
    state.flow_hold_sec = float(HOLD_BY_STRATEGY.get(_strat, max_hold_sec) or max_hold_sec)
    state.flow_strategy = _strat
    # [2026-10-08 用户选 1] 进场挂贴近对手价：买单贴近卖一、卖单贴近买一。
    # 仍是挂单（0 费），但排在队列前面 ⇒ 成交快。价差太宽时不越中价（防跳空币）。
    # 回滚：MM_ENTRY_AGGRESSIVE=0 ⇒ 恢复挂队尾（bid/ask）。
    _agg = True
    try:
        import os as _os2
        _agg = str(_os2.getenv("MM_ENTRY_AGGRESSIVE", "1") or "1").strip().lower() not in (
            "0", "false", "no", "off")
    except Exception:
        _agg = True
    if _agg and bid > 0 and ask > bid:
        _spread = ask - bid
        _mid = (bid + ask) / 2.0
        if side == "buy":
            # 贴近卖一，但不越过中价 + 1/4 价差（留捕获空间）
            px = min(ask, _mid + _spread * 0.25)
        else:
            px = max(bid, _mid - _spread * 0.25)
    else:
        px = bid if side == "buy" else ask
    # ── [整顿轮·T42 2026-10-06] **持仓超时的硬兑现**（针对实测的 45-60s 甜蜜区）──
    #
    # 实测（596 条真实往返，按持仓时长分桶）：
    #
    #   持仓档      n     均 y_bp    胜率
    #   <15s       46    −35.30      2%
    #   15-30s    104    +10.29     53%
    #   **30-45s**  62   **+23.79**  **61%**   ← 甜蜜区
    #   45-60s     19    +14.90     42%
    #   **60-120s** 189  **−20.62**  28%      ← 61% 的往返在这（或更久）
    #   >2min     175     −7.41     38%
    #
    # ⇒ 配置时限是 45s（`HOLD_BY_STRATEGY` S1=45），但**61% 的往返持仓 ≥60s**。
    #   原因是：到点后 `maker_time` 只**挂**被动离场单，**填不上就继续等**
    #   （`runner.py:2229-2230` 在 `timeout_exit_maker_only=True` 时"只登记不 taker"），
    #   于是仓位停在衰减区里吃逆向漂移（R051/R053 已定位：代价在持仓期行情）。
    #
    # 修法：`MM_HOLD_HARD_EXIT_SEC` 可配（默认 **0 = 逐字保持现状**）。
    #   >0 时：若 `age > 该值` 且仍有仓位 ⇒ **立即市价兑现**（taker），
    #   把仓位锁在甜蜜区尾部，而不是无限等被动单。
    #   代价：付一次 taker 费（4bp）；收益：躲开 −20.62bp 的衰减区。
    #   **这是"先兑现再谈价"**，与 T41（把离场挂得更被动）方向相反 ——
    #   T41 已实测失败（指标改善但净额更差），因为它让成交更慢。
    #
    # 回滚：删掉 `MM_HOLD_HARD_EXIT_SEC`（或设 0）⇒ 与修复前逐字一致。
    #
    # ── [T47 2026-10-06] **位置错了 ⇒ 从来没执行过** ──────────────────────
    # 本块原先放在**第 747 行**，而它前面有约 15 处 `return None`
    # （`dust_swept` / `tax_exit_no_book` / holding / regime / mu_below_margin /
    #   no_book / 以及第 484/613 行的**全部离场动作分支** `*_maker`）。
    # ⇒ **只要仓位存在，控制流早就在上面返回了**，
    #   本块永远到不了 ⇒ 实测 `hold_hard_taker` 在账本里 **0 条**（工具查出来的）。
    # 这正是本会话第 5 次"机制存在但从不执行"（前 4 次：
    #   `_EXIT_TAKER_AFTER_SEC` 死常量、`flow_gate_last` 无生产者、
    #   `vol_top20` 静默停摆、`states` 被白名单裁掉计数器）。
    # ⇒ 修法：**移到仓位检查的最前面**（见下面 `_ho_exit` 块）。
    _hard_exit = 0.0
    try:
        _hard_exit = float(os.getenv("MM_HOLD_HARD_EXIT_SEC", "0") or 0.0)
    except ValueError:
        _hard_exit = 60.0
    stop = disaster_stop_bp(vol_300s_bp, floor_bp=stop_floor_bp, cap_bp=stop_cap_bp)
    cap = notional_cap_usd(equity, stop, same_side_n, loss_frac)
    # [h887 桥 21:58] **绝对名义上限**:安静市场波动小 ⇒ 止损压到 15bp 地板
    # ⇒ notional_cap_usd = 权益×0.5%÷15bp 反而放大(实测单腿 $1300~1900,
    # 之前 $150~500)⇒ 3 笔逆腿 −18U。单笔名义不得超过权益的 5%,
    # 不管止损多窄 —— 止损压到地板时仓位不再跟着膨胀。
    #
    # ── [整顿轮·T29 2026-10-06] **把 5% 改成可配，并默认交回风险模型** ──
    #
    # 实测（权益 $10,077，`flow_learn_params` floor=15 cap=40）：
    #   实测 stop 落在 **15~20bp**（R1 均 |net| 16.8bp、R2 均 9.5bp）
    #
    #   stop_bp | 风险推导上限 | 5% 上限 | 止损时实亏 | 占设计意图
    #   --------|-------------|---------|-----------|----------
    #      15   |   $33,591   |  $504   |   $0.76   |   1.5%
    #      20   |   $25,193   |  $504   |   $1.01   |   2.0%
    #      40   |   $12,596   |  $504   |   $2.02   |   4.0%
    #
    # 引擎自己声明的**单笔风险预算**是 `EQUITY_LOSS_PER_TRADE = 0.5%`
    # ⇒ 权益 $10,077 时**应为 $50.39/笔**。
    # 而 5% 上限让实际风险只有 **$1.01**（20bp 止损）——**比设计意图低 50 倍**。
    #
    # ⇒ 这不是"保守"，这是**口子比引擎自己的风险模型小了两个数量级**，
    #    收益被同比例压薄（R045 实测：稳定态 $504 腿 12 条只赚 +$0.65）。
    #
    # 修法：`MM_AF_LEG_CAP_PCT` 可配（默认 **0 = 交回风险模型**）。
    #   · 0（默认）⇒ cap 就是 `notional_cap_usd(...)`，
    #     它**同时**受"单笔 0.5%"与"当日 2% ÷ 同向笔数"两个约束，
    #     所以放开的只是**多余的**那一层，**风险预算仍被引擎自己的公式焊住**。
    #   · >0 ⇒ 恢复"硬百分比上限"语义（例：`5` = 旧行为逐字一致，回滚用）。
    # [T57] _leg_cap_pct 已在函数顶部读取（两处共用），此处不再重复定义。
    if _leg_cap_pct > 0 and float(equity or 0.0) > 0:
        cap = min(cap, float(equity) * _leg_cap_pct / 100.0)
    # [整顿轮·T20] 账户级总敞口：剩余额度 = 权益×比例 − 已占用总敞口。
    # 只约束**加仓**方向；减仓由调用方标记（此处 cap 只在下单侧使用）。
    if float(max_gross_notional_ratio or 0.0) > 0 and float(equity or 0.0) > 0:
        _room = (float(equity) * float(max_gross_notional_ratio)
                 - max(0.0, float(gross_notional_usd or 0.0)))
        if _room <= 0.0:
            state.quote_bid = state.quote_ask = 0.0
            dec.skip = (f"gross_exposure_active_flow("
                        f"{float(gross_notional_usd or 0.0):.0f})")
            return None
        cap = min(cap, _room)
    if float(probe_notional_usd or 0.0) > 0:
        cap = min(cap, float(probe_notional_usd))
    qty = _vqty(state.symbol, cap / px) if px > 0 and cap > 0 else 0.0
    px_v = _vpx(state.symbol, px)
    if qty <= 0 or not _vp(state.symbol, px_v, qty, mid)[0]:
        state.quote_bid = state.quote_ask = 0.0
        dec.skip = "venue_filter(active_flow)"
        # [2026-10-09 诊断] 拒单真实原因写日志(临时,查明后删):
        # 是 qty=0(cap 算没了) 还是 passes 拒(min_notional/pct_band)。
        try:
            import json as _vj
            import pathlib as _vp2
            _ok2, _reason2 = _vp(state.symbol, px_v, qty, mid)
            with open(_vp2.Path(__file__).resolve().parents[3]
                      / "logs" / "venue_filter_debug.jsonl", "a", encoding="utf-8") as _vf:
                _vf.write(_vj.dumps({
                    "ts": now_ts, "symbol": state.symbol, "side": side,
                    "px": px, "px_v": px_v, "cap": cap, "qty": qty, "mid": mid,
                    "qty_zero": qty <= 0, "passes": _ok2, "reason": _reason2,
                }, ensure_ascii=False) + "\n")
        except Exception:
            pass
        return None
    # [h879 用户"需要全速"] **信号强 ⇒ 吃单追进场**(流交易本色):
    # 挂单等人来打在市场安静时成交极慢(周日下午 fills 30/h);
    # 方向分数够强时付 4bp 直接成交 —— 4bp 买的是"马上有仓 + 不会被逆向选择"。
    if bool(taker_entry):
        px_cross = ask if side == "buy" else bid
        if px_cross > 0:
            _apply(side, _vpx(state.symbol, px_cross), qty, "flow_entry_taker",
                   TAKER_FEE_BP, False)
            state.flow_hold_sec = float(HOLD_BY_STRATEGY.get(_strat, max_hold_sec)
                                        or max_hold_sec)
            state.flow_strategy = _strat
            state.quote_bid = state.quote_ask = 0.0
            dec.skip = "taker_chase"
            dec.action = "pause"
            return None
    _arm(side, px_v, bid_qty if side == "buy" else ask_qty)
    state.flow_hold_sec = float(max_hold_sec or 0.0)
    # [h856] 探索模式传 mu=None(有意为之:没有模型证据)⇒ 存 0.0,
    # 表示"剩余期望未知/不为正" ⇒ 离场阶梯在最短持有后走 maker_edge 挂单离场。
    # 之前写 `float(model_mu)` 会在 None 时抛 TypeError ⇒ 整条主动流被跳过
    # (实测 active_flow_error 9331 次 = 半个车道空转)。
    state.flow_mu = float(model_mu or 0.0)
    dec.action = "quote"
    dec.skip = f"maker_working({side})"
    dec.exit_path = ""
    return None
