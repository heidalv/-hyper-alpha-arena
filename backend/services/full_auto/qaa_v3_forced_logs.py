"""QAA v3 超时降级决策日志 — 从 monolith 迁出（整改#8 Phase2）。"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def write_qaa_v3_forced_decision_logs(
    *,
    session_orm_id: int,
    account_id: int,
    decisions: list,
    balance_info: dict,
    positions_list: list,
    market_summary: dict,
) -> None:
    if not decisions:
        return
    from decimal import Decimal
    from backend.database.connection import AnalyticsSessionLocal
    from backend.database.models import AIDecisionLog, DecisionSnapshot

    total_assets = float((balance_info or {}).get("total_equity", 0) or 10000)
    written = 0
    db = AnalyticsSessionLocal()
    try:
        for dec in decisions:
            sym = str(dec.get("symbol") or "").upper()
            action = str(dec.get("action") or "hold").lower()
            if not sym or action not in ("buy", "sell", "hold", "close", "reduce"):
                continue
            confidence = float(dec.get("confidence", 0) or 0)
            reasoning = str(dec.get("reasoning") or "QAA v3 超时降级决策")[:4000]
            prev_portion = 0.0
            for pos in positions_list or []:
                if str(pos.get("symbol") or "").upper() == sym:
                    val = float(pos.get("value", 0) or pos.get("notional", 0) or 0)
                    prev_portion = val / total_assets if total_assets > 0 else 0.0
                    break

            mkt = (market_summary or {}).get(sym, {}) if isinstance(market_summary, dict) else {}
            db.add(DecisionSnapshot(
                session_id=session_orm_id,
                strategy_id=str(dec.get("strategy_id") or "") or None,
                symbol=sym,
                tier=dec.get("timeframe_tier") or dec.get("tier") or "mid",
                market_snapshot_json=mkt if isinstance(mkt, dict) else {},
                ai_reasoning=reasoning,
                action=action,
                direction="buy" if action == "buy" else ("sell" if action == "sell" else action),
                confidence=confidence,
                regime_at_decision=mkt.get("market_cycle") if isinstance(mkt, dict) else None,
                volatility_at_decision=float(mkt.get("volatility_value", 0) or 0) if isinstance(mkt, dict) else None,
            ))
            db.add(AIDecisionLog(
                account_id=account_id,
                reason=reasoning[:1000],
                operation=action,
                symbol=sym,
                prev_portion=Decimal(str(round(prev_portion, 6))),
                target_portion=Decimal(str(round(float(dec.get("position_pct", 0) or 0), 6))),
                total_balance=Decimal(str(round(total_assets, 2))),
                executed="false",
                reasoning_snapshot=reasoning,
                decision_snapshot=reasoning[:2000],
                ai_strategy_id=dec.get("strategy_id"),
                decision_source="qaa_timeout_fallback",
            ))
            written += 1
        db.commit()
        logger.warning(f"[FullAuto][QAA v3] 兜底写入 AI 决策日志/快照: {written} 条")
    except Exception as exc:
        db.rollback()
        logger.error(f"[FullAuto][QAA v3] 兜底写入 AI 决策日志失败: {exc}", exc_info=True)
    finally:
        db.close()
