# -*- coding: utf-8 -*-
"""[F60] L1 做市车道驱动器（影子期 = 模拟账户直跑）。

设计依据：《复合策略与交易系统全面改造设计_V2》§1.3 / §3.2

分层：
  - **纯逻辑层**（本文件上半部分，无 IO）：`SymbolState` / `plan_tick` / `TickDecision`。
    一个 tick = 「检查旧挂单成交 → 记账 → 超时平仓 → 重挂新单」，
    与 F59 回放**同口径**（`fill_side` + `InventoryBook`），保证影子结论可复算。
  - **驱动层**（本文件下半部分）：`ShadowRunner` 负责读盘口/成交、持久化状态、
    写 `lane_ledger`、刷新 `lane_registry`、产出影子期报告。

为什么单独写驱动器而不是复用 paper_engine.place_order：
  `paper_engine.place_order` 面向「方向性开仓」，会经过 trade_gate / scalp 开仓闸门 /
  持仓合并，且每次下单落 PaperOrder 行；做市一秒数张挂单会把它压垮。做市需要的是
  「常驻挂单 + 区间成交判定」，因此用独立车道账本（`lane_ledger`）+ 独立状态表，
  资金仍挂在模拟账户权益下（`ShadowRunner.account_id`）。
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

DEFAULT_LANE_ID = "mm_asterdex"
DEFAULT_VENUE = "asterdex"
DEFAULT_SYMBOLS = ["BTC", "ETH", "BNB", "XRP", "SOL", "DOGE"]
FILL_NOTIONAL = float(os.getenv("F60_FILL_NOTIONAL", "100"))
TAKER_FEE_BP = float(os.getenv("F60_TAKER_FEE_BP", "4"))
MAX_MAKER_FEE_BP = float(os.getenv("F60_MAX_MAKER_FEE_BP", "0.5"))  # 费率闸门
# 数据新鲜度闸门：盘口快照超过这个年龄就**不报价**。
# 事故背景：2026-08-18 之后 asterdex 的盘口/成交采集停止，最新快照已陈旧 22 天，
# 若不加闸门，驱动器会拿 22 天前的价格挂单（BTC 64k vs 实际 78k）。
MAX_DATA_AGE_SEC = float(os.getenv("MM_MAX_DATA_AGE_SEC", "180"))
# 队列保守假设：价格需穿过挂单价多少 bp 才算我们成交（0=假设排在队列最前）
PENETRATION_BP = float(os.getenv("F60_PENETRATION_BP", "0.0"))


# ═══════════════════════ 纯逻辑层 ═══════════════════════

@dataclass
class SymbolState:
    """一个币的做市运行态（可持久化）。"""

    symbol: str
    qty: float = 0.0
    avg_px: float = 0.0
    avg_mid: float = 0.0
    opened_ts: float = 0.0
    last_ts: float = 0.0
    quote_bid: float = 0.0
    quote_ask: float = 0.0
    quote_mid: float = 0.0
    quote_ts: float = 0.0
    toxic_streak: int = 0
    # 波动归一的滚动窗口（相对价差），窗口长度固定 20。
    # 不能用「上次挂单时的中价」做基准：断流 22 天后中价差 22%，
    # 会算出 sigma=450 并永久 vol_pause（实测事故）。
    spread_hist: List[float] = field(default_factory=list)
    spread_baseline: float = 0.0
    mid_hist: List[float] = field(default_factory=list)      # 近 N 期中价（趋势闸门）
    vol_baseline_bp: float = 0.0                             # 已实现波动基准（F71b）

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol, "qty": round(self.qty, 10),
            "avg_px": round(self.avg_px, 10), "avg_mid": round(self.avg_mid, 10),
            "opened_ts": self.opened_ts, "last_ts": self.last_ts,
            "quote_bid": self.quote_bid, "quote_ask": self.quote_ask,
            "quote_mid": self.quote_mid, "quote_ts": self.quote_ts,
            "toxic_streak": self.toxic_streak,
            "spread_hist": [round(x, 8) for x in (self.spread_hist or [])[-20:]],
            "spread_baseline": round(self.spread_baseline, 8),
            "mid_hist": [round(x, 10) for x in (self.mid_hist or [])[-240:]],
            "vol_baseline_bp": round(self.vol_baseline_bp, 4),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SymbolState":
        return cls(
            symbol=str(d.get("symbol") or ""),
            qty=float(d.get("qty") or 0.0),
            avg_px=float(d.get("avg_px") or 0.0),
            avg_mid=float(d.get("avg_mid") or 0.0),
            opened_ts=float(d.get("opened_ts") or 0.0),
            last_ts=float(d.get("last_ts") or 0.0),
            quote_bid=float(d.get("quote_bid") or 0.0),
            quote_ask=float(d.get("quote_ask") or 0.0),
            quote_mid=float(d.get("quote_mid") or 0.0),
            quote_ts=float(d.get("quote_ts") or 0.0),
            toxic_streak=int(d.get("toxic_streak") or 0),
            spread_hist=[float(x) for x in (d.get("spread_hist") or [])],
            spread_baseline=float(d.get("spread_baseline") or 0.0),
            mid_hist=[float(x) for x in (d.get("mid_hist") or [])],
            vol_baseline_bp=float(d.get("vol_baseline_bp") or 0.0),
        )


def update_sigma(state: SymbolState, rel_spread: float, *,
                 window: int = 20) -> float:
    """用滚动相对价差算波动归一（与 F59 回放同口径）。

    sigma_norm = 窗口均值 / 基准值 − 1，下限 0。
    基准取窗口填满时的均值——**不依赖任何跨时段的价格水平**，
    因此断流/重启后不会算出荒谬值。
    """
    if rel_spread is None or rel_spread <= 0:
        return 0.0
    state.spread_hist.append(float(rel_spread))
    if len(state.spread_hist) > window:
        state.spread_hist = state.spread_hist[-window:]
    if state.spread_baseline <= 0 and len(state.spread_hist) >= window:
        state.spread_baseline = sum(state.spread_hist) / len(state.spread_hist)
    if state.spread_baseline <= 0:
        return 0.0
    avg = sum(state.spread_hist) / len(state.spread_hist)
    return max(0.0, avg / state.spread_baseline - 1.0)


@dataclass
class PlannedFill:
    """本 tick 判定成交的一腿。"""

    symbol: str
    side: str
    qty: float
    px: float
    mid: float
    ts: float
    is_flatten: bool = False
    edge_bp: float = 0.0
    spread_usd: float = 0.0
    price_usd: float = 0.0
    fee_usd: float = 0.0

    @property
    def net_usd(self) -> float:
        return self.spread_usd + self.price_usd + self.fee_usd

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol, "side": self.side, "qty": round(self.qty, 10),
            "px": self.px, "mid": self.mid, "ts": self.ts,
            "is_flatten": self.is_flatten, "edge_bp": round(self.edge_bp, 4),
            "spread_usd": round(self.spread_usd, 6),
            "price_usd": round(self.price_usd, 6),
            "fee_usd": round(self.fee_usd, 6),
            "net_usd": round(self.net_usd, 6),
        }


@dataclass
class TickDecision:
    symbol: str
    action: str = "pause"           # quote / flatten / pause
    bid: float = 0.0
    ask: float = 0.0
    w_bid_bp: float = 0.0
    w_ask_bp: float = 0.0
    mid: float = 0.0
    sigma_norm: float = 0.0
    fills: List[PlannedFill] = field(default_factory=list)
    skip: str = ""
    skip_side: str = ""
    vol_bp: float = 0.0            # 当前已实现波动（bp，F71b）
    lane_pause: str = ""           # [§82/P5-A] 车道级暂停原因（空=未暂停）

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol, "action": self.action,
            "bid": self.bid, "ask": self.ask,
            "w_bid_bp": round(self.w_bid_bp, 4), "w_ask_bp": round(self.w_ask_bp, 4),
            "mid": self.mid, "sigma_norm": round(self.sigma_norm, 4),
            "vol_bp": round(self.vol_bp, 3),
            "fills": [f.to_dict() for f in self.fills],
            "skip": self.skip, "skip_side": self.skip_side,
            "lane_pause": self.lane_pause,
        }


def lane_limits_enforce_enabled() -> bool:
    """[§82/P5-A] 车道级风控闸门开关 `MM_LANE_LIMITS_ENFORCE`（默认 **false**）。

    默认关 ⇒ 与 P5 接线前**逐字一致**（影子证据基线不被静默改写）；
    打开后 `plan_tick` 才执行 toxic_streak / 日亏 / 波动 / 权益的车道级暂停。
    一键回滚 = 置 false（或删键）+ 重启。
    """
    try:
        from backend.config.settings import MM_LANE_LIMITS_ENFORCE as _v
        return bool(_v)
    except Exception:
        return str(os.environ.get("MM_LANE_LIMITS_ENFORCE", "false")).strip().lower() in (
            "1", "true", "yes", "on",
        )


def lane_day_pnl_usd(lane_id: str, now_ts: Optional[float] = None) -> float:
    """本 UTC 日的车道已实现净额（美元）——日亏闸 `daily_loss_stop_pct` 的输入。

    取 `lane_ledger.daily_series`（唯一事实源）最后一行；失败返回 0.0
    （fail-open：读不到账本不应把车道停掉，但会在 `last_error` 里可见）。
    """
    try:
        from backend.services import lane_ledger

        rows = lane_ledger.daily_series(lane_id=lane_id, days=2) or []
        if not rows:
            return 0.0
        today = datetime.now(timezone.utc).date()
        for r in reversed(rows):
            d = r.get("day") or r.get("date") or r.get("ts")
            if d is None:
                continue
            try:
                dd = d.date() if hasattr(d, "date") else datetime.fromisoformat(str(d)).date()
            except Exception:
                continue
            if dd == today:
                return float(r.get("net_usd") or 0.0)
        return float(rows[-1].get("net_usd") or 0.0)
    except Exception as exc:  # pragma: no cover - 账本不可用时 fail-open
        logger.warning("[F60] 日亏闸读数失败(fail-open，按 0 处理): %s", exc)
        return 0.0


def check_fee_guard(maker_fee_bp: float, *, max_bp: float = MAX_MAKER_FEE_BP) -> Tuple[bool, str]:
    """费率闸门：Aster 的 0% maker 可能是活动价，超过阈值必须停车道。

    F52/F53 实测：挂宽 5bp 时，maker 涨到 2bp 边际就接近 0，涨到 4bp 直接转负。
    """
    if maker_fee_bp < 0:
        return True, "rebate"
    if maker_fee_bp > max_bp:
        return False, f"maker_fee_too_high({maker_fee_bp:.2f}bp>{max_bp:.2f}bp)"
    return True, ""


def check_data_freshness(snapshot_ts_ms: int, now_ts: float,
                         max_age_sec: float = MAX_DATA_AGE_SEC) -> Tuple[bool, float, str]:
    """盘口数据新鲜度检查。返回 (fresh, age_sec, reason)。

    陈旧数据必须拒单：用 22 天前的价格挂单不是「做市」，是「送钱」。
    """
    if not snapshot_ts_ms:
        return False, -1.0, "no_snapshot"
    age = float(now_ts) - int(snapshot_ts_ms) / 1000.0
    if max_age_sec > 0 and age > max_age_sec:
        return False, age, f"stale_data({age/60:.1f}min>{max_age_sec/60:.1f}min)"
    return True, age, ""


def plan_tick(
    *,
    state: SymbolState,
    mid: float,
    seg_low: float,
    seg_high: float,
    seg_taker_sell: float,
    seg_taker_buy: float,
    now_ts: float,
    params=None,
    limits=None,
    equity: float = 5000.0,
    fill_notional: float = FILL_NOTIONAL,
    taker_fee_bp: float = TAKER_FEE_BP,
    maker_fee_bp: float = 0.0,
    half_spread: float = 0.0,
    sigma_norm: float = 0.0,
    inv_ratio_hint: Optional[float] = None,
    book=None,
    marks: Optional[Dict[str, float]] = None,
    day_pnl_usd: float = 0.0,
) -> Tuple[TickDecision, Dict[str, Any]]:
    """一个 tick 的纯决策：成交判定 → 超时平仓 → 重挂新单。

    Args:
        state: 该币当前运行态（含上次挂单价与库存）。
        seg_*: 自 `state.quote_ts` 以来的区间成交明细（低/高/主动买/主动卖量）。
        half_spread: 当前盘口半价差（用于超时平仓打对手价；0 表示未知，退化为中价）。
        book: 可选的跨币 `InventoryBook`（用于净敞口约束）；None 时只用单币约束。
        inv_ratio_hint: 由调用方按账户级限额算出的库存偏离度（覆盖单币计算）。

    Returns:
        (decision, meta) —— meta 含 `inventory_book`（若传入则原样返回）与统计。
    """
    from backend.services.market_maker.core import (
        InventoryBook, LaneRiskLimits, Position, QuoteParams, check_side_allowed,
        compute_quote, fill_side, lane_pause_reason, should_stop_loss,
        trend_blocked_side, vol_regime_blocked,
    )

    params = params or QuoteParams()
    limits = limits or LaneRiskLimits()
    dec = TickDecision(symbol=state.symbol, mid=float(mid or 0.0), sigma_norm=float(sigma_norm or 0.0))
    if not mid or mid <= 0:
        dec.skip = "no_mid"
        return dec, {}

    local_book = book if book is not None else InventoryBook()
    if book is None and abs(state.qty) > 1e-12:
        # 无外部库存时用本币库存初始化（保证单币逻辑可独立测试）
        local_book.positions[state.symbol] = Position(
            qty=state.qty, avg_px=state.avg_px, avg_mid=state.avg_mid,
            opened_ts=state.opened_ts, last_ts=state.last_ts,
        )

    limit_notional = equity * limits.max_net_directional_ratio

    # ① 旧挂单成交判定（区间成交明细）
    # **两侧独立判定**：一侧被敞口/趋势闸门挡住时挂单价为 0，但另一侧的挂单
    # 依然真实存在、必须照常检查成交。此前用 `bid>0 and ask>0` 作为总开关，
    # 结果库存到顶后减仓腿永远不被检查 → 只能等超时砸单（实测平仓占比
    # 从回放的 14% 涨到 32%，净期望因此转负）。
    if state.quote_bid > 0 or state.quote_ask > 0:
        pen = max(0.0, float(PENETRATION_BP)) / 1e4
        hit_buy = (state.quote_bid > 0 and seg_taker_sell > 0
                   and seg_low < state.quote_bid * (1.0 - pen))
        hit_sell = (state.quote_ask > 0 and seg_taker_buy > 0
                    and seg_high > state.quote_ask * (1.0 + pen))
        legs: List[Tuple[str, float]] = []
        if hit_buy:
            legs.append(("buy", state.quote_bid))
        if hit_sell:
            legs.append(("sell", state.quote_ask))
        # 固定基础币数量（而非固定美元名义）：否则一买一卖后残留差额，
        # 长期会漂移出不受控的方向性库存。与 F59 回放同口径。
        leg_qty = fill_notional / mid
        # [F75 2026-09-12 回放/实盘同口径] 队列份额约束：影子成交模拟必须与回放
        # 一致——排在既有做市商之后，只能吃到区间主动量的一部分（F59_QUEUE_SHARE，
        # 默认 0.30）。此前实盘每段全量吃 $100、回放只吃 30%：库存摆动 ~3× 大、
        # 漂移亏损 ~3× 大——回放正收益的配置在实盘变负的根因。
        try:
            from backend.services.market_maker.replay import (
                MIN_FILL_NOTIONAL as _MIN_FILL_NOTIONAL,
                QUEUE_SHARE as _QUEUE_SHARE,
            )
        except Exception:
            _QUEUE_SHARE, _MIN_FILL_NOTIONAL = 0.30, 10.0
        # 归因口径：价差用**挂单时的中价**做基准（挂单意图的边际），
        # 行情从挂单到成交的移动归入 price 维度。
        # 若用成交判定时的中价，行情下跌会把负值塞进 spread，
        # 看起来像「挂宽 8bp 却负价差」，实际是逆选择（总量不变，但归因不可读）。
        ref_mid = state.quote_mid if state.quote_mid > 0 else mid
        for side, px in legs:
            # [F75] 队列份额：与回放同口径（只能吃到区间主动量的一部分）
            _avail = float(seg_taker_sell if side == "buy" else seg_taker_buy)
            qty = min(leg_qty, _avail * _QUEUE_SHARE)
            if qty * px < _MIN_FILL_NOTIONAL:
                continue
            edge = ((ref_mid - px) if side == "buy" else (px - ref_mid)) / ref_mid * 1e4
            d = local_book.apply_fill(symbol=state.symbol, side=side, qty=qty, fill_px=px,
                                      mid_px=ref_mid, fee_rate=maker_fee_bp / 1e4,
                                      now_ts=now_ts)
            state.qty = local_book.qty(state.symbol)
            pos = local_book.positions.get(state.symbol)
            state.avg_px = pos.avg_px if pos else 0.0
            state.avg_mid = pos.avg_mid if pos else 0.0
            state.opened_ts = pos.opened_ts if pos else 0.0
            state.last_ts = now_ts
            dec.fills.append(PlannedFill(
                symbol=state.symbol, side=side, qty=qty, px=px, mid=ref_mid,
                ts=now_ts, edge_bp=edge,
                spread_usd=float(d.get("spread_usd") or 0.0),
                price_usd=float(d.get("price_usd") or 0.0),
                fee_usd=float(d.get("fee_usd") or 0.0),
            ))
            # 毒性流判定：成交后中价相对**挂单时中价**的反向移动。
            # 买在挂单价、随后中价继续跌（或卖完继续涨）→ 逆选择。
            move_bp = ((mid - ref_mid) if side == "buy" else (ref_mid - mid)) / ref_mid * 1e4
            if move_bp < -limits.toxic_bp:
                state.toxic_streak += 1
            else:
                state.toxic_streak = 0

    # ①′ [F71] 止损平仓：浮亏超阈值立即平（早于超时，削掉尾部亏损）
    if abs(state.qty) > 1e-12 and should_stop_loss(state.qty, state.avg_mid, mid,
                                                   limits.stop_loss_bp):
        hs = max(0.0, float(half_spread or 0.0))
        side = "sell" if state.qty > 0 else "buy"
        px = (mid - hs) if side == "sell" else (mid + hs)
        qty = abs(state.qty)
        fd = local_book.apply_fill(symbol=state.symbol, side=side, qty=qty, fill_px=px,
                                   mid_px=mid, fee_rate=abs(taker_fee_bp) / 1e4,
                                   now_ts=now_ts)
        state.qty = local_book.qty(state.symbol)
        state.avg_px = state.avg_mid = state.opened_ts = 0.0
        state.last_ts = now_ts
        dec.fills.append(PlannedFill(
            symbol=state.symbol, side=side, qty=qty, px=px, mid=mid, ts=now_ts,
            is_flatten=True,
            spread_usd=float(fd.get("spread_usd") or 0.0),
            price_usd=float(fd.get("price_usd") or 0.0),
            fee_usd=float(fd.get("fee_usd") or 0.0),
        ))
        dec.action = "flatten"
        dec.skip = "stop_loss"

    # ② 单边持仓超时 → 打对手价平仓（taker）
    if abs(state.qty) > 1e-12 and state.opened_ts > 0 and \
            (now_ts - state.opened_ts) > limits.max_one_side_seconds:
        hs = max(0.0, float(half_spread or 0.0))
        side = "sell" if state.qty > 0 else "buy"
        px = (mid - hs) if side == "sell" else (mid + hs)
        qty = abs(state.qty)
        fd = local_book.apply_fill(symbol=state.symbol, side=side, qty=qty, fill_px=px,
                                   mid_px=mid, fee_rate=abs(taker_fee_bp) / 1e4,
                                   now_ts=now_ts)
        state.qty = local_book.qty(state.symbol)
        state.avg_px = 0.0
        state.avg_mid = 0.0
        state.opened_ts = 0.0
        state.last_ts = now_ts
        dec.fills.append(PlannedFill(
            symbol=state.symbol, side=side, qty=qty, px=px, mid=mid, ts=now_ts,
            is_flatten=True,
            spread_usd=float(fd.get("spread_usd") or 0.0),
            price_usd=float(fd.get("price_usd") or 0.0),
            fee_usd=float(fd.get("fee_usd") or 0.0),
        ))
        dec.action = "flatten"

    # ③ 重挂新单（含单侧许可）
    inv_ratio = (inv_ratio_hint if inv_ratio_hint is not None
                 else local_book.inv_ratio(state.symbol, mid, limit_notional))
    # [F80 2026-09-13] 冻结行情信号：近 frozen_lookback 期单步最大移动（bp）。
    # 单步指标对「微幅高频往返」敏感、对缓慢漂移不敏感——与「挂单能否被
    # 穿越」的真实成交条件对应（冻结日 2h 全幅 16bp 但单步 ≤2bp 即为例证）。
    _hist = state.mid_hist or []
    slow_move_bp = 0.0
    _lb = max(2, int(getattr(params, "frozen_lookback", 60) or 60))
    if len(_hist) >= _lb + 1:
        _h = _hist[-_lb - 1:]
        _maxmv = 0.0
        for _i in range(len(_h) - 1):
            if _h[_i] > 0 and _h[_i + 1] > 0:
                _mv = abs(_h[_i + 1] - _h[_i]) / _h[_i] * 1e4
                if _mv > _maxmv:
                    _maxmv = _mv
        slow_move_bp = _maxmv
    q = compute_quote(symbol=state.symbol, mid=mid, sigma_norm=sigma_norm,
                      inv_ratio=inv_ratio, slow_range_bp=slow_move_bp, params=params)
    if q is None:
        dec.skip = "no_quote"
        if dec.action != "flatten":
            dec.action = "pause"
        return dec, {"book": local_book}

    marks = dict(marks) if marks else {state.symbol: mid}
    marks.setdefault(state.symbol, mid)
    if book is None:
        marks = {s: (mid if s == state.symbol else p.avg_mid or p.avg_px or mid)
                 for s, p in local_book.positions.items()}
        marks.setdefault(state.symbol, mid)

    # [§82 执行 2026-09-11 / 决策 P5-A —— 清单第 19 条] **车道级暂停闸**。
    # 此前 `check_lane_limits()` 生产调用点 = 0（AST 复核，`_audit_ml/Z222`）：
    # `toxic_streak`（连续逆选择暂停）在运行中的影子里**从未生效**，
    # `daily_loss_stop_pct`（日亏上限）更是**从未实现**（判定用消费方 0 个）。
    # 现在：由 `MM_LANE_LIMITS_ENFORCE` 控制（默认 false ⇒ 与旧行为逐字一致），
    # 打开后只执行**车道级**判据（权益/波动/毒性流/日亏）——
    # **绝不**把手伸到敞口判据：那会让"减仓腿"一起停掉，库存只能等超时砸单。
    # 位置刻意放在 ①′止损 / ②超时平仓 **之后** ⇒ 暂停永远不阻断已有库存的离场。
    if lane_limits_enforce_enabled():
        _lane_pause, _lane_why = lane_pause_reason(
            equity=equity, limits=limits, sigma_norm=sigma_norm,
            toxic_streak=state.toxic_streak, day_pnl_usd=day_pnl_usd,
        )
        if _lane_pause:
            dec.lane_pause = _lane_why
            dec.skip = _lane_why.split("(")[0]
            dec.skip_side = "both"
            if dec.action != "flatten":
                dec.action = "pause"
            state.quote_bid = state.quote_ask = state.quote_ts = 0.0
            return dec, {"book": local_book, "lane_pause": _lane_why}

    # [F71b] 波动状态闸门：高波动时平仓成本吞掉价差 → 暂停该币
    vol_paused, vol_cur = vol_regime_blocked(
        state.mid_hist, state.vol_baseline_bp, limits.vol_pause_mult,
        limits.vol_window)
    dec.vol_bp = round(vol_cur, 3)
    if vol_paused:
        dec.action, dec.skip, dec.skip_side = "pause", "vol_regime", "both"
        state.quote_bid = state.quote_ask = state.quote_ts = 0.0
        return dec, {"book": local_book}

    allow_buy, why_buy = check_side_allowed(
        symbol=state.symbol, side="buy", book=local_book, marks=marks, equity=equity,
        add_notional=fill_notional, limits=limits, now_ts=now_ts, sigma_norm=sigma_norm)
    allow_sell, why_sell = check_side_allowed(
        symbol=state.symbol, side="sell", book=local_book, marks=marks, equity=equity,
        add_notional=fill_notional, limits=limits, now_ts=now_ts, sigma_norm=sigma_norm)

    # [F71] 趋势闸门：单边行情里禁止逆势侧（下跌禁买、上涨禁卖）
    blocked = trend_blocked_side(state.mid_hist, limits.trend_pause_bp,
                                 limits.trend_lookback)
    # [F76 2026-09-12] 库存感知：趋势闸只封锁**加仓侧**。
    # 减仓侧在趋势里成交对持仓是**有利**的（多头在上涨中高价卖出、空头在
    # 下跌中低价回补）——此前减仓侧一并被封死，库存只能等超时 taker 平仓
    # （实盘平仓均价 -12.98bp，是亏损主因）。空仓时两侧都是加仓，语义不变。
    if blocked:
        _pos = local_book.qty(state.symbol)
        if _pos > 1e-12 and blocked == "sell":
            blocked = ""          # 多头减仓侧（卖），放行
        elif _pos < -1e-12 and blocked == "buy":
            blocked = ""          # 空头减仓侧（买），放行
    if blocked == "buy" and allow_buy:
        allow_buy, why_buy = False, "trend_down"
    elif blocked == "sell" and allow_sell:
        allow_sell, why_sell = False, "trend_up"

    if not allow_buy and not allow_sell:
        dec.skip = (why_buy or why_sell or "blocked").split("(")[0]
        dec.skip_side = "both"
        if dec.action != "flatten":
            dec.action = "pause"
        state.quote_bid = state.quote_ask = state.quote_ts = 0.0
        return dec, {"book": local_book}

    # 不允许的一侧不下单（挂 0），另一侧照常
    dec.bid = q.bid if allow_buy else 0.0
    dec.ask = q.ask if allow_sell else 0.0
    dec.w_bid_bp = q.w_bid_bp if allow_buy else 0.0
    dec.w_ask_bp = q.w_ask_bp if allow_sell else 0.0
    if not allow_buy or not allow_sell:
        dec.skip = (why_buy if not allow_buy else why_sell).split("(")[0]
        dec.skip_side = "buy" if not allow_buy else "sell"
    state.quote_bid, state.quote_ask = dec.bid, dec.ask
    state.quote_mid, state.quote_ts = mid, now_ts
    if dec.action != "flatten":
        dec.action = "quote"
    return dec, {"book": local_book}


def _params_maker_fee_bp(venue: str) -> float:
    """真实费率表里的 maker 费率（bp）。"""
    from backend.services.market_maker.replay import params_maker_fee
    return float(params_maker_fee(venue)) * 1e4


# ═══════════════════════ 驱动层（DB + 调度） ═══════════════════════

_ensured = False
_ensure_lock = None


def ensure_table() -> None:
    """建运行态表（每进程一次）。"""
    global _ensured, _ensure_lock
    if _ensure_lock is None:
        import threading
        _ensure_lock = threading.Lock()
    if _ensured:
        return
    with _ensure_lock:
        if _ensured:
            return
        try:
            from sqlalchemy import text

            from backend.core.tenant import system_identity
            from backend.database.connection import SessionLocal

            with system_identity():
                with SessionLocal() as db:
                    db.execute(text(
                        "CREATE TABLE IF NOT EXISTS lane_runtime_state ("
                        " lane_id VARCHAR(64) NOT NULL,"
                        " symbol VARCHAR(32) NOT NULL,"
                        " state_json JSONB NOT NULL,"
                        " updated_ts TIMESTAMPTZ NOT NULL DEFAULT now(),"
                        " PRIMARY KEY (lane_id, symbol))"
                    ))
                    db.execute(text(
                        "CREATE TABLE IF NOT EXISTS lane_shadow_report ("
                        " id BIGSERIAL PRIMARY KEY,"
                        " lane_id VARCHAR(64) NOT NULL,"
                        " as_of TIMESTAMPTZ NOT NULL DEFAULT now(),"
                        " window_days INTEGER NOT NULL DEFAULT 30,"
                        " fills INTEGER NOT NULL DEFAULT 0,"
                        " flattens INTEGER NOT NULL DEFAULT 0,"
                        " notional DOUBLE PRECISION NOT NULL DEFAULT 0,"
                        " spread_bp DOUBLE PRECISION NOT NULL DEFAULT 0,"
                        " price_bp DOUBLE PRECISION NOT NULL DEFAULT 0,"
                        " fee_bp DOUBLE PRECISION NOT NULL DEFAULT 0,"
                        " net_bp DOUBLE PRECISION NOT NULL DEFAULT 0,"
                        " net_usd DOUBLE PRECISION NOT NULL DEFAULT 0,"
                        " per_symbol JSONB,"
                        " promotion JSONB,"
                        " note TEXT)"
                    ))
                    db.execute(text(
                        "CREATE INDEX IF NOT EXISTS ix_lane_shadow_report_lane"
                        " ON lane_shadow_report (lane_id, as_of DESC)"
                    ))
                    # 指标列允许 NULL：无成交时六维是「无数据」而不是 0，
                    # 用 0 会把「没跑」误读成「跑了但收益为 0」。
                    for col in ("notional", "spread_bp", "price_bp", "fee_bp",
                                "net_bp", "net_usd"):
                        db.execute(text(
                            f"ALTER TABLE lane_shadow_report ALTER COLUMN {col} DROP NOT NULL"
                        ))
                    db.commit()
            _ensured = True
        except Exception as e:
            logger.warning("[F60] ensure_table 失败: %s", e)


class ShadowRunner:
    """L1 做市车道的影子期驱动器。

    每个 tick：读最新盘口 + 自上次 tick 以来的区间成交 → `plan_tick` →
    写 `lane_ledger` → 存运行态 → 刷新 `lane_registry` edge。
    """

    def __init__(
        self,
        *,
        lane_id: str = DEFAULT_LANE_ID,
        venue: str = DEFAULT_VENUE,
        symbols: Optional[List[str]] = None,
        equity: float = 5000.0,
        account_id: Optional[int] = None,
        params=None,
        limits=None,
        fill_notional: float = FILL_NOTIONAL,
        strategy_type: str = "MM",
    ) -> None:
        from backend.services.market_maker.core import LaneRiskLimits, QuoteParams

        self.lane_id = lane_id
        self.venue = venue
        self.symbols = list(symbols or DEFAULT_SYMBOLS)
        self.equity = float(equity)
        self.account_id = account_id
        self.strategy_type = str(strategy_type or "MM").upper()
        self.meta: Dict[str, Any] = {}
        self.params = params or QuoteParams()
        self.limits = limits or LaneRiskLimits()
        self.fill_notional = float(fill_notional)
        self.maker_fee_bp = _params_maker_fee_bp(venue)
        self.states: Dict[str, SymbolState] = {
            s: SymbolState(symbol=s) for s in self.symbols
        }
        self.last_tick_ts: float = 0.0
        self.last_error: str = ""
        self.ticks: int = 0
        self.fills: int = 0
        self.flattens: int = 0
        self.realized_usd: float = 0.0
        # [F81] 成交桶水位线：每币已消费的最大桶时间戳（防漏桶/防重复消费）
        self._seg_watermark: Dict[str, int] = {}
        # [F85] 复利比例：>0 时每 tick 用模拟账户权益 × 比例 决定腿量（0=固定）
        self.compound_ratio: float = float(params.compound_ratio) if hasattr(
            params, "compound_ratio") and params.compound_ratio else 0.0

    def _read_account_equity(self) -> float:
        """[F85] 读模拟账户当前权益（复利模式的腿量/上限基准）。"""
        if not self.account_id:
            return 0.0
        try:
            from sqlalchemy import text

            from backend.core.tenant import system_identity
            from backend.database.connection import SessionLocal

            with system_identity():
                with SessionLocal() as db:
                    row = db.execute(text(
                        "SELECT total_equity FROM arbitrage_paper_accounts WHERE id=:i"
                    ), {"i": self.account_id}).first()
                    return float(row[0]) if row and row[0] else 0.0
        except Exception as e:
            logger.warning("[F85] 读账户权益失败: %s", e)
            return 0.0

    # ── 状态持久化 ──
    def load_states(self) -> int:
        """从 DB 恢复运行态（重启后不丢库存）。"""
        ensure_table()
        try:
            from sqlalchemy import text

            from backend.core.tenant import system_identity
            from backend.database.connection import SessionLocal

            with system_identity():
                with SessionLocal() as db:
                    rows = db.execute(text(
                        "SELECT symbol, state_json FROM lane_runtime_state WHERE lane_id=:l"
                    ), {"l": self.lane_id}).mappings().all()
            n = 0
            for r in rows:
                st = SymbolState.from_dict(dict(r["state_json"] or {}))
                if not st.symbol:
                    st.symbol = str(r["symbol"])
                self.states[st.symbol] = st
                n += 1
            return n
        except Exception as e:
            self.last_error = f"load_states: {e}"
            logger.warning("[F60] load_states 失败: %s", e)
            return 0

    def save_states(self) -> None:
        ensure_table()
        try:
            import json

            from sqlalchemy import text

            from backend.core.tenant import system_identity
            from backend.database.connection import SessionLocal

            with system_identity():
                with SessionLocal() as db:
                    for st in self.states.values():
                        db.execute(text(
                            "INSERT INTO lane_runtime_state (lane_id, symbol, state_json, updated_ts)"
                            " VALUES (:l, :s, CAST(:j AS JSONB), now())"
                            " ON CONFLICT (lane_id, symbol) DO UPDATE SET"
                            " state_json=EXCLUDED.state_json, updated_ts=now()"
                        ), {"l": self.lane_id, "s": st.symbol,
                            "j": json.dumps(st.to_dict(), ensure_ascii=False)})
                    db.commit()
        except Exception as e:
            self.last_error = f"save_states: {e}"
            logger.warning("[F60] save_states 失败: %s", e)

    # ── 行情 ──
    def fetch_market(self, since_ms: int) -> Dict[str, Dict[str, Any]]:
        """读每个币的最新盘口 + 自水位线以来的区间成交汇总。

        [F81 2026-09-14] 成交桶水位线：market_trades_aggregated 按 **15 秒桶**存储、
        时间戳=桶起点；此前 `timestamp > since_ms`（开区间）+ 15s tick 使桶起点
        恰好落在边界时被系统性跳过——同窗口实测回放 212 笔 vs 实盘 4 笔（漏单 98%），
        「一直亏损」的真因（几笔坏平仓主导了稀少的成交）。水位线保证每个成交桶
        **恰好消费一次**（与回放 [left,right] 语义一致），迟到的桶也会被补收。
        """
        from sqlalchemy import text

        from backend.core.tenant import system_identity
        from backend.database.connection import MarketSessionLocal

        out: Dict[str, Dict[str, Any]] = {}
        with system_identity():
            with MarketSessionLocal() as db:
                for s in self.symbols:
                    ob = db.execute(text(
                        "SELECT timestamp, best_bid, best_ask FROM market_orderbook_snapshots"
                        " WHERE exchange=:e AND symbol=:s AND best_bid>0 AND best_ask>best_bid"
                        " ORDER BY timestamp DESC LIMIT 1"
                    ), {"e": self.venue, "s": s}).mappings().first()
                    # [F81] 取数下界 = max(since_ms, 已消费桶水位) ⇒ 每桶恰好一次
                    _wm = max(int(since_ms or 0), int(self._seg_watermark.get(s, 0)))
                    tr = db.execute(text(
                        "SELECT MIN(low_price) AS lo, MAX(high_price) AS hi,"
                        " COALESCE(SUM(taker_sell_volume),0) AS sv,"
                        " COALESCE(SUM(taker_buy_volume),0) AS bv,"
                        " MAX(timestamp) AS mts"
                        " FROM market_trades_aggregated"
                        " WHERE exchange=:e AND symbol=:s AND timestamp > :ts"
                    ), {"e": self.venue, "s": s, "ts": _wm}).mappings().first()
                    if tr and tr["mts"] is not None:
                        self._seg_watermark[s] = max(_wm, int(tr["mts"]))
                    if not ob:
                        continue
                    best_bid, best_ask = float(ob["best_bid"]), float(ob["best_ask"])
                    mid = (best_bid + best_ask) / 2.0
                    out[s] = {
                        "ts_ms": int(ob["timestamp"]),
                        "mid": mid,
                        "half_spread": max(0.0, (best_ask - best_bid) / 2.0),
                        # 相对价差（波动归一的输入；与 F59 回放同口径）
                        "rel_spread": ((best_ask - best_bid) / mid) if mid > 0 else 0.0,
                        "seg_low": float(tr["lo"]) if tr and tr["lo"] else 0.0,
                        "seg_high": float(tr["hi"]) if tr and tr["hi"] else 0.0,
                        "seg_sell": float(tr["sv"]) if tr else 0.0,
                        "seg_buy": float(tr["bv"]) if tr else 0.0,
                    }
        return out

    def _record_fills(self, decision: TickDecision) -> None:
        """把本 tick 判定成交的腿写入：① 六维账本（车道级）；② 统一模拟账户总账。

        两本账的分工：
          - `lane_ledger` 是**车道级**事实源（六维归因、晋升判定）；
          - 统一账户总账（`arbitrage_paper_ledger`，strategy_type=MM）让 MM 与
            S3/S8/SDN 同账管理——账户权益、可用余额、按策略盈亏一处可见。
        """
        if not decision.fills:
            return
        try:
            from datetime import datetime, timezone

            from backend.services import lane_ledger

            for f in decision.fills:
                fee_rate = (abs(TAKER_FEE_BP) / 1e4 if f.is_flatten
                            else self.maker_fee_bp / 1e4)
                notional = f.qty * f.px
                price_bp = (f.price_usd / notional * 1e4) if notional > 0 else 0.0
                lane_ledger.record_fill(
                    lane_id=self.lane_id, symbol=f.symbol, side=f.side, qty=f.qty,
                    fill_px=f.px, mid_px=f.mid, fee_rate=fee_rate,
                    price_bp=price_bp,
                    ts=datetime.fromtimestamp(float(f.ts), tz=timezone.utc),
                    meta={"source": "F60_shadow", "flatten": f.is_flatten,
                          "notional": round(notional, 4),
                          "price_usd": round(f.price_usd, 6)},
                )
                self._record_account_fill(f, fee_rate=fee_rate)
        except Exception as e:
            self.last_error = f"record_fills: {e}"
            logger.warning("[F60] record_fill 失败: %s", e)

    def _record_account_fill(self, f: PlannedFill, *, fee_rate: float) -> None:
        """把一笔做市成交汇入**统一模拟账户**（按策略分账）。"""
        if not self.account_id:
            return
        try:
            from backend.services.rebate_arb.arbitrage_paper_account_service import (
                ArbitragePaperAccountService,
            )

            notional = abs(f.qty * f.px)
            fee_usd = abs(fee_rate) * notional
            # 账户侧：扣手续费 + 记已实现盈亏（价差 + 价格），合计等于本笔净额
            svc = ArbitragePaperAccountService()
            from backend.core.tenant import system_identity
            from backend.database.connection import SessionLocal

            with system_identity():
                with SessionLocal() as db:
                    svc.record_paper_leg_fill(
                        db, int(self.account_id), self.venue,
                        position_id=f"mm:{f.symbol}",
                        strategy_type=self.strategy_type,
                        phase="flatten" if f.is_flatten else "fill",
                        fee_paid=fee_usd, rebate_received=0.0, slippage_cost=0.0,
                        pnl_delta=float(f.spread_usd + f.price_usd),
                        note=f"{f.symbol} {f.side} {'平仓' if f.is_flatten else '做市成交'}",
                        metadata={"lane_id": self.lane_id, "symbol": f.symbol,
                                  "side": f.side, "qty": round(f.qty, 10),
                                  "px": f.px, "mid": f.mid,
                                  "edge_bp": round(f.edge_bp, 4),
                                  "source": "F60_shadow"},
                        force_log=True,
                    )
                    # record_paper_leg_fill 只写不提交（由调用方控制事务边界）；
                    # 做市驱动器是独立调度任务，必须自己提交，否则流水被回滚。
                    db.commit()
        except Exception as e:
            logger.warning("[F60] 账户入账失败（不影响车道账本）: %s", e)

    # ── 主循环 ──
    def tick(self, *, now_ts: Optional[float] = None) -> Dict[str, Any]:
        """跑一个调度周期。返回本 tick 摘要（可直接进日志/API）。"""
        from backend.services.market_maker.core import InventoryBook, Position

        ok, reason = check_fee_guard(self.maker_fee_bp)
        if not ok:
            self.last_error = reason
            return {"ok": False, "reason": reason, "decisions": []}

        now_ts = float(now_ts or time.time())
        since_ms = int((self.last_tick_ts or (now_ts - 60.0)) * 1000)
        try:
            market = self.fetch_market(since_ms)
        except Exception as e:
            self.last_error = f"fetch_market: {e}"
            logger.warning("[F60] fetch_market 失败: %s", e)
            return {"ok": False, "reason": self.last_error, "decisions": []}

        decisions: List[Dict[str, Any]] = []
        data_ages: List[float] = []
        # [§82/P5-A] 日亏闸输入：整个 tick 只读一次账本（各币共用同一日亏）
        day_pnl = 0.0
        if lane_limits_enforce_enabled():
            day_pnl = lane_day_pnl_usd(self.lane_id, now_ts)
            if day_pnl:
                logger.info("[F60] 车道 %s 本日已实现净额 = %.2f USD（日亏闸输入）",
                            self.lane_id, day_pnl)
        # [F85 2026-09-14] 复利模式：每 tick 读模拟账户权益，腿量 = 权益 × 比例。
        # 复利研究结论（30 天/6 天回放）：全权益腿复利 +6.25% vs 固定 +5.34%
        # （6 天窗口），且回撤不增；固定腿量 + 权益联动上限反而会在小额亏损后
        # 死锁入场侧（上限 < 腿量）。容量上限：$10k 腿 41% 段被队列份额截断、
        # $30k 腿 73%——复利增长在 $10k 腿量附近开始饱和。
        if (self.compound_ratio or 0) > 0 and self.account_id:
            try:
                _eq = self._read_account_equity()
                if _eq and _eq > 0:
                    self.equity = float(_eq)
                    self.fill_notional = max(10.0, float(self.compound_ratio) * self.equity)
            except Exception as _e:
                self.last_error = f"compound_equity: {_e}"
        # [F72] 组合级共享库存账本：所有币共用，`max_net_exposure_ratio`
        # （组合净敞口上限）才有意义——此前每币各自一本账，6 个币各持 $100
        # 时组合已 $600 同向暴露，却谁都看不到。
        from backend.services.market_maker.core import InventoryBook, Position

        shared_book = InventoryBook()
        marks: Dict[str, float] = {}
        for s in self.symbols:
            m0 = market.get(s)
            st0 = self.states.get(s)
            if m0 and st0 and abs(st0.qty) > 1e-12:
                shared_book.positions[s] = Position(
                    qty=st0.qty, avg_px=st0.avg_px, avg_mid=st0.avg_mid,
                    opened_ts=st0.opened_ts, last_ts=st0.last_ts)
            if m0:
                marks[s] = float(m0["mid"])
        for s in self.symbols:
            m = market.get(s)
            st = self.states.setdefault(s, SymbolState(symbol=s))
            if not m:
                decisions.append({"symbol": s, "action": "pause", "skip": "no_market"})
                continue
            fresh, age, why = check_data_freshness(m.get("ts_ms"), now_ts)
            if age >= 0:
                data_ages.append(age)
            if not fresh:
                # 数据陈旧 → 撤掉挂单（清空 quote），本 tick 不报价
                st.quote_bid = st.quote_ask = st.quote_ts = 0.0
                decisions.append({"symbol": s, "action": "pause", "skip": why,
                                  "data_age_sec": round(age, 1)})
                continue
            st.mid_hist.append(float(m["mid"]))
            if len(st.mid_hist) > 240:
                st.mid_hist = st.mid_hist[-240:]
            # [F74] 波动信号：当前已实现波动相对基准的倍数（低波动≈0 → w 退化为 w_base）
            from backend.services.market_maker.core import realized_vol_bp

            vol_cur = realized_vol_bp(st.mid_hist, self.limits.vol_window)
            sigma = (max(0.0, vol_cur / st.vol_baseline_bp - 1.0)
                     if st.vol_baseline_bp > 0 else 0.0)
            dec, _meta = plan_tick(
                state=st, mid=m["mid"], seg_low=m["seg_low"], seg_high=m["seg_high"],
                seg_taker_sell=m["seg_sell"], seg_taker_buy=m["seg_buy"],
                now_ts=now_ts, params=self.params, limits=self.limits,
                equity=self.equity, fill_notional=self.fill_notional,
                taker_fee_bp=TAKER_FEE_BP, maker_fee_bp=self.maker_fee_bp,
                half_spread=float(m.get("half_spread") or 0.0),
                sigma_norm=sigma,
                book=shared_book, marks=marks,
                # [§82/P5-A] 日亏闸输入：本 UTC 日车道已实现净额（关闭时不参与判定）
                day_pnl_usd=(day_pnl if lane_limits_enforce_enabled() else 0.0),
            )
            self._record_fills(dec)
            self.fills += len(dec.fills)
            self.flattens += sum(1 for f in dec.fills if f.is_flatten)
            d = dec.to_dict()
            d["data_age_sec"] = round(age, 1)
            decisions.append(d)

        self.last_tick_ts = now_ts
        self.ticks += 1
        self.save_states()
        worst_age = max(data_ages) if data_ages else -1.0
        self._refresh_registry(now_ts, worst_age)
        return {
            "ok": True, "lane_id": self.lane_id, "ts": now_ts,
            "ticks": self.ticks, "fills": self.fills, "flattens": self.flattens,
            "maker_fee_bp": self.maker_fee_bp,
            "quoting_symbols": sum(1 for d in decisions if d.get("action") == "quote"),
            "stale_symbols": [d["symbol"] for d in decisions
                              if str(d.get("skip") or "").startswith("stale_data")],
            "worst_data_age_sec": None if worst_age < 0 else round(worst_age, 1),
            "inventory": {s: round(st.qty, 8) for s, st in self.states.items()},
            "decisions": decisions,
        }

    def _refresh_registry(self, now_ts: Optional[float] = None,
                          worst_data_age: float = -1.0) -> None:
        """把行情数据年龄与熔断原因写入车道 health（前端熔断矩阵数据源）。"""
        try:
            from backend.services import lane_registry as reg

            breaker = self.last_error or None
            if breaker is None and worst_data_age >= 0 and \
                    MAX_DATA_AGE_SEC > 0 and worst_data_age > MAX_DATA_AGE_SEC:
                breaker = f"stale_data({worst_data_age/60:.1f}min>{MAX_DATA_AGE_SEC/60:.1f}min)"
            reg.update_health(self.lane_id, {
                "data_age_sec": round(worst_data_age, 1) if worst_data_age >= 0 else None,
                "breaker": breaker,
                "note": (f"shadow ticks={self.ticks} fills={self.fills} "
                         f"flattens={self.flattens}"),
            })
        except Exception as e:
            logger.debug("[F60] refresh_registry 失败: %s", e)

    # ── 报告 ──
    def report(self, days: int = 30) -> Dict[str, Any]:
        """影子期达标报告：从 lane_ledger 汇总 + 晋级判定。"""
        from backend.services import lane_ledger, lane_registry

        attr = lane_ledger.attribution(days=days, lane_id=self.lane_id)
        # attribution 返回 {total, by_lane, by_symbol}；这里取 total 层
        total = attr.get("total") or {}
        per_symbol = {x["symbol"]: {
            "n": x["n"], "net_bp": x["net_bp"], "spread_bp": x["spread_bp"],
            "price_bp": x["price_bp"], "fee_bp": x["fee_bp"],
            "notional": x["notional"], "net_usd": x["net_usd"],
        } for x in (attr.get("by_symbol") or []) if x.get("symbol")}
        net_bp = float(total.get("net_bp") or 0.0)
        fills = int(total.get("n") or 0)
        series = lane_ledger.daily_series(days=days, lane_id=self.lane_id)
        # 逐日序列（美元口径）——晋级判定需要「分折」，影子期用自然日作为一折
        folds = [{"date": row.get("date"), "net_usd": row.get("net_usd"),
                  "n": row.get("n")} for row in series or []]

        # ── 两项此前「无法验证」的晋升指标，现在用真实数据算 ──
        # fill_rate_ratio = 实测成交速率 ÷ 回放建模速率（基线存在车道 meta）
        baseline = (self.meta.get("replay_baseline") or {}).get("fills_per_symbol_hour")
        frr = lane_ledger.fill_rate_ratio(self.lane_id, baseline_per_symbol_hour=baseline,
                                          days=days)
        fr_stats = lane_ledger.fill_rate_stats(self.lane_id, days=days)
        dd = lane_ledger.max_drawdown_pct(self.lane_id, days=days, equity=self.equity)
        fstats = lane_ledger.flatten_stats(self.lane_id, days=days)
        baseline_flat = ((self.meta.get("replay_baseline") or {})
                         .get("flatten_price_bp"))

        rep = {
            "lane_id": self.lane_id, "venue": self.venue, "window_days": days,
            # [F75] flattens 与 fills 必须同窗口同源（此前用进程内计数 vs 30 天账本
            # ——重启即清零，与 fills 窗口不一致，报表平仓占比长期失真）。
            "fills": fills, "flattens": int((fstats or {}).get("flattens") or 0),
            "notional": round(float(total.get("notional") or 0.0), 2),
            # 无成交时六维是「无数据」而非 0——用 0 会把「没跑」读成「跑平了」
            "spread_bp": total.get("spread_bp") if fills else None,
            "price_bp": total.get("price_bp") if fills else None,
            "fee_bp": total.get("fee_bp") if fills else None,
            "net_bp": net_bp if fills else None,
            "net_usd": total.get("net_usd") if fills else None,
            "per_symbol": per_symbol, "daily": folds,
            "maker_fee_bp": self.maker_fee_bp,
            "fill_rate_ratio": frr,
            "fill_rate_stats": fr_stats,
            "replay_baseline_per_symbol_hour": baseline,
            "flatten_stats": fstats,
            "replay_baseline_flatten_price_bp": baseline_flat,
            "max_dd_pct": dd,
            "equity": self.equity,
        }
        try:
            # 影子期只跑了几小时时，日序列不足 4 折 → folds_positive 自然不通过（fail-closed）
            rep["promotion"] = lane_registry.evaluate_promotion({
                "source": "paper_shadow",
                "net_bp": net_bp, "n": fills,
                "folds": [{"net_bp": f["net_usd"], "t": None, "n": f["n"]}
                          for f in folds if f.get("net_usd") is not None],
                "fill_rate_ratio": frr,
                "max_dd_pct": dd,
            })
        except Exception as e:
            rep["promotion"] = {"ready": False, "passed": [], "failed": ["evaluate_error"],
                                "labels": {}, "progress_pct": 0.0, "reason": str(e)}
        return rep

    def status(self) -> Dict[str, Any]:
        return {
            "lane_id": self.lane_id, "venue": self.venue, "symbols": self.symbols,
            "equity": self.equity, "account_id": self.account_id,
            "strategy_type": self.strategy_type,
            "maker_fee_bp": self.maker_fee_bp, "ticks": self.ticks,
            "fills": self.fills, "flattens": self.flattens,
            "last_tick_ts": self.last_tick_ts, "last_error": self.last_error,
            # [F85] 复利/账户字段（前端「账户总览」卡片数据源）
            "compound_ratio": self.compound_ratio,
            "fill_notional": self.fill_notional,
            "account_equity": self._read_account_equity() if self.account_id else None,
            "states": {s: st.to_dict() for s, st in self.states.items()},
            "as_of": _now_iso(),
        }

    def archive_report(self, days: int = 30) -> bool:
        """把影子期报告落库（`lane_shadow_report`），形成可追溯的达标证据链。"""
        ensure_table()
        rep = self.report(days=days)
        try:
            import json

            from sqlalchemy import text

            from backend.core.tenant import system_identity
            from backend.database.connection import SessionLocal

            with system_identity():
                with SessionLocal() as db:
                    db.execute(text(
                        "INSERT INTO lane_shadow_report (lane_id, window_days, fills,"
                        " flattens, notional, spread_bp, price_bp, fee_bp, net_bp,"
                        " net_usd, per_symbol, promotion, note) VALUES"
                        " (:l, :w, :f, :fl, :n, :sp, :pr, :fe, :nb, :nu,"
                        " CAST(:ps AS JSONB), CAST(:pm AS JSONB), :note)"
                    ), {
                        "l": self.lane_id, "w": int(days),
                        "f": int(rep.get("fills") or 0),
                        "fl": int(rep.get("flattens") or 0),
                        "n": float(rep.get("notional") or 0.0),
                        "sp": rep.get("spread_bp"), "pr": rep.get("price_bp"),
                        "fe": rep.get("fee_bp"), "nb": rep.get("net_bp"),
                        "nu": rep.get("net_usd"),
                        "ps": json.dumps(rep.get("per_symbol") or {}, ensure_ascii=False,
                                         default=str),
                        "pm": json.dumps(rep.get("promotion") or {}, ensure_ascii=False,
                                         default=str),
                        "note": f"maker_fee_bp={self.maker_fee_bp}; ticks={self.ticks}",
                    })
                    db.commit()
            return True
        except Exception as e:
            self.last_error = f"archive_report: {e}"
            logger.warning("[F60] archive_report 失败: %s", e)
            return False


# ═══════════════════════ 调度接线 ═══════════════════════

_SHADOW_RUNNERS: Dict[str, ShadowRunner] = {}
_SHADOW_LOCK = None


def get_runner(lane_id: str = DEFAULT_LANE_ID) -> Optional[ShadowRunner]:
    """取（或建）车道影子期驱动器；运行态从 DB 恢复。"""
    global _SHADOW_LOCK
    if _SHADOW_LOCK is None:
        import threading
        _SHADOW_LOCK = threading.Lock()
    with _SHADOW_LOCK:
        if lane_id in _SHADOW_RUNNERS:
            return _SHADOW_RUNNERS[lane_id]
        try:
            from backend.services import lane_registry as reg

            lane = reg.get_lane(lane_id)
            if not lane:
                return None
            meta = lane.get("meta") or {}
            stored = meta.get("params") or {}
            from backend.services.market_maker.core import LaneRiskLimits, QuoteParams

            params = QuoteParams(**{k: v for k, v in stored.items()
                                    if k in QuoteParams.__dataclass_fields__})
            limits = LaneRiskLimits(**{k: v for k, v in stored.items()
                                       if k in LaneRiskLimits.__dataclass_fields__})
            # [F77 2026-09-12] 币种宇宙由注册表 meta.symbols 控制。
            # 此前 get_runner 不传 symbols ⇒ 永远跑 DEFAULT_SYMBOLS（6 币），
            # 组合回放证实 6 币共享账本下 alt 币全部负边际，只有 BTC（及
            # 部分 ETH 组合）为正 ⇒ 币种精选无法上线。现在读注册表。
            _symbols = list(meta.get("symbols") or [])
            _symbols = [str(s) for s in _symbols if str(s)] or list(DEFAULT_SYMBOLS)
            _fn = float(stored.get("fill_notional") or FILL_NOTIONAL)
            r = ShadowRunner(
                lane_id=lane_id, venue=str(meta.get("venue") or DEFAULT_VENUE),
                equity=float(meta.get("shadow_equity") or 5000.0),
                account_id=meta.get("paper_account_id"),
                strategy_type=str(meta.get("strategy_type") or "MM"),
                params=params, limits=limits, symbols=_symbols,
                fill_notional=_fn,
            )
            r.meta = meta          # 供 report() 读取回放基线等
            r.load_states()
            # [F71b] 用**回放窗口**的已实现波动做高波动基准（自适应当前盘会失去意义）
            vol_base = (meta.get("replay_baseline") or {}).get("vol_baseline_bp") or {}
            # [F79 2026-09-12] 注册表始终权威：此前只在持久化基线 ≤0 时播种 ⇒
            # 基线在注册表更新（重锚回放窗口）后永远不会生效——持久化副本粘住旧值，
            # 实盘 sigma 口径与验证回放漂移。现在每次 runner 重建都从注册表覆写。
            for sym, st in r.states.items():
                if vol_base.get(sym):
                    st.vol_baseline_bp = float(vol_base[sym])
            _SHADOW_RUNNERS[lane_id] = r
            return r
        except Exception as e:
            logger.warning("[F60] get_runner(%s) 失败: %s", lane_id, e)
            return None


def shadow_tick_task(lane_id: str = DEFAULT_LANE_ID) -> Dict[str, Any]:
    """调度器任务：车道为 paper + active 时才推进影子期。

    默认 `status="stopped"`，因此**必须显式启动车道**才会开始跑——
    避免部署后自动下单。
    """
    try:
        from backend.services import lane_registry as reg

        lane = reg.get_lane(lane_id)
        if not lane:
            return {"ok": False, "reason": f"车道不存在: {lane_id}"}
        if lane.get("mode") != "paper":
            return {"ok": False, "reason": f"mode={lane.get('mode')} 非 paper，影子期不跑"}
        health = lane.get("health") or {}
        if health.get("drill"):
            # 组合级熔断演练：真的停报价（由 /api/trading/risk/drill 写入）
            return {"ok": False, "reason": f"drill: {health.get('drill_reason') or '演练中'}"}
        if lane.get("status") != "active":
            return {"ok": False, "reason": f"status={lane.get('status')}，未启动"}
        runner = get_runner(lane_id)
        if runner is None:
            return {"ok": False, "reason": "runner 初始化失败"}
        res = runner.tick()
        if not res.get("ok"):
            logger.warning("[F60] shadow tick 失败 lane=%s: %s", lane_id, res.get("reason"))
        return res
    except Exception as e:
        logger.warning("[F60] shadow_tick_task 异常: %s", e)
        return {"ok": False, "reason": str(e)}


def shadow_archive_task(lane_id: str = DEFAULT_LANE_ID, days: int = 30) -> Dict[str, Any]:
    """每日归档影子期报告（无论车道是否在跑都归档一次快照）。"""
    runner = get_runner(lane_id)
    if runner is None:
        return {"ok": False, "reason": "runner 初始化失败"}
    ok = runner.archive_report(days=days)
    return {"ok": ok, "lane_id": lane_id, "days": days}


def register_shadow_task(
    *,
    lane_id: str = DEFAULT_LANE_ID,
    interval_sec: Optional[int] = None,
) -> bool:
    """把影子期 tick + 每日归档注册到全局调度器（由 main.py 启动时调用）。

    `MM_SHADOW_ENABLED=0` 可整体关闭（默认开启注册，但车道未启动时不会下单）。
    """
    if os.getenv("MM_SHADOW_ENABLED", "1").strip().lower() in ("0", "false", "no"):
        logger.info("[F60] MM_SHADOW_ENABLED=0，跳过影子期调度注册")
        return False
    interval = int(interval_sec or os.getenv("MM_SHADOW_INTERVAL_SEC", "15"))
    try:
        from backend.services.scheduler import task_scheduler

        task_scheduler.start()
        # 注意：add_interval_task 的签名是
        # (task_func, interval_seconds, task_id, max_instances, next_run_time, *args, **kwargs)，
        # 额外参数只能作为 **kwargs 传（传 args=... 会被 apscheduler 当成非法关键字）。
        task_scheduler.add_interval_task(
            shadow_tick_task,
            interval,
            f"mm_shadow_tick_{lane_id}",
            1,
            None,
            lane_id=lane_id,
        )
        # 每日 00:10 归档一次报告（错开 00:00 的日切任务）
        task_scheduler.add_cron_task(
            shadow_archive_task,
            f"mm_shadow_archive_{lane_id}",
            1,
            hour=0, minute=10,
            lane_id=lane_id, days=30,
        )
        logger.info("[F60] 影子期调度已注册: %s 每 %ss + 每日 00:10 归档", lane_id, interval)
        return True
    except Exception as e:
        logger.warning("[F60] 影子期调度注册失败: %s", e)
        return False

