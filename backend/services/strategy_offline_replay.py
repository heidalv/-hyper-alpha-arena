"""历史订单离线回放 — 对比策略表现。

验收标准：对比胜率、盈亏比、最大回撤、手续费占比。
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ReplayMetrics:
    total_trades: int = 0
    wins: int = 0
    win_rate: float = 0.0
    total_pnl: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    profit_factor: float = 0.0
    max_drawdown: float = 0.0
    fee_ratio: float = 0.0
    by_close_reason: Dict[str, float] = field(default_factory=dict)
    by_tier: Dict[str, float] = field(default_factory=dict)
    by_nature: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_trades": self.total_trades,
            "wins": self.wins,
            "win_rate": round(self.win_rate, 4),
            "total_pnl": round(self.total_pnl, 2),
            "avg_win": round(self.avg_win, 2),
            "avg_loss": round(self.avg_loss, 2),
            "profit_factor": round(self.profit_factor, 2),
            "max_drawdown": round(self.max_drawdown, 2),
            "fee_ratio": round(self.fee_ratio, 4),
            "by_close_reason": self.by_close_reason,
            "by_tier": self.by_tier,
            "by_nature": self.by_nature,
        }


def _resolve_pnl(row: dict) -> float:
    partial = float(row.get("partial_realized_pnl") or 0)
    unrealized = float(row.get("unrealized_pnl") or 0)
    if partial != 0:
        return partial + unrealized
    entry = float(row.get("entry_price") or 0)
    close = float(row.get("close_price") or row.get("mark_price") or 0)
    size = float(row.get("size") or 0)
    side = str(row.get("side") or "long").lower()
    if entry > 0 and close > 0 and size > 0:
        return (close - entry) * size if side == "long" else (entry - close) * size
    return unrealized


def _load_closed(db_path: str) -> List[dict]:
    if not os.path.isfile(db_path):
        return []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM paper_positions WHERE status='closed' ORDER BY closed_at"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _load_fees(db_path: str) -> float:
    if not os.path.isfile(db_path):
        return 0.0
    conn = sqlite3.connect(db_path)
    try:
        for table in ("paper_orders", "orders"):
            try:
                row = conn.execute(
                    f"SELECT SUM(fee) FROM {table} WHERE status IN ('filled','closed')"
                ).fetchone()
                if row and row[0]:
                    return float(row[0])
            except sqlite3.Error:
                continue
    finally:
        conn.close()
    return 0.0


def replay_closed_positions(db_path: Optional[str] = None) -> ReplayMetrics:
    """对已平仓仓位做离线回放统计。"""
    path = db_path or _default_db()
    rows = _load_closed(path)
    metrics = ReplayMetrics()

    if not rows:
        return metrics

    pnls: List[float] = []
    equity_curve = [0.0]
    wins_pnl: List[float] = []
    losses_pnl: List[float] = []

    for row in rows:
        pnl = _resolve_pnl(row)
        pnls.append(pnl)
        equity_curve.append(equity_curve[-1] + pnl)
        if pnl > 0:
            wins_pnl.append(pnl)
        elif pnl < 0:
            losses_pnl.append(pnl)

        for field_name, bucket in (
            ("close_reason", metrics.by_close_reason),
            ("timeframe_tier", metrics.by_tier),
            ("trade_nature", metrics.by_nature),
        ):
            key = str(row.get(field_name) or "unknown")
            bucket[key] = bucket.get(key, 0.0) + pnl

    metrics.total_trades = len(pnls)
    metrics.wins = len(wins_pnl)
    metrics.win_rate = metrics.wins / metrics.total_trades if metrics.total_trades else 0
    metrics.total_pnl = sum(pnls)
    metrics.avg_win = sum(wins_pnl) / len(wins_pnl) if wins_pnl else 0
    metrics.avg_loss = sum(losses_pnl) / len(losses_pnl) if losses_pnl else 0
    gross_loss = abs(sum(losses_pnl))
    metrics.profit_factor = sum(wins_pnl) / gross_loss if gross_loss > 0 else float("inf")

    peak = equity_curve[0]
    max_dd = 0.0
    for eq in equity_curve:
        peak = max(peak, eq)
        max_dd = max(max_dd, peak - eq)
    metrics.max_drawdown = max_dd

    total_fees = _load_fees(path)
    gross = abs(metrics.total_pnl) + total_fees
    metrics.fee_ratio = total_fees / gross if gross > 0 else 0.0

    return metrics


def compare_replay_baseline(
    db_path: Optional[str] = None,
    *,
    short_tier_extra_gate: bool = True,
) -> Dict[str, Any]:
    """对比「全量」与「模拟 short 门槛过滤后」的表现差异。"""
    path = db_path or _default_db()
    rows = _load_closed(path)

    all_metrics = replay_closed_positions(path)

    # 模拟：若 short/scalp 交易置信度不足（用 tier 代理过滤亏损 nature）
    filtered_pnls: List[float] = []
    blocked = 0
    for row in rows:
        tier = str(row.get("timeframe_tier") or "mid").lower()
        nature = str(row.get("trade_nature") or "swing").lower()
        pnl = _resolve_pnl(row)
        if short_tier_extra_gate and (tier == "short" or nature in ("scalp", "intraday")):
            if pnl < 0:
                blocked += 1
                continue
        filtered_pnls.append(pnl)

    filtered = ReplayMetrics()
    if filtered_pnls:
        filtered.total_trades = len(filtered_pnls)
        filtered.wins = sum(1 for p in filtered_pnls if p > 0)
        filtered.win_rate = filtered.wins / filtered.total_trades
        filtered.total_pnl = sum(filtered_pnls)

    return {
        "baseline": all_metrics.to_dict(),
        "with_short_gate_simulation": filtered.to_dict(),
        "simulated_blocked_losing_short_trades": blocked,
        "pnl_improvement": round(filtered.total_pnl - all_metrics.total_pnl, 2),
    }


def _default_db() -> str:
    for p in ("data/alpha_arena.db", "../data/alpha_arena.db", "backend/data/alpha_arena.db"):
        if os.path.isfile(p):
            return p
    return "data/alpha_arena.db"


def save_replay_report(path: str, db_path: Optional[str] = None) -> str:
    report = compare_replay_baseline(db_path=db_path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return path


if __name__ == "__main__":
    import sys
    p = sys.argv[1] if len(sys.argv) > 1 else None
    print(json.dumps(compare_replay_baseline(p), ensure_ascii=False, indent=2))
