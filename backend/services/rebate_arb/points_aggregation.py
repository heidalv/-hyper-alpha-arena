"""积分 / PnL 汇总 — 单一口径，排除资金协调器 CAP 状态行。"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

from backend.services.rebate_arb.rule_registry import STAGE6_POINT_MODEL

CAP_STATE_POSITION_ID = "__capital_allocation_state__"


def point_usd_rate(_exchange: Optional[str] = None) -> float:
    """Stage6 积分估值：usd_per_point × speculative_discount（默认 0.01×0.5=0.005）。"""
    val = STAGE6_POINT_MODEL.get("point_valuation") or {}
    usd = float(val.get("usd_per_point_estimate") or 0.01)
    disc = float(val.get("speculative_discount") or 0.5)
    return usd * disc


def points_to_usd(points: float, exchange: Optional[str] = None) -> float:
    return float(points or 0) * point_usd_rate(exchange)


def is_trade_performance_log(row: Any) -> bool:
    """排除 capital_coordinator 复用的 CAP 状态行（total_pnl 实为 total_equity）。"""
    pid = str(getattr(row, "position_id", "") or "")
    if pid == CAP_STATE_POSITION_ID:
        return False
    st = str(getattr(row, "strategy_type", "") or "").upper()
    return st != "CAP"


def _exchange_for_log(row: Any, pos_by_id: Dict[str, Any]) -> str:
    ex = getattr(row, "source_exchange", None)
    if ex:
        return str(ex).lower()
    pid = str(getattr(row, "position_id", "") or "")
    pos = pos_by_id.get(pid)
    if pos is not None:
        return str(getattr(pos, "source_exchange", None) or "unknown").lower()
    return "unknown"


def dedupe_performance_logs(logs: Iterable[Any]) -> List[Any]:
    """同一 position_id 只保留最新一条结算日志，避免重复计分。"""
    best: Dict[str, Any] = {}
    for row in logs:
        if not is_trade_performance_log(row):
            continue
        pid = str(getattr(row, "position_id", "") or "")
        if not pid:
            continue
        prev = best.get(pid)
        row_id = int(getattr(row, "id", 0) or 0)
        prev_id = int(getattr(prev, "id", 0) or 0) if prev is not None else -1
        if prev is None or row_id >= prev_id:
            best[pid] = row
    return list(best.values())


def aggregate_points_and_pnl(
    active_positions: Iterable[Any],
    performance_logs: Iterable[Any],
    *,
    pos_lookup: Optional[Iterable[Any]] = None,
    paper_only: bool = False,
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]], float, float]:
    """
    汇总各交易所 / 各策略的积分与 PnL。

    口径：活跃仓 accumulated_points + 已平仓 performance log（不含 CAP 行）。
    返回 (exchange_stats, strategy_stats, total_points, total_pnl)。
    """
    pos_by_id: Dict[str, Any] = {}
    if pos_lookup is not None:
        for p in pos_lookup:
            pid = str(getattr(p, "position_id", "") or "")
            if pid:
                pos_by_id[pid] = p

    exchange_stats: Dict[str, Dict[str, Any]] = {}
    strategy_stats: Dict[str, Dict[str, Any]] = {}

    def _ex_slot(ex: str) -> Dict[str, Any]:
        return exchange_stats.setdefault(
            ex or "unknown",
            {"points_earned": 0.0, "pnl": 0.0, "position_count": 0},
        )

    def _st_slot(sid: str) -> Dict[str, Any]:
        return strategy_stats.setdefault(
            sid or "unknown",
            {"points_earned": 0.0, "pnl": 0.0, "position_count": 0},
        )

    settled_ids: set = set()
    deduped_logs = dedupe_performance_logs(performance_logs)
    for row in deduped_logs:
        settled_ids.add(str(getattr(row, "position_id", "") or ""))

    for pos in active_positions:
        if paper_only and not getattr(pos, "paper_mode", True):
            continue
        pid = str(getattr(pos, "position_id", "") or "")
        if pid and pid in settled_ids:
            continue
        ex = str(getattr(pos, "source_exchange", None) or "unknown").lower()
        pts = float(getattr(pos, "accumulated_points", 0) or 0)
        pnl = float(getattr(pos, "current_pnl", 0) or 0)
        sid = str(getattr(pos, "strategy_type", None) or "unknown")

        slot = _ex_slot(ex)
        slot["points_earned"] += pts
        slot["pnl"] += pnl
        slot["position_count"] += 1

        st = _st_slot(sid)
        st["points_earned"] += pts
        st["pnl"] += pnl
        st["position_count"] += 1

    for log in deduped_logs:
        ex = _exchange_for_log(log, pos_by_id)
        pts = float(getattr(log, "total_points", 0) or 0)
        pnl = float(getattr(log, "total_pnl", 0) or 0)
        sid = str(getattr(log, "strategy_type", None) or "unknown")

        slot = _ex_slot(ex)
        slot["points_earned"] += pts
        slot["pnl"] += pnl

        st = _st_slot(sid)
        st["points_earned"] += pts
        st["pnl"] += pnl

    total_points = sum(v["points_earned"] for v in exchange_stats.values())
    total_pnl = sum(v["pnl"] for v in exchange_stats.values())
    return exchange_stats, strategy_stats, total_points, total_pnl


def build_db_performance_summary(
    active_positions: Iterable[Any],
    performance_logs: Iterable[Any],
    *,
    pos_lookup: Optional[Iterable[Any]] = None,
) -> Dict[str, Any]:
    """从 DB 构建与 /analytics 兼容的绩效摘要（重启后仍准确）。"""
    trade_logs = dedupe_performance_logs(performance_logs)
    _, strategy_stats, total_points, total_pnl = aggregate_points_and_pnl(
        active_positions,
        performance_logs,
        pos_lookup=pos_lookup,
    )

    total_rebate = sum(float(getattr(r, "total_rebate", 0) or 0) for r in trade_logs)
    wins = sum(
        1 for r in trade_logs
        if float(r.total_pnl or 0) + float(r.total_rebate or 0) > 0
    )
    closed_count = len(trade_logs)

    by_strategy: Dict[str, Dict[str, float]] = {}
    for row in trade_logs:
        sid = str(getattr(row, "strategy_type", None) or "unknown")
        bucket = by_strategy.setdefault(sid, {"count": 0, "pnl": 0.0, "rebate": 0.0, "points": 0.0})
        bucket["count"] += 1
        bucket["pnl"] += float(row.total_pnl or 0)
        bucket["rebate"] += float(row.total_rebate or 0)
        bucket["points"] += float(row.total_points or 0)

    active_pts = sum(float(getattr(p, "accumulated_points", 0) or 0) for p in active_positions)
    active_pnl = sum(float(getattr(p, "current_pnl", 0) or 0) for p in active_positions)

    return {
        "total_trades": closed_count,
        "closed_trades": closed_count,
        "active_positions_with_points": len(list(active_positions)),
        "win_rate": wins / max(closed_count, 1),
        "total_pnl": round(total_pnl, 4),
        "total_rebate": round(total_rebate, 4),
        "total_points": round(total_points, 2),
        "active_unrealized_pnl": round(active_pnl, 4),
        "active_accrued_points": round(active_pts, 2),
        "net_pnl": round(total_pnl + total_rebate, 4),
        "by_strategy": by_strategy,
        "source": "database",
    }


def build_exchange_points_payload(
    exchange_stats: Dict[str, Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """将 exchange_stats 转为 API 响应字段。"""
    out: Dict[str, Dict[str, Any]] = {}
    for ex, stats in exchange_stats.items():
        pts = float(stats.get("points_earned") or 0)
        out[ex] = {
            "points_earned_total": round(pts, 2),
            "estimated_value_usd": round(points_to_usd(pts, ex), 4),
            "pnl_from_positions": round(float(stats.get("pnl") or 0), 4),
            "position_count": int(stats.get("position_count") or 0),
            "risk_status": "healthy",
            "point_usd_rate": point_usd_rate(ex),
        }
    return out
