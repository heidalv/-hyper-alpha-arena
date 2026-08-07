"""Paper Engine One-Way 净额计算工具（净额风险 + 分层记账）

设计原则（与 Hyperliquid/Asterdex 真实 One-Way 模式对齐）:
- **记账层不变**: PaperPosition 仍按 (account_id, symbol, side, trade_nature) 分行存储，
  每个层级 (scalp/swing/trend) 独立保留 entry/TP/SL/DCA/子仓追踪。
- **风险层净额**: 保证金占用、爆仓价、可用余额、总权益、风险敞口全部按
  每个币种的净头寸 (signed size 求和后取绝对值) 计算。

核心公式:
- signed_size(long) = +size, signed_size(short) = -size
- net_signed_size = Σ signed_size_per_row
- net_side = 'long' if net_signed_size > 0 else 'short'
- net_size = |net_signed_size|
- 净保证金 = net_size × net_weighted_entry ÷ unified_leverage
  （取代行级 margin 求和，对冲对释放保证金）
- 净爆仓价 = 净方向单一爆仓价
- 净 uPnL = 各行 uPnL 代数求和（等价于净头寸 uPnL，但保留行级精度）

本模块为纯函数 + 数据类，零副作用，易测试。

注意: maintenance_margin_rate 默认 0.005 仅是 fallback。
调用方（paper_engine）应通过 fee_schedule_service.get_maint_margin_rate(exchange)
传入 per-exchange 精确值（asterdex/hyperliquid=0.005, binance=0.004）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────────
# 数据类
# ────────────────────────────────────────────────────────────────────

@dataclass
class NetPosition:
    """单个币种的净头寸视图。

    Attributes:
        symbol: 币种
        net_side: 净方向 'long' / 'short' / 'flat'
        net_size: 净头寸绝对值 (>=0)
        net_signed_size: 带符号净头寸 (long 正 / short 负 / flat 0)
        net_weighted_entry: 净头寸加权均价（仅 net_size>0 时有意义）
        unified_leverage: 该币种统一杠杆（取所有 open 行的最大值，HL 行为）
        row_margin_sum: 行级保证金求和（用于审计对比）
        net_margin: 净保证金（取代 row_margin_sum 用于风险/余额计算）
        net_liquidation_price: 净方向单一爆仓价（net_size=0 时为 0.0）
        net_unrealized_pnl: 净 uPnL（各行 uPnL 代数和）
        row_count: 聚合的 open 行数
        hedge_release: 对冲释放的保证金 = row_margin_sum - net_margin（>=0）
        rows: 原始 PaperPosition 行（引用，不修改）
    """
    symbol: str
    net_side: str = "flat"
    net_size: float = 0.0
    net_signed_size: float = 0.0
    net_weighted_entry: float = 0.0
    unified_leverage: float = 1.0
    row_margin_sum: float = 0.0
    net_margin: float = 0.0
    net_liquidation_price: float = 0.0
    net_unrealized_pnl: float = 0.0
    row_count: int = 0
    hedge_release: float = 0.0
    rows: List[Any] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "net_side": self.net_side,
            "net_size": round(self.net_size, 8),
            "net_signed_size": round(self.net_signed_size, 8),
            "net_weighted_entry": round(self.net_weighted_entry, 6),
            "unified_leverage": self.unified_leverage,
            "row_margin_sum": round(self.row_margin_sum, 2),
            "net_margin": round(self.net_margin, 2),
            "net_liquidation_price": round(self.net_liquidation_price, 2),
            "net_unrealized_pnl": round(self.net_unrealized_pnl, 2),
            "row_count": self.row_count,
            "hedge_release": round(self.hedge_release, 2),
        }


@dataclass
class AccountNetExposure:
    """整个账户的净额风险敞口视图。"""
    per_symbol: Dict[str, NetPosition] = field(default_factory=dict)
    total_net_margin: float = 0.0       # Σ net_margin (跨币种)
    total_row_margin: float = 0.0       # Σ row_margin_sum (审计对比)
    total_net_notional: float = 0.0     # Σ |net_signed_size × net_weighted_entry|
    total_net_upnl: float = 0.0         # Σ net_unrealized_pnl
    total_hedge_release: float = 0.0    # Σ hedge_release
    symbol_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "per_symbol": {s: np_.to_dict() for s, np_ in self.per_symbol.items()},
            "total_net_margin": round(self.total_net_margin, 2),
            "total_row_margin": round(self.total_row_margin, 2),
            "total_net_notional": round(self.total_net_notional, 2),
            "total_net_upnl": round(self.total_net_upnl, 2),
            "total_hedge_release": round(self.total_hedge_release, 2),
            "symbol_count": self.symbol_count,
        }


# ────────────────────────────────────────────────────────────────────
# 基础工具
# ────────────────────────────────────────────────────────────────────

def signed_size(side: str, size: float) -> float:
    """方向 → 带符号 size: long → +size, short → -size。

    对 'buy'/'sell' 也兼容（开仓视角），便于从订单直接计算。
    """
    s = str(side or "").strip().lower()
    if s in ("long", "buy"):
        return abs(float(size or 0))
    if s in ("short", "sell"):
        return -abs(float(size or 0))
    return 0.0


def net_side_from_signed(net_signed: float) -> str:
    """带符号 size → 净方向: >0 long, <0 short, ==0 flat。"""
    if net_signed > 1e-12:
        return "long"
    if net_signed < -1e-12:
        return "short"
    return "flat"


def calc_net_liquidation_price(
    net_weighted_entry: float,
    net_side: str,
    leverage: float,
    maintenance_margin_rate: float = 0.005,
) -> float:
    """净头寸单一爆仓价（简化版逐仓公式，与 _calc_liquidation_price 对齐）。

    net_side == 'flat' 或 leverage <= 1 时返回 0.0。
    """
    if net_side == "flat" or leverage <= 1 or net_weighted_entry <= 0:
        return 0.0
    mm = float(maintenance_margin_rate or 0.005)
    if net_side == "long":
        return net_weighted_entry * (1 - (1 / leverage) + mm)
    # short
    return net_weighted_entry * (1 + (1 / leverage) - mm)


# ────────────────────────────────────────────────────────────────────
# 核心聚合
# ────────────────────────────────────────────────────────────────────

def _row_fields(p: Any) -> Dict[str, float]:
    """从 PaperPosition 行（ORM 或 dict）安全提取字段，统一为 float。"""
    def _g(name: str, default: float = 0.0) -> float:
        v = getattr(p, name, None)
        if v is None and isinstance(p, dict):
            v = p.get(name)
        try:
            return float(v or 0)
        except (TypeError, ValueError):
            return default
    return {
        "side": str(getattr(p, "side", None) or (p.get("side") if isinstance(p, dict) else "") or "").lower(),
        "size": _g("size"),
        "entry_price": _g("entry_price"),
        "leverage": max(1.0, _g("leverage", 1.0)),
        "margin": _g("margin"),
        "unrealized_pnl": _g("unrealized_pnl"),
    }


def aggregate_rows_to_net(
    symbol: str,
    rows: Iterable[Any],
    maintenance_margin_rate: float = 0.005,
) -> NetPosition:
    """将单个币种的所有 open PaperPosition 行聚合为 NetPosition。

    算法:
    1. net_signed_size = Σ signed_size(side, size)
    2. net_weighted_entry = Σ(entry_i × signed_size_i) / net_signed_size  (净方向加权)
       - 注意: 对冲时相反方向的行贡献负的 notional，自动抵消
    3. unified_leverage = max(leverage_i)  (HL 同币种杠杆必须一致，取最大)
    4. net_margin = net_size × net_weighted_entry ÷ unified_leverage
    5. net_unrealized_pnl = Σ row.unrealized_pnl  (代数和，等价净头寸 uPnL)
    6. row_margin_sum = Σ row.margin  (审计用)
    7. hedge_release = max(0, row_margin_sum - net_margin)

    Args:
        symbol: 币种
        rows: 该币种的 open PaperPosition 行（ORM 对象或 dict 均可）
        maintenance_margin_rate: 维持保证金率（爆仓价计算用）
    """
    np_ = NetPosition(symbol=symbol)

    signed_sum = 0.0
    # 加权 notional 求和: Σ(entry × signed_size)
    weighted_signed_notional = 0.0
    max_lev = 1.0
    row_margin_sum = 0.0
    net_upnl = 0.0
    row_list: List[Any] = []

    for p in rows:
        row_list.append(p)
        f = _row_fields(p)
        ss = signed_size(f["side"], f["size"])
        signed_sum += ss
        weighted_signed_notional += f["entry_price"] * ss
        if f["leverage"] > max_lev:
            max_lev = f["leverage"]
        row_margin_sum += f["margin"]
        net_upnl += f["unrealized_pnl"]

    np_.net_signed_size = signed_sum
    np_.net_side = net_side_from_signed(signed_sum)
    np_.net_size = abs(signed_sum)
    np_.unified_leverage = max_lev
    np_.row_margin_sum = row_margin_sum
    np_.net_unrealized_pnl = net_upnl
    np_.row_count = len(row_list)
    np_.rows = row_list

    # 净方向加权均价: 用 signed_sum 做分母
    # 当 net_signed_size ≈ 0 (完全对冲) 时均价无意义，置 0
    if abs(signed_sum) > 1e-12:
        np_.net_weighted_entry = weighted_signed_notional / signed_sum
        # 均价必须为正（理论上应为正；防御性 abs）
        if np_.net_weighted_entry < 0:
            np_.net_weighted_entry = abs(np_.net_weighted_entry)
    else:
        # 完全对冲: 净头寸 0，保证金按 max row notional 估算（保守）
        # 实际 HL 完全对冲时保证金需求极低，这里保守取 row_margin_sum 的较小部分
        np_.net_weighted_entry = 0.0
        np_.net_size = 0.0

    # 净保证金
    if np_.net_size > 0 and np_.net_weighted_entry > 0 and max_lev > 0:
        np_.net_margin = (np_.net_size * np_.net_weighted_entry) / max_lev
    else:
        # 完全对冲或无仓位: 净保证金 0（行级保证金全部释放）
        np_.net_margin = 0.0

    # 对冲释放量（审计指标）
    np_.hedge_release = max(0.0, row_margin_sum - np_.net_margin)

    # 净爆仓价
    np_.net_liquidation_price = calc_net_liquidation_price(
        np_.net_weighted_entry, np_.net_side, np_.unified_leverage,
        maintenance_margin_rate,
    )

    return np_


def compute_net_position(
    db, account_id: int, symbol: str, maintenance_margin_rate: float = 0.005,
) -> NetPosition:
    """从 DB 查询并聚合单个币种的净头寸。

    惰性导入 PaperPosition 避免循环依赖。
    """
    from backend.database.models import PaperPosition
    rows = (
        db.query(PaperPosition)
        .filter(
            PaperPosition.account_id == account_id,
            PaperPosition.symbol == symbol,
            PaperPosition.status == "open",
        )
        .all()
    )
    return aggregate_rows_to_net(symbol, rows, maintenance_margin_rate)


def compute_account_net_exposure(
    db, account_id: int, maintenance_margin_rate: float = 0.005,
) -> AccountNetExposure:
    """聚合整个账户所有币种的净额风险敞口。

    Returns:
        AccountNetExposure, 包含 per_symbol dict 和总计。
    """
    from backend.database.models import PaperPosition
    from sqlalchemy import distinct

    exposure = AccountNetExposure()

    # 取该账户所有 open 仓位的币种列表
    symbols = [
        r[0]
        for r in db.query(distinct(PaperPosition.symbol)).filter(
            PaperPosition.account_id == account_id,
            PaperPosition.status == "open",
        ).all()
    ]

    total_net_margin = 0.0
    total_row_margin = 0.0
    total_net_notional = 0.0
    total_net_upnl = 0.0
    total_hedge_release = 0.0

    for sym in symbols:
        np_ = compute_net_position(db, account_id, sym, maintenance_margin_rate)
        exposure.per_symbol[sym] = np_
        total_net_margin += np_.net_margin
        total_row_margin += np_.row_margin_sum
        total_net_notional += np_.net_size * np_.net_weighted_entry
        total_net_upnl += np_.net_unrealized_pnl
        total_hedge_release += np_.hedge_release

    exposure.total_net_margin = total_net_margin
    exposure.total_row_margin = total_row_margin
    exposure.total_net_notional = total_net_notional
    exposure.total_net_upnl = total_net_upnl
    exposure.total_hedge_release = total_hedge_release
    exposure.symbol_count = len(symbols)

    return exposure


# ────────────────────────────────────────────────────────────────────
# 保证金增量计算（开仓前检查用）
# ────────────────────────────────────────────────────────────────────

def compute_margin_delta_for_order(
    current_net: NetPosition,
    order_side: str,
    order_size: float,
    order_price: float,
    order_leverage: float,
) -> Tuple[float, str]:
    """计算"开此仓后"相对"开此仓前"的净保证金增量。

    匹配真实交易所 One-Way 行为: 反向订单先抵消已有净头寸，剩余部分才占用新保证金。

    Args:
        current_net: 开仓前该币种的净头寸视图
        order_side: 订单方向 'buy'/'sell' 或 'long'/'short'
        order_size: 订单数量 (绝对值, >0)
        order_price: 订单预估成交价
        order_leverage: 订单杠杆

    Returns:
        (margin_delta, scenario):
        - margin_delta: 净保证金增量 (>=0)，即开仓需额外冻结的保证金
        - scenario: 'open_new' / 'add_same_side' / 'partial_hedge' / 'full_hedge_flip'
          （用于日志/审计）
    """
    order_signed = signed_size(order_side, order_size)
    new_signed = current_net.net_signed_size + order_signed

    # 新净头寸的保证金
    new_size = abs(new_signed)
    new_side = net_side_from_signed(new_signed)

    # 统一杠杆: 取订单杠杆与现有净杠杆的最大值（HL 同币种杠杆一致）
    unified_lev = max(float(order_leverage or 1.0), current_net.unified_leverage, 1.0)

    if new_size <= 1e-12:
        # 新订单完全平掉现有净头寸（或更小）→ 净保证金归零，释放全部旧保证金
        # 这种情况下保证金增量是负的（释放），但调用方只关心"需要额外多少"
        return 0.0, "full_hedge_flip"

    # 新净头寸均价估算: 简化用 order_price 作为新净头寸的参考均价
    # （精确加权需要知道现有各行均价，但保证金增量对均价不敏感，量级正确即可）
    new_margin = (new_size * float(order_price or current_net.net_weighted_entry)) / unified_lev
    old_margin = current_net.net_margin

    delta = max(0.0, new_margin - old_margin)

    # 判定场景（基于订单方向 vs 现有净头寸方向）
    order_side_sign = 1.0 if order_signed > 0 else -1.0
    cur_side_sign = 1.0 if current_net.net_signed_size > 0 else (
        -1.0 if current_net.net_signed_size < 0 else 0.0
    )
    if current_net.net_size <= 1e-12:
        scenario = "open_new"
    elif cur_side_sign == 0.0:
        scenario = "open_new"
    elif order_side_sign == cur_side_sign:
        # 订单与现有净头寸同向 → 加仓
        scenario = "add_same_side"
    elif new_side == current_net.net_side:
        # 订单反向但未翻转（部分对冲，净方向不变但量减少）
        scenario = "partial_hedge"
    else:
        # 反向翻转（净方向改变）
        scenario = "full_hedge_flip"

    return delta, scenario


# ────────────────────────────────────────────────────────────────────
# 开关守卫
# ────────────────────────────────────────────────────────────────────

def is_netting_enabled() -> bool:
    """读取 PAPER_NETTING_MODE 开关（默认 true）。

    所有净额计算路径应先检查此函数；false 时回退到旧行级求和。
    """
    try:
        from backend.config.settings import PAPER_NETTING_MODE
        return bool(PAPER_NETTING_MODE)
    except Exception:
        # settings 未加载时默认开启净额（生产默认行为）
        return True
