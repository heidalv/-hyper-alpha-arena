"""FeeContext — 费用感知上下文。

把"这笔交易要花多少钱才能保本"算清楚，同时统计当日已耗手续费与剩余交易额度，
供 UnifiedDecisionGate 硬约束 + 提示词注入双用途。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Hyperliquid taker 0.035% × 开平两腿 + 滑点预估 0.02%
ROUNDTRIP_FEE_PCT = 0.0007
SLIPPAGE_EST_PCT = 0.0002
ROUNDTRIP_COST_PCT = ROUNDTRIP_FEE_PCT + SLIPPAGE_EST_PCT  # 0.09% of notional


@dataclass
class FeeContext:
    roundtrip_cost_pct: float          # 往返成本占名义仓位比例
    fees_paid_today: float             # 当日已付手续费 (USD)
    gross_pnl_today: float             # 当日毛盈亏 (USD)
    opens_today: int                   # 当日开仓笔数
    daily_cap: int                     # 当日开仓上限
    symbol_opens_today: dict           # symbol -> 当日开仓笔数

    @property
    def trades_remaining(self) -> int:
        return max(0, self.daily_cap - self.opens_today)

    def breakeven_move_pct(self, leverage: float = 1.0) -> float:
        """价格需要朝有利方向走多少（%）才能覆盖手续费+滑点。与杠杆无关（按名义算）。"""
        _ = leverage
        return self.roundtrip_cost_pct * 100

    def prompt_block(self, *, show_trade_cap: bool = True) -> str:
        fee_eat = ""
        if self.gross_pnl_today > 0:
            ratio = self.fees_paid_today / self.gross_pnl_today * 100
            fee_eat = f"，已吃掉当日毛利的 {ratio:.0f}%"
        cap_line = ""
        if show_trade_cap and self.daily_cap > 0:
            if self.trades_remaining <= 0:
                # 真的用尽才允许以额度为由停开
                cap_line = (
                    f"- ⛔ 今日已开仓 {self.opens_today} 笔，已达上限 {self.daily_cap} 笔/日，"
                    f"**额度确已用尽**，本日不再开新仓\n"
                )
            else:
                # 额度充足：额度是"上限"不是"任务"，但严禁 LLM 拿"额度不足"当 hold 借口
                # （历史上 LLM 在信号本就弱时会编造"额度用尽/无交易额度"来搪塞，误导运营）
                cap_line = (
                    f"- 今日已开仓 {self.opens_today} 笔，**剩余额度 {self.trades_remaining} 笔（充足）**"
                    f"（上限 {self.daily_cap} 笔/日）；额度是上限、不是必须用满的任务。\n"
                    f"  ⚠️ **额度尚未用尽——禁止以「额度不足/额度用尽/无交易额度」作为 hold 理由**；"
                    f"要 hold 必须给出行情或信号层面的真实依据（如方向不明、盈亏比不足、结构位不佳）\n"
                )
        return (
            "## 💸 交易成本铁律（必须纳入每个决策）\n"
            f"- 每笔往返成本 ≈ 名义仓位的 {self.roundtrip_cost_pct*100:.2f}%"
            f"（taker 0.035%×2 + 滑点），价格至少朝有利方向走 "
            f"{self.roundtrip_cost_pct*100:.2f}% 才保本\n"
            f"- 今日已付手续费 ${self.fees_paid_today:.0f}{fee_eat}\n"
            f"{cap_line}"
            "- 预期获利 < 往返成本 3 倍的交易没有数学期望，必须 hold\n"
            "- 频繁小止盈是手续费绞肉机：竞技场冠军模型两周仅交易 43 次、"
            "胜率 30% 仍盈利 22%，靠的是亏小赚大，不是出手次数"
        )


def _query_fee_stats(db, account_id: int, trade_nature: str | None = None):
    """在给定 db 上跑当日开仓/手续费/毛盈亏统计，返回 (fees, gross, opens, sym_opens)。

    trade_nature: 若指定则只统计该 nature 的订单（用于按 tier 独立配额计数）。
    """
    from sqlalchemy import func
    from backend.database.models import PaperOrder

    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    ).replace(tzinfo=None)

    query = (
        db.query(
            PaperOrder.symbol,
            PaperOrder.close_reason,
            func.coalesce(PaperOrder.fee, 0.0),
            func.coalesce(PaperOrder.pnl, 0.0),
        )
        .filter(
            PaperOrder.account_id == account_id,
            PaperOrder.status == "filled",
            PaperOrder.created_at >= today_start,
        )
    )
    if trade_nature:
        query = query.filter(PaperOrder.trade_nature == trade_nature)
    rows = query.all()

    fees = 0.0
    gross = 0.0
    opens = 0
    sym_opens: dict = {}
    for symbol, close_reason, fee, pnl in rows:
        fees += float(fee or 0)
        if pnl and float(pnl) > 0:
            gross += float(pnl)
        if not close_reason:  # 开仓单
            opens += 1
            sym_opens[symbol] = sym_opens.get(symbol, 0) + 1
    return fees, gross, opens, sym_opens


def build_fee_context(db, account_id: int, daily_cap: int = 10, trade_nature: str | None = None) -> FeeContext:
    """统计当日（UTC）该账户的开仓数 / 手续费 / 毛盈亏。

    trade_nature: 若指定则只统计该 nature（用于按 tier 独立配额计数）。
    """
    fees = 0.0
    gross = 0.0
    opens = 0
    sym_opens: dict = {}
    try:
        from backend.database.connection import SessionLocal
        _short_db = SessionLocal()
        try:
            fees, gross, opens, sym_opens = _query_fee_stats(_short_db, account_id, trade_nature=trade_nature)
        finally:
            _short_db.close()
    except Exception as err:
        # fail-closed：统计失败时不能再按 0 计算——那等价于"今日未开仓"，
        # 会让 unified_gate 的日额度门禁在数据库异常期间完全失效（额度形同虚设）。
        # 保守视为已达上限，宁可误拦几笔也不放行未经额度校验的交易。
        logger.warning(
            "[FeeContext] 当日统计失败，保守视为已达上限（不再按零计）: %s", err
        )
        fees, gross, opens, sym_opens = 0.0, 0.0, daily_cap, {}

    return FeeContext(
        roundtrip_cost_pct=ROUNDTRIP_COST_PCT,
        fees_paid_today=round(fees, 2),
        gross_pnl_today=round(gross, 2),
        opens_today=opens,
        daily_cap=daily_cap,
        symbol_opens_today=sym_opens,
    )


def count_nature_opens(
    db,
    account_id: int,
    *,
    nature: str | None = None,
    symbol: str | None = None,
    since_days: int = 0,
) -> int:
    """统计指定 nature/symbol 的开仓笔数（UTC 日界或近 since_days 天）。"""
    try:
        from datetime import timedelta
        from backend.database.models import PaperOrder

        if since_days > 0:
            since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=since_days)
        else:
            since = datetime.now(timezone.utc).replace(
                hour=0, minute=0, second=0, microsecond=0
            ).replace(tzinfo=None)

        q = db.query(PaperOrder).filter(
            PaperOrder.account_id == account_id,
            PaperOrder.status == "filled",
            PaperOrder.created_at >= since,
            PaperOrder.close_reason.is_(None),
        )
        if nature:
            q = q.filter(PaperOrder.trade_nature == nature)
        if symbol:
            q = q.filter(PaperOrder.symbol == str(symbol).upper())
        return int(q.count())
    except Exception as err:
        logger.debug("[FeeContext] count_nature_opens 失败: %s", err)
        return 0
