"""Assemble proposal_execution.py."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FA = ROOT / "backend/services/full_auto"


def dedent(body: str) -> str:
    return "\n".join("    " + line[8:] if line.startswith("        ") else line for line in body.splitlines())


header = '''"""Proposal 评估与执行 — 从 monolith _evaluate_and_execute_proposal 迁出（整改#8 Phase2）。"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Optional, Tuple

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


@dataclass
class ProposalExecutionHost:
    midlong_persistence_allow: Callable = field(repr=False, default=lambda *a, **k: True)
    resolve_independent_strategy: Callable = field(repr=False, default=lambda *a, **k: None)
    session_trading_mode: Callable = field(repr=False, default=lambda *a, **k: "paper")
    persist_tcp_snapshot: Callable = field(repr=False, default=lambda *a, **k: None)
    build_portfolio_for_agents: Callable = field(repr=False, default=lambda *a, **k: {})
    decision_price_consistency_ok: Callable = field(repr=False, default=lambda *a, **k: (True, ""))
    append_event: Callable = field(repr=False, default=lambda *a, **k: None)
    live_constitutional_pre_trade_check: Callable = field(repr=False, default=lambda *a, **k: (True, ""))
    execute_live_trade: Callable = field(repr=False, default=lambda *a, **k: None)
    safe_commit: Callable = field(repr=False, default=lambda *a, **k: True)
    execute_paper_trade: Callable = field(repr=False, default=lambda *a, **k: False)
    record_midlong_factor_snapshots: Callable = field(repr=False, default=lambda *a, **k: None)


def build_proposal_execution_host(svc) -> ProposalExecutionHost:
    return ProposalExecutionHost(
        midlong_persistence_allow=svc._midlong_persistence_allow,
        resolve_independent_strategy=svc._resolve_independent_strategy,
        session_trading_mode=svc._session_trading_mode,
        persist_tcp_snapshot=svc._persist_tcp_snapshot,
        build_portfolio_for_agents=svc._build_portfolio_for_agents,
        decision_price_consistency_ok=svc._decision_price_consistency_ok,
        append_event=svc._append_event,
        live_constitutional_pre_trade_check=svc._live_constitutional_pre_trade_check,
        execute_live_trade=svc._execute_live_trade,
        safe_commit=svc._safe_commit,
        execute_paper_trade=svc._execute_paper_trade,
        record_midlong_factor_snapshots=svc._record_midlong_factor_snapshots,
    )


def evaluate_and_execute_proposal(
    *,
    db: Session,
    session,
    proposal,
    market_summary: dict,
    host: ProposalExecutionHost,
    session_mode: str = "running",
    strat=None,
) -> bool:
'''

# fix missing field import
header = header.replace("from dataclasses import dataclass", "from dataclasses import dataclass, field")

body = dedent((FA / "_proposal_exec_body.tmp").read_text(encoding="utf-8"))
(FA / "proposal_execution.py").write_text(header + body + "\n", encoding="utf-8")
print("assembled proposal_execution.py")
