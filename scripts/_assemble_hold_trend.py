"""Assemble hold_timeout_trend_review.py."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FA = ROOT / "backend/services/full_auto"


def dedent(body: str) -> str:
    return "\n".join(
        ("    " + line[8:] if line.startswith("        ") else line)
        for line in body.splitlines()
    )


header = '''"""持仓时限 AI 复审 + TrendAgent 复查 — 从 monolith 迁出（整改#8 Phase2）。"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


@dataclass
class HoldTrendReviewHost:
    active_db_sessions: Dict[str, Any]
    last_hold_timeout_ai_review: Dict[str, float]
    last_analyst_reports: Dict[str, Any] = field(default_factory=dict)
    last_unified_snapshot: Any = None
    TIER_PROTECTION: Dict[str, Any] = field(default_factory=dict)

    get_trading_account_id: Callable = field(repr=False, default=lambda *a, **k: 0)
    append_event: Callable = field(repr=False, default=lambda *a, **k: None)
    active_exchange: Callable = field(repr=False, default=lambda: "binance")
    run_analyst_system: Callable = field(repr=False, default=lambda *a, **k: None)
    get_account_risk_score: Callable = field(repr=False, default=lambda *a, **k: 0.0)
    clear_hold_timeout_queue_entry: Callable = field(repr=False, default=lambda *a, **k: None)
    orch_payload_from_decision: Callable = field(repr=False, default=lambda *a, **k: {})
    safe_commit: Callable = field(repr=False, default=lambda *a, **k: True)


def build_hold_trend_review_host(svc) -> HoldTrendReviewHost:
    return HoldTrendReviewHost(
        active_db_sessions=svc._active_db_sessions,
        last_hold_timeout_ai_review=svc._last_hold_timeout_ai_review,
        last_analyst_reports=getattr(svc, "_last_analyst_reports", None) or {},
        last_unified_snapshot=getattr(svc, "_last_unified_snapshot", None),
        TIER_PROTECTION=getattr(svc, "TIER_PROTECTION", {}) or {},
        get_trading_account_id=svc._get_trading_account_id,
        append_event=svc._append_event,
        active_exchange=svc._active_exchange,
        run_analyst_system=svc._run_analyst_system,
        get_account_risk_score=svc._get_account_risk_score,
        clear_hold_timeout_queue_entry=svc._clear_hold_timeout_queue_entry,
        orch_payload_from_decision=svc._orch_payload_from_decision,
        safe_commit=svc._safe_commit,
    )


def run_hold_timeout_ai_review_if_needed(
    session_id: str,
    host: HoldTrendReviewHost,
    *,
    priority_expired: bool = False,
) -> None:
'''

if_body = dedent((FA / "_hold_if_body.tmp").read_text(encoding="utf-8"))
rev_header = '''

def run_hold_timeout_ai_review(
    db: Session,
    session,
    pending: list,
    host: HoldTrendReviewHost,
) -> None:
'''
rev_body = dedent((FA / "_hold_rev_body.tmp").read_text(encoding="utf-8"))
trend_header = '''

def run_trend_review(
    db,
    session,
    account_id,
    market_summary,
    host: HoldTrendReviewHost,
) -> None:
'''
trend_body = dedent((FA / "_trend_rev_body.tmp").read_text(encoding="utf-8"))

(FA / "hold_timeout_trend_review.py").write_text(
    header + if_body + rev_header + rev_body + trend_header + trend_body + "\n",
    encoding="utf-8",
)
print("assembled hold_timeout_trend_review.py")
