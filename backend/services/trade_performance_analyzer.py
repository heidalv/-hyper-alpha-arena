"""按平仓原因、tier、nature、symbol 统计盈亏来源。

用于 AI 策略升级的数据归因；支持 SQLite paper_positions 与 ORM 查询。
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class DimensionStats:
    key: str
    count: int
    wins: int
    total_pnl: float
    avg_pnl: float
    win_rate: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "count": self.count,
            "wins": self.wins,
            "total_pnl": round(self.total_pnl, 2),
            "avg_pnl": round(self.avg_pnl, 2),
            "win_rate": round(self.win_rate, 4),
        }


@dataclass
class TradePerformanceReport:
    total_closed: int = 0
    overall_win_rate: float = 0.0
    overall_pnl: float = 0.0
    by_close_reason: List[DimensionStats] = field(default_factory=list)
    by_tier: List[DimensionStats] = field(default_factory=list)
    by_nature: List[DimensionStats] = field(default_factory=list)
    by_symbol: List[DimensionStats] = field(default_factory=list)
    insights: List[str] = field(default_factory=list)
    generated_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_closed": self.total_closed,
            "overall_win_rate": round(self.overall_win_rate, 4),
            "overall_pnl": round(self.overall_pnl, 2),
            "by_close_reason": [d.to_dict() for d in self.by_close_reason],
            "by_tier": [d.to_dict() for d in self.by_tier],
            "by_nature": [d.to_dict() for d in self.by_nature],
            "by_symbol": [d.to_dict() for d in self.by_symbol],
            "insights": self.insights,
            "generated_at": self.generated_at,
        }


def _resolve_pnl(row: Dict[str, Any]) -> float:
    """从 paper_positions 行估算已实现盈亏。"""
    partial = float(row.get("partial_realized_pnl") or 0)
    unrealized = float(row.get("unrealized_pnl") or 0)
    if partial != 0:
        return partial + unrealized
    entry = float(row.get("entry_price") or 0)
    close = float(row.get("close_price") or row.get("mark_price") or 0)
    size = float(row.get("size") or 0)
    side = str(row.get("side") or "long").lower()
    if entry > 0 and close > 0 and size > 0:
        if side == "long":
            return (close - entry) * size
        return (entry - close) * size
    return unrealized


def _aggregate(rows: List[Dict[str, Any]], key_field: str) -> List[DimensionStats]:
    buckets: Dict[str, List[float]] = {}
    for row in rows:
        key = str(row.get(key_field) or "unknown").strip() or "unknown"
        pnl = _resolve_pnl(row)
        buckets.setdefault(key, []).append(pnl)

    stats: List[DimensionStats] = []
    for key, pnls in buckets.items():
        wins = sum(1 for p in pnls if p > 0)
        total = sum(pnls)
        cnt = len(pnls)
        stats.append(
            DimensionStats(
                key=key,
                count=cnt,
                wins=wins,
                total_pnl=total,
                avg_pnl=total / cnt if cnt else 0,
                win_rate=wins / cnt if cnt else 0,
            )
        )
    stats.sort(key=lambda s: s.total_pnl)
    return stats


def _load_closed_positions_sqlite(db_path: str) -> List[Dict[str, Any]]:
    if not os.path.isfile(db_path):
        logger.warning("[TradePerf] DB 不存在: %s", db_path)
        return []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(
            "SELECT * FROM paper_positions WHERE status='closed'"
        )
        return [dict(r) for r in cur.fetchall()]
    except sqlite3.Error as e:
        logger.error("[TradePerf] 查询失败: %s", e)
        return []
    finally:
        conn.close()


def _cutoff_naive(
    *,
    since_hours: Optional[int] = None,
    since_days: Optional[int] = None,
    since_at: Optional[datetime] = None,
) -> Optional[datetime]:
    if since_at is not None:
        return since_at.replace(tzinfo=None) if since_at.tzinfo else since_at
    if since_hours is not None and since_hours > 0:
        return datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=since_hours)
    if since_days is not None and since_days > 0:
        return datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=since_days)
    return None


def _load_closed_positions_orm(
    db=None,
    *,
    since_hours: Optional[int] = None,
    since_days: Optional[int] = None,
    since_at: Optional[datetime] = None,
    account_id: Optional[int] = None,
    exclude_rebate: bool = True,
) -> List[Dict[str, Any]]:
    from backend.database.models import PaperPosition

    q = db.query(PaperPosition).filter(PaperPosition.status == "closed")
    cutoff = _cutoff_naive(since_hours=since_hours, since_days=since_days, since_at=since_at)
    if cutoff is not None:
        q = q.filter(PaperPosition.closed_at >= cutoff)
    if account_id is not None:
        q = q.filter(PaperPosition.account_id == account_id)
    if exclude_rebate:
        q = q.filter(~PaperPosition.strategy_id.like("rebate_%"))
    rows = q.all()
    result = []
    for p in rows:
        result.append({
            "symbol": p.symbol,
            "side": p.side,
            "close_reason": p.close_reason,
            "timeframe_tier": p.timeframe_tier,
            "trade_nature": p.trade_nature,
            "strategy_id": getattr(p, "strategy_id", None),
            "account_id": getattr(p, "account_id", None),
            "entry_price": p.entry_price,
            "close_price": p.close_price,
            "mark_price": p.mark_price,
            "size": p.size,
            "partial_realized_pnl": getattr(p, "partial_realized_pnl", 0),
            "unrealized_pnl": p.unrealized_pnl,
            "leverage": p.leverage,
            "closed_at": p.closed_at,
        })
    return result


def _derive_insights(report: TradePerformanceReport) -> List[str]:
    insights: List[str] = []

    # close_reason 归因
    worst_reasons = [d for d in report.by_close_reason if d.total_pnl < 0]
    if worst_reasons:
        w = worst_reasons[0]
        insights.append(
            f"最大亏损来源 close_reason={w.key}: {w.count} 笔累计 {w.total_pnl:.0f} USDT"
        )
    best_reasons = [d for d in report.by_close_reason if d.total_pnl > 0]
    if best_reasons:
        b = best_reasons[-1]
        insights.append(
            f"最赚钱退出 close_reason={b.key}: 胜率 {b.win_rate:.0%}，累计 +{b.total_pnl:.0f}"
        )

    # tier / nature
    for label, dims in (("tier", report.by_tier), ("nature", report.by_nature)):
        losers = [d for d in dims if d.total_pnl < -50]
        for d in losers:
            insights.append(
                f"{label}={d.key} 累计亏损 {d.total_pnl:.0f}（{d.count} 笔，胜率 {d.win_rate:.0%}）"
            )
        winners = [d for d in dims if d.total_pnl > 100]
        for d in winners:
            insights.append(
                f"{label}={d.key} 累计盈利 +{d.total_pnl:.0f}（{d.count} 笔）— 应提高预算权重"
            )

    # symbol 集中度
    if report.by_symbol:
        worst_sym = report.by_symbol[0]
        if worst_sym.total_pnl < -500:
            insights.append(
                f"symbol={worst_sym.key} 拖累最大: {worst_sym.total_pnl:.0f}，建议单独风控"
            )

    # 策略门槛建议
    short_like = [d for d in report.by_tier if d.key == "short" and d.total_pnl < 0]
    intraday_like = [d for d in report.by_nature if d.key in ("intraday", "scalp") and d.total_pnl < 0]
    if short_like or intraday_like:
        insights.append(
            "建议：提高 short/intraday/scalp 开仓门槛 +8%，限制连续同向短线开仓"
        )

    sl_losses = [d for d in report.by_close_reason if d.key in ("sl", "stop_loss") and d.total_pnl < 0]
    if sl_losses:
        insights.append(
            f"止损出场累计 {sl_losses[0].total_pnl:.0f}：检查 SL 距离是否过紧或入场时机"
        )

    return insights


def analyze_closed_trades(
    *,
    db_path: Optional[str] = None,
    db=None,
    since_hours: Optional[int] = None,
    since_days: Optional[int] = None,
    since_at: Optional[datetime] = None,
    account_id: Optional[int] = None,
    exclude_rebate: bool = True,
) -> TradePerformanceReport:
    """分析已平仓交易，返回分层归因报告。"""
    if db is not None:
        rows = _load_closed_positions_orm(
            db,
            since_hours=since_hours,
            since_days=since_days,
            account_id=account_id,
            exclude_rebate=exclude_rebate,
        )
    else:
        path = db_path or _default_db_path()
        rows = _load_closed_positions_sqlite(path)
        cutoff = _cutoff_naive(since_hours=since_hours, since_days=since_days, since_at=since_at)
        if cutoff is not None:
            rows = [
                r for r in rows
                if r.get("closed_at") and r["closed_at"] >= cutoff
            ]

    report = TradePerformanceReport(
        total_closed=len(rows),
        generated_at=datetime.now(timezone.utc).isoformat(),
    )
    if not rows:
        report.insights.append("无已平仓数据，无法归因")
        return report

    pnls = [_resolve_pnl(r) for r in rows]
    wins = sum(1 for p in pnls if p > 0)
    report.overall_pnl = sum(pnls)
    report.overall_win_rate = wins / len(pnls) if pnls else 0

    report.by_close_reason = _aggregate(rows, "close_reason")
    report.by_tier = _aggregate(rows, "timeframe_tier")
    report.by_nature = _aggregate(rows, "trade_nature")
    report.by_symbol = _aggregate(rows, "symbol")

    report.insights = _derive_insights(report)
    return report


def _default_db_path() -> str:
    candidates = [
        os.environ.get("DATABASE_URL", "").replace("sqlite:///", ""),
        "data/alpha_arena.db",
        "../data/alpha_arena.db",
        "backend/data/alpha_arena.db",
    ]
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return "data/alpha_arena.db"


def save_report_json(report: TradePerformanceReport, path: str) -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report.to_dict(), f, ensure_ascii=False, indent=2)
    return path


def render_report_markdown(report: TradePerformanceReport) -> str:
    lines = [
        "# 交易盈亏归因报告",
        "",
        f"- 生成时间: {report.generated_at}",
        f"- 已平仓: {report.total_closed} 笔",
        f"- 总盈亏: {report.overall_pnl:+.2f} USDT",
        f"- 胜率: {report.overall_win_rate:.1%}",
        "",
        "## 关键洞察",
    ]
    for ins in report.insights:
        lines.append(f"- {ins}")
    lines.append("")

    def _table(title: str, dims: List[DimensionStats]) -> None:
        lines.append(f"## {title}")
        lines.append("| 维度 | 笔数 | 胜率 | 累计盈亏 | 均笔 |")
        lines.append("|---|---:|---:|---:|---:|")
        for d in dims:
            lines.append(
                f"| {d.key} | {d.count} | {d.win_rate:.0%} | {d.total_pnl:+.2f} | {d.avg_pnl:+.2f} |"
            )
        lines.append("")

    _table("按平仓原因", report.by_close_reason)
    _table("按 timeframe_tier", report.by_tier)
    _table("按 trade_nature", report.by_nature)
    _table("按 symbol（全部）", report.by_symbol)
    return "\n".join(lines)


if __name__ == "__main__":
    import sys

    path = sys.argv[1] if len(sys.argv) > 1 else None
    rpt = analyze_closed_trades(db_path=path)
    print(render_report_markdown(rpt))
