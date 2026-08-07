"""Assemble strategy_maintenance.py."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def dedent(body: str) -> str:
    return "\n".join("    " + line[8:] if line.startswith("        ") else line for line in body.splitlines())

header = '''"""策略维护 — cleanup_stale / merge_duplicate 迁出（整改#8 Phase2）。"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


@dataclass
class StrategyMaintenanceHost:
    safe_commit: Callable = field(repr=False, default=lambda *a, **k: True)
    get_trading_account_id: Callable = field(repr=False, default=lambda *a, **k: 0)
    clear_master_strat_cache: Callable = field(repr=False, default=lambda: None)


def build_strategy_maintenance_host(svc) -> StrategyMaintenanceHost:
    return StrategyMaintenanceHost(
        safe_commit=svc._safe_commit,
        get_trading_account_id=svc._get_trading_account_id,
        clear_master_strat_cache=svc._clear_master_strat_cache,
    )


def cleanup_stale_strategies(db: Session, host: StrategyMaintenanceHost) -> dict:
'''

merge_header = '''

def merge_duplicate_strategies(db: Session, session_id: str, host: StrategyMaintenanceHost) -> dict:
'''

cleanup = (ROOT / "backend/services/full_auto/_cleanup_stale_body.tmp").read_text(encoding="utf-8")
merge = (ROOT / "backend/services/full_auto/_merge_dup_body.tmp").read_text(encoding="utf-8")
content = header + dedent(cleanup) + "\n" + merge_header + dedent(merge) + "\n"
out = ROOT / "backend/services/full_auto/strategy_maintenance.py"
out.write_text(content, encoding="utf-8")
print(f"wrote {out} ({content.count(chr(10))} lines)")
