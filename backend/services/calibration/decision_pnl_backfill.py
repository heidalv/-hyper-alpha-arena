"""decision_pnl_backfill — S2-8 样本管道：paper 平仓盈亏 → ai_decision_logs 回填。

问题
====
`ai_decision_logs` 是 LLM 置信度校准器的样本源，但 outcome（`realized_pnl`）只有
Hyperliquid 实盘 sync 端点（arena_routes）能回填，且依赖钱包绑定，实际从未生效
（2026-08-05 实测：buy/sell 共 4766 条，realized_pnl 回填为 0）。校准器因此永远
拿不到样本，conf→胜率曲线无从拟合。

本模块补齐**系统内**样本管道：虚拟盘（paper）仓位在 paper_trading_engine 平仓时
把盈亏写入 `paper_positions.unrealized_pnl`（closed 状态复用），本服务按
(account_id, symbol, 开仓时间窗口) 把已平仓仓位的盈亏回填到对应的决策日志：

- 决策匹配：`operation IN (buy, sell)` + `executed == 'true'` + `realized_pnl IS NULL`，
  `decision_time` 在仓位 `opened_at` 前 `MATCH_WINDOW_MIN` 分钟内最近的一条。
- 幂等：只处理未回填决策；回填后 realized_pnl 非空，重跑自动跳过。
- 同仓位多决策（DCA 等）：只回填最近一条，避免重复归属。

用法
====
    from backend.services.calibration.decision_pnl_backfill import backfill_decision_pnl
    result = backfill_decision_pnl(lookback_days=90)   # 返回 {positions, candidates, matched, updated, skipped}

也可经 API `GET /api/analytics/ai-decision-calibration?backfill=true` 触发（S2-8）。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# 决策→执行→开仓的允许时间窗：决策时间早于开仓时间 N 分钟以上的不匹配。
MATCH_WINDOW_MIN = 15
# 单次最大处理决策数（防 OOM，超出部分下次再跑）。
MAX_DECISIONS = 10000


def _fetch_decisions(a_db, cutoff) -> List[Tuple[int, int, str, Optional[datetime]]]:
    """未结算的 buy/sell 决策：(id, account_id, symbol, decision_time)。"""
    from backend.database.models import AIDecisionLog

    try:
        rows = (
            a_db.query(
                AIDecisionLog.id,
                AIDecisionLog.account_id,
                AIDecisionLog.symbol,
                AIDecisionLog.decision_time,
            )
            .filter(
                AIDecisionLog.operation.in_(["buy", "sell"]),
                AIDecisionLog.executed == "true",
                AIDecisionLog.realized_pnl.is_(None),
                AIDecisionLog.decision_time >= cutoff,
            )
            .order_by(AIDecisionLog.decision_time.asc())
            .limit(MAX_DECISIONS)
            .all()
        )
        return [
            (int(r[0]), int(r[1]), (r[2] or "").upper(), r[3])
            for r in rows
            if r[2] and r[3] is not None
        ]
    except Exception as e:
        logger.warning(f"[PnlBackfill] 决策查询失败: {e}")
        try:
            a_db.rollback()
        except Exception:
            pass
        return []


def _fetch_positions(p_db, cutoff) -> List[Tuple[int, str, datetime, float]]:
    """已平仓仓位：(account_id, symbol, opened_at, realized_pnl)。"""
    from backend.database.models import PaperPosition

    try:
        rows = (
            p_db.query(
                PaperPosition.account_id,
                PaperPosition.symbol,
                PaperPosition.opened_at,
                PaperPosition.unrealized_pnl,
            )
            .filter(
                PaperPosition.status.in_(["closed", "liquidated"]),
                PaperPosition.closed_at >= cutoff,
                PaperPosition.opened_at.isnot(None),
            )
            .all()
        )
        return [
            (int(r[0]), (r[1] or "").upper(), r[2], float(r[3] or 0.0))
            for r in rows
            if r[1] and r[2] is not None
        ]
    except Exception as e:
        logger.warning(f"[PnlBackfill] 仓位查询失败: {e}")
        try:
            p_db.rollback()
        except Exception:
            pass
        return []


def _match(
    decisions: List[Tuple[int, int, str, datetime]],
    positions: List[Tuple[int, str, datetime, float]],
    match_window_min: int,
) -> List[Tuple[int, float]]:
    """按 (account, symbol) 分组 + 时间窗口匹配，返回 [(decision_id, pnl)]。

    决策须满足：opened_at - window <= decision_time <= opened_at（决策先于开仓，
    且在允许窗口内）。同一决策只归属一个仓位（匹配后从候选移除）；
    同一仓位只回填最近的一条决策。
    """
    # 决策按 (account, symbol) 分组、时间升序（_fetch 已升序，分组保持有序）
    by_key: Dict[Tuple[int, str], List[Tuple[datetime, int]]] = {}
    for d_id, acc, sym, d_dt in decisions:
        by_key.setdefault((acc, sym), []).append((d_dt, d_id))
    for lst in by_key.values():
        lst.sort(key=lambda x: x[0])

    window = timedelta(minutes=int(match_window_min))
    out: List[Tuple[int, float]] = []
    for acc, sym, opened_at, pnl in positions:
        lst = by_key.get((acc, sym))
        if not lst:
            continue
        lo = opened_at - window
        cand_idx: Optional[int] = None
        for i, (d_dt, _d_id) in enumerate(lst):
            if d_dt > opened_at:
                break
            if d_dt >= lo:
                cand_idx = i  # 升序遍历：最后一个满足的即最接近开仓时间
        if cand_idx is None:
            continue
        _d_dt, d_id = lst.pop(cand_idx)
        out.append((d_id, pnl))
    return out


def backfill_decision_pnl(
    lookback_days: int = 90,
    match_window_min: int = MATCH_WINDOW_MIN,
) -> Dict[str, object]:
    """把已平仓 paper 仓位的盈亏回填到未结算的 buy/sell 决策日志。

    Returns:
        {"positions": 已扫描平仓仓位数, "candidates": 未结算决策数,
         "matched": 命中数, "updated": 实际回填数, "skipped": 未命中数}
    """
    from backend.database.connection import AnalyticsSessionLocal, SessionLocal

    cutoff = datetime.now(timezone.utc) - timedelta(days=int(lookback_days))
    result: Dict[str, object] = {
        "positions": 0, "candidates": 0, "matched": 0,
        "updated": 0, "skipped": 0, "window_min": match_window_min,
    }

    a_db = AnalyticsSessionLocal()
    try:
        decisions = _fetch_decisions(a_db, cutoff)
    finally:
        a_db.close()
    result["candidates"] = len(decisions)
    if not decisions:
        return result

    p_db = SessionLocal()
    try:
        positions = _fetch_positions(p_db, cutoff)
    finally:
        p_db.close()
    result["positions"] = len(positions)
    if not positions:
        return result

    matched = _match(decisions, positions, match_window_min)
    result["matched"] = len(matched)
    result["skipped"] = len(positions) - len(matched)
    if not matched:
        return result

    a_db = AnalyticsSessionLocal()
    try:
        from backend.database.models import AIDecisionLog

        now = datetime.now(timezone.utc)
        upd = 0
        for d_id, pnl in matched:
            a_db.query(AIDecisionLog).filter(AIDecisionLog.id == d_id).update(
                {"realized_pnl": pnl, "pnl_updated_at": now},
                synchronize_session=False,
            )
            upd += 1
        a_db.commit()
        result["updated"] = upd
    except Exception as e:
        logger.warning(f"[PnlBackfill] 回填更新失败: {e}")
        try:
            a_db.rollback()
        except Exception:
            pass
    finally:
        a_db.close()
    return result


def run_backfill_once(lookback_days: int = 90) -> Dict[str, object]:
    """供定时/手动调用的入口（带日志）。"""
    res = backfill_decision_pnl(lookback_days=lookback_days)
    logger.info(
        f"[PnlBackfill] 完成: positions={res.get('positions')} "
        f"candidates={res.get('candidates')} matched={res.get('matched')} "
        f"updated={res.get('updated')} skipped={res.get('skipped')}"
    )
    return res
