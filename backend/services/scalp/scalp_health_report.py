"""ScalpHealthReport — 短线转正验收 & 健康度指标（阶段一 1.6 / 阶段三 3.3）。

一处集中计算"改造是否见效"的关键指标，供：
- 阶段一验收（p1-verify）：胜率、净期望、笔数是否达标；
- 阶段三可观测性（p3-observability）：前端短线因子健康视图。

指标口径
========
- 滚动胜率 / 笔数：最近 N 天 `paper_positions` 中 `trade_nature='scalp'` 的已平仓单。
- 净期望（每笔）：按名义口径的价格变动收益率
  `side_sign × (close_price − entry_price) / entry_price` 的均值（与 EV 闸门同口径）。
- EV 闸门放行率：来自 `scalp_ev_gate.get_stats()`（进程内计数）。
- 校准器状态：来自 `scalp_confidence_calibrator.get_stats()`。

验收目标（默认）：胜率 ≥ 48%、净期望 > 0、笔数较改造前明显下降。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# 验收阈值（可在调用处覆盖）
TARGET_WIN_RATE = 0.48


def _trade_stats(db, lookback_days: int, account_id: Optional[int]) -> Dict[str, Any]:
    """从 paper_positions 统计已平仓 scalp 单的胜率/笔数/净期望。"""
    from sqlalchemy import text

    params: Dict[str, Any] = {"days": lookback_days}
    acct_clause = ""
    if account_id is not None:
        acct_clause = " AND account_id = :acct "
        params["acct"] = account_id

    sql = text(
        f"""
        SELECT side, entry_price, close_price
        FROM paper_positions
        WHERE status = 'closed'
          AND trade_nature = 'scalp'
          AND closed_at >= NOW() - (:days || ' days')::interval
          {acct_clause}
        """
    )
    rows = db.execute(sql, params).fetchall()

    returns: List[float] = []
    for side, entry, close in rows:
        try:
            e = float(entry or 0.0)
            c = float(close or 0.0)
            if e <= 0 or c <= 0:
                continue
            sign = 1.0 if str(side).lower() in ("long", "buy") else -1.0
            returns.append(sign * (c - e) / e)
        except (TypeError, ValueError):
            continue

    n = len(returns)
    if n == 0:
        return {
            "trade_count": 0,
            "win_rate": None,
            "avg_return_pct": None,
            "avg_win_pct": None,
            "avg_loss_pct": None,
        }

    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r <= 0]
    return {
        "trade_count": n,
        "win_rate": round(len(wins) / n, 4),
        "avg_return_pct": round(sum(returns) / n, 6),
        "avg_win_pct": round(sum(wins) / len(wins), 6) if wins else 0.0,
        "avg_loss_pct": round(sum(losses) / len(losses), 6) if losses else 0.0,
    }


def build_scalp_health(
    lookback_days: int = 14,
    account_id: Optional[int] = None,
    target_win_rate: float = TARGET_WIN_RATE,
) -> Dict[str, Any]:
    """汇总短线健康度/验收指标。

    Args:
        lookback_days: 回看天数（验收默认 14 天）
        account_id: 限定账户（None=全部）
        target_win_rate: 胜率验收目标

    Returns:
        指标字典（含验收判定 pass/fail）
    """
    report: Dict[str, Any] = {
        "lookback_days": lookback_days,
        "account_id": account_id,
    }

    # 1) 成交统计
    try:
        from backend.database.connection import SessionLocal
        db = SessionLocal()
        try:
            report["trades"] = _trade_stats(db, lookback_days, account_id)
        finally:
            db.close()
    except Exception as e:
        logger.debug(f"[ScalpHealth] 成交统计失败: {e}")
        report["trades"] = {"error": str(e)}

    # 2) EV 闸门放行率
    try:
        from backend.services.scalp.scalp_ev_gate import scalp_ev_gate
        report["ev_gate"] = scalp_ev_gate.get_stats()
    except Exception as e:
        report["ev_gate"] = {"error": str(e)}

    # 3) 校准器状态
    try:
        from backend.services.scalp.scalp_confidence_calibrator import (
            scalp_confidence_calibrator,
        )
        report["calibrator"] = scalp_confidence_calibrator.get_stats()
    except Exception as e:
        report["calibrator"] = {"error": str(e)}

    # 4) 验收判定
    trades = report.get("trades") or {}
    wr = trades.get("win_rate")
    exp = trades.get("avg_return_pct")
    acceptance = {
        "target_win_rate": target_win_rate,
        "win_rate_ok": bool(wr is not None and wr >= target_win_rate),
        "expectancy_positive": bool(exp is not None and exp > 0),
    }
    acceptance["passed"] = bool(
        acceptance["win_rate_ok"] and acceptance["expectancy_positive"]
    )
    report["acceptance"] = acceptance
    return report
