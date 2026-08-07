"""RebateStrategyEvolver — 将历史绩效沉淀为人工确认提案。"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from backend.database.connection import SessionLocal, sqlite_write_commit
from backend.database.models import RebateEvolutionProposalDB
from backend.services.rebate_arb.rebate_backtest_runner import rebate_backtest_runner
from backend.services.rebate_arb.schema import ensure_rebate_schema


class RebateStrategyEvolver:
    def generate_proposals(self, strategies: List[str] | None = None) -> Dict[str, Any]:
        # M4: S1/S5 已下线，不再生成进化提案
        strategies = strategies or ["S2", "S3", "S4", "S6", "S7", "S8"]
        ensure_rebate_schema()
        proposals = []
        db = SessionLocal()
        try:
            for sid in strategies:
                result = rebate_backtest_runner.run(sid)
                if result["sample_count"] < 3:
                    continue
                if result["recommendation"] == "keep":
                    continue
                severity = "medium" if result["recommendation"] == "reduce_size" else "low"
                title = (
                    f"{sid} 胜率偏低，建议 Paper 降仓验证"
                    if result["recommendation"] == "reduce_size"
                    else f"{sid} 表现较好，可 Paper 小幅加仓"
                )
                row = RebateEvolutionProposalDB(
                    source="evolution",
                    strategy_type=sid,
                    severity=severity,
                    title=title,
                    proposal_json=json.dumps(result["proposal"], ensure_ascii=False, default=str),
                    status="pending",
                    requires_paper_validation=True,
                    requires_manual_live_confirm=True,
                )
                db.add(row)
                db.flush()
                proposals.append({
                    "id": row.id,
                    "source": row.source,
                    "strategy_type": sid,
                    "severity": severity,
                    "title": title,
                    "change": result["proposal"],
                    "requires_manual_live_confirm": True,
                })
            sqlite_write_commit(db, label="rebate_evolver_generate")
            return {"success": True, "count": len(proposals), "proposals": proposals}
        except Exception as e:
            db.rollback()
            return {"success": False, "error": str(e), "count": 0, "proposals": []}
        finally:
            db.close()


rebate_strategy_evolver = RebateStrategyEvolver()
