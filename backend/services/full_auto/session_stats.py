"""Session 绩效汇总 — 从 monolith _update_session_stats 迁出（整改#8 Phase2）。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from sqlalchemy.orm import Session


@dataclass
class SessionStatsHost:
    get_trading_account_id: Callable = field(repr=False, default=lambda *a, **k: 0)


def build_session_stats_host(svc) -> SessionStatsHost:
    return SessionStatsHost(get_trading_account_id=svc._get_trading_account_id)


def update_session_stats(
    db: Session,
    session,
    active_ids: list,
    host: SessionStatsHost,
) -> None:
    from backend.database.models import StrategyTrade, PaperPosition, PaperBalance

    all_ids = list(active_ids) + list(session.terminated_strategy_ids or [])
    trading_mode = session.trading_mode or "paper"
    # P5-fix(2026-05-08): paper 模式必须用 paper_account_id 查持仓/余额，
    # 否则统计永远 0（持仓在 acct=4，但旧逻辑读 acct=3）
    account_id = (
        host.get_trading_account_id(db, session)
        if trading_mode == "paper"
        else session.account_id
    )

    # ── 账户快照（真相源）──
    paper_bal = None
    reset_at = None
    initial_capital = 10000.0
    current_equity: Optional[float] = None
    if account_id:
        try:
            paper_bal = db.query(PaperBalance).filter(
                PaperBalance.account_id == account_id
            ).first()
        except Exception:
            paper_bal = None
        if paper_bal:
            if paper_bal.last_reset_at:
                reset_at = paper_bal.last_reset_at
            initial_capital = float(paper_bal.initial_balance or 10000)
            current_equity = float(paper_bal.total_equity or initial_capital)

    total_pnl = 0.0
    total_trades = 0
    winning_trades = 0

    if trading_mode == "paper":
        # —— PnL：账户权益差（含 funding / fees / 全部已/未实现）——
        if current_equity is not None:
            total_pnl = current_equity - initial_capital
        else:
            # 极端 fallback：PaperBalance 缺失时，沿用 PaperPosition 手动汇总
            # （同 live 分支下方的 StrategyTrade fallback 逻辑保持对称）
            total_pnl = 0.0

        # —— 计数：PaperPosition 唯一源 ——
        _price_cache: dict[str, float] = {}
        try:
            from backend.services.price_cache import get_cached_price
            for sym in (session.symbols or []):
                p = get_cached_price(sym, "CRYPTO", "mainnet")
                if p:
                    _price_cache[sym] = p
        except Exception:
            pass

        _pos_filter = [PaperPosition.account_id == account_id]
        if all_ids:
            _pos_filter.append(PaperPosition.strategy_id.in_(all_ids))
        if reset_at:
            _pos_filter.append(PaperPosition.opened_at >= reset_at)
        all_pos = db.query(PaperPosition).filter(*_pos_filter).all()

        _fallback_pnl = 0.0
        for pos in all_pos:
            total_trades += 1
            partial_pnl = float(pos.partial_realized_pnl or 0)
            pos_pnl = 0.0

            if pos.status in ("closed", "liquidated"):
                if pos.close_price and pos.entry_price:
                    sz = float(pos.size or 0)
                    if pos.side == "long":
                        pos_pnl = (float(pos.close_price) - float(pos.entry_price)) * sz + partial_pnl
                    else:
                        pos_pnl = (float(pos.entry_price) - float(pos.close_price)) * sz + partial_pnl
                else:
                    pos_pnl = partial_pnl
            elif pos.status == "open":
                mark = _price_cache.get(pos.symbol) or pos.mark_price
                if mark and pos.entry_price:
                    sz = float(pos.size or 0)
                    if pos.side == "long":
                        pos_pnl = (float(mark) - float(pos.entry_price)) * sz + partial_pnl
                    else:
                        pos_pnl = (float(pos.entry_price) - float(mark)) * sz + partial_pnl
                else:
                    pos_pnl = float(pos.unrealized_pnl or 0) + partial_pnl

            if pos_pnl > 0:
                winning_trades += 1
            _fallback_pnl += pos_pnl

        # paper_bal 为空时启用 fallback（不含 fees，比权益差偏乐观，仅保底）
        if current_equity is None:
            total_pnl = _fallback_pnl

    else:
        # live 模式：以 StrategyTrade 为唯一 PnL / 计数源
        if all_ids:
            st_trades = db.query(StrategyTrade).filter(
                StrategyTrade.strategy_id.in_(all_ids),
            ).all()
            for t in st_trades:
                total_trades += 1
                pnl = float(t.pnl or 0)
                total_pnl += pnl
                if pnl > 0:
                    winning_trades += 1

    session.total_pnl = round(total_pnl, 4)
    session.total_trades = total_trades
    session.winning_trades = winning_trades

    # 回撤计算：优先用 PaperBalance.total_equity，缺失时退回 initial+total_pnl
    if current_equity is None:
        current_equity = initial_capital + total_pnl

    peak = session.peak_balance or initial_capital
    # 真实权益高于峰值时，更新峰值
    if current_equity > peak:
        session.peak_balance = round(current_equity, 4)
        peak = current_equity
    if peak > 0:
        current_dd = (peak - current_equity) / peak
        if current_dd > (session.max_drawdown or 0):
            session.max_drawdown = round(current_dd, 6)
        session.current_drawdown = round(current_dd, 6)
