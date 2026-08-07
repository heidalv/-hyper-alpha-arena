"""Assemble defensive_cycle.py from extracted bodies."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def load(name: str) -> str:
    return (ROOT / f"backend/services/full_auto/_defensive_{name}_body.tmp").read_text(encoding="utf-8")

def dedent(body: str) -> str:
    return "\n".join("    " + line[8:] if line.startswith("        ") else line for line in body.splitlines())

header = '''"""防守模式 — 从 monolith _execute_defensive_* 迁出（整改#8 Phase2）。"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


@dataclass
class DefensiveHost:
    tier_protection: Dict[str, Any]
    default_protection: Dict[str, Any]
    get_trading_account_id: Callable = field(repr=False, default=lambda *a, **k: 0)
    append_event: Callable = field(repr=False, default=lambda *a, **k: None)


def build_defensive_host(svc) -> DefensiveHost:
    from backend.services.full_auto_trading_service import FullAutoTradingService
    return DefensiveHost(
        tier_protection=svc.TIER_PROTECTION,
        default_protection=FullAutoTradingService.DEFAULT_PROTECTION,
        get_trading_account_id=svc._get_trading_account_id,
        append_event=svc._append_event,
    )


def run_defensive_analysis(
    db: Session,
    session,
    market_summary: dict,
    host: DefensiveHost,
) -> None:
'''

verdicts_header = '''

def run_defensive_verdicts(
    db: Session,
    session,
    account_id: int,
    verdicts: list,
    positions_list: list,
    host: DefensiveHost,
) -> None:
'''

rule_header = '''

def run_rule_based_defensive(
    db: Session,
    session,
    positions_list: list,
    market_summary: dict,
    host: DefensiveHost,
) -> None:
'''

content = (
    header + dedent(load("execute_defensive_analysis")) + "\n"
    + verdicts_header + dedent(load("execute_defensive_verdicts")) + "\n"
    + rule_header + dedent(load("rule_based_defensive")) + "\n"
)
out = ROOT / "backend/services/full_auto/defensive_cycle.py"
out.write_text(content, encoding="utf-8")
print(f"wrote {out} ({content.count(chr(10))} lines)")
