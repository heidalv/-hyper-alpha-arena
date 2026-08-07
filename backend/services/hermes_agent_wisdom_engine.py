"""Hermes Agent 决策智慧 — Swing/Trend 平仓后采集，供后续 prompt 注入。"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _agent_type_from_nature(nature: str) -> Optional[str]:
    n = (nature or "").lower()
    if n in ("swing", "intraday"):
        return "swing"
    if n in ("trend_follow", "position", "midlong"):
        return "trend"
    return None


class HermesAgentWisdomEngine:
    """从 TradeOutcome 提取 Agent 决策模式并写入 Hermes DB。"""

    def extract_wisdom_from_outcome(self, outcome) -> bool:
        nature = getattr(outcome, "trade_nature", "") or ""
        agent_type = _agent_type_from_nature(nature)
        if not agent_type:
            meta = getattr(outcome, "metadata", None) or {}
            if meta.get("thesis_id") or meta.get("mlto_thesis_id"):
                agent_type = "trend"
            else:
                return False

        pnl = float(getattr(outcome, "pnl", 0) or 0)
        pnl_pct = float(getattr(outcome, "pnl_pct", 0) or 0)
        if pnl > 0:
            label = "win"
        elif pnl < 0:
            label = "loss"
        else:
            label = "breakeven"

        meta = getattr(outcome, "metadata", None) or {}
        regime = getattr(outcome, "regime_at_entry", "") or ""
        side = getattr(outcome, "side", "") or ""
        thesis_id = (
            meta.get("thesis_id")
            or meta.get("mlto_thesis_id")
            or (meta.get("agent_envelope") or {}).get("thesis_id")
            or ""
        )
        pattern_key = f"{regime}_{side}_{nature}".strip("_")
        if thesis_id:
            pattern_key = f"{pattern_key}:thesis_{str(thesis_id)[:12]}"

        context = {
            "thesis_id": thesis_id,
            "regime_at_entry": regime,
            "regime_at_exit": getattr(outcome, "regime_at_exit", ""),
            "fingerprint_at_entry": getattr(outcome, "fingerprint_at_entry", None),
            "agent_evidence": meta.get("agent_evidence"),
            "agent_envelope": meta.get("agent_envelope"),
            "cited_facts": meta.get("cited_fact_ids") or meta.get("cited_facts"),
            "alignment_score": meta.get("alignment_score"),
            "duration_seconds": getattr(outcome, "duration_seconds", 0),
        }

        try:
            from backend.services.hermes_db import hermes_execute
            hermes_execute(
                """INSERT INTO agent_decision_wisdom
                   (agent_type, trade_id, symbol, side, regime, close_reason,
                    decision_action, confidence, pnl, pnl_pct, outcome,
                    pattern_key, context_snapshot, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    agent_type,
                    meta.get("paper_position_id"),
                    (getattr(outcome, "symbol", "") or "").upper(),
                    side,
                    regime,
                    str(meta.get("close_reason") or ""),
                    side,
                    float(getattr(outcome, "confidence", 0) or 0),
                    pnl,
                    pnl_pct,
                    label,
                    pattern_key,
                    json.dumps(context, ensure_ascii=False, default=str),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            logger.info(
                "[HermesAgentWisdom] %s %s %s pnl=%+.2f outcome=%s",
                agent_type, outcome.symbol, pattern_key, pnl, label,
            )
            return True
        except Exception as exc:
            logger.debug(f"[HermesAgentWisdom] 采集失败: {exc}")
            return False


agent_wisdom = HermesAgentWisdomEngine()


def build_agent_wisdom_context(agent_type: str, *, limit: int = 10) -> str:
    """为 Hermes L2 优化提供 Agent 决策智慧摘要。"""
    try:
        from backend.services.hermes_db import hermes_fetchall

        rows = hermes_fetchall(
            """SELECT symbol, side, regime, outcome, pnl, pnl_pct, pattern_key, close_reason
               FROM agent_decision_wisdom
               WHERE agent_type=?
               ORDER BY id DESC LIMIT ?""",
            (agent_type, int(limit)),
        )
        if not rows:
            return f"（暂无 {agent_type} Agent 决策智慧记录）"

        lines = [f"## {agent_type} Agent 近期决策智慧（{len(rows)} 条）"]
        for r in rows:
            lines.append(
                f"- {r.get('symbol')} {r.get('side')} regime={r.get('regime')} "
                f"→ {r.get('outcome')} pnl={float(r.get('pnl') or 0):+.2f} "
                f"({r.get('pattern_key')}, {r.get('close_reason')})"
            )
        return "\n".join(lines)
    except Exception as exc:
        logger.debug("[HermesAgentWisdom] build_context 失败: %s", exc)
        return f"（{agent_type} Agent 智慧加载失败）"
