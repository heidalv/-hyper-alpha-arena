"""Assemble analyst_system_cycle.py from extracted bodies."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
wrapper_body = (ROOT / "backend/services/full_auto/_analyst_wrapper_body.tmp").read_text(encoding="utf-8")
unified_body = (ROOT / "backend/services/full_auto/_analyst_unified_body.tmp").read_text(encoding="utf-8")

header = '''"""分析师系统 — 从 monolith _run_analyst_system* 迁出（整改#8 Phase2）。"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Set

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


@dataclass
class AnalystSystemHost:
    market_scan_cache: Dict[str, Any]
    long_tier_staged_tp_state: Dict[str, Any]
    tick_symbol_subset: Dict[str, Set[str]]
    pre_screen_results: Any = None
    pre_screen_passed: Set[str] = field(default_factory=set)
    mlto_handled_keys: Set[str] = field(default_factory=set)

    clear_master_strat_cache: Callable = field(repr=False, default=lambda: None)
    get_trading_account_id: Callable = field(repr=False, default=lambda *a, **k: 0)
    sync_hold_timeout_alerts: Callable = field(repr=False, default=lambda *a, **k: None)
    append_event: Callable = field(repr=False, default=lambda *a, **k: None)
    annotate_auto_coin_meta: Callable = field(repr=False, default=lambda *a, **k: None)
    build_fast_stability_result: Callable = field(repr=False, default=lambda *a, **k: {})
    run_with_timeout: Callable = field(repr=False, default=lambda *a, **k: None)
    record_ai_failure: Callable = field(repr=False, default=lambda *a, **k: None)
    record_ai_success: Callable = field(repr=False, default=lambda *a, **k: None)
    validate_ai_decisions: Callable = field(repr=False, default=lambda *a, **k: {})
    inject_orch_scheduled_stubs: Callable = field(repr=False, default=lambda *a, **k: [])
    execute_master_decisions: Callable = field(repr=False, default=lambda *a, **k: None)
    maintain_mlto_theses_for_session: Callable = field(repr=False, default=lambda *a, **k: None)
    execute_ai_decisions: Callable = field(repr=False, default=lambda *a, **k: None)
    execute_defensive_analysis: Callable = field(repr=False, default=lambda *a, **k: None)


def build_analyst_system_host(svc) -> AnalystSystemHost:
    return AnalystSystemHost(
        market_scan_cache=svc._market_scan_cache,
        long_tier_staged_tp_state=svc._long_tier_staged_tp_state,
        tick_symbol_subset=svc._tick_symbol_subset,
        pre_screen_results=getattr(svc, "_pre_screen_results", None),
        pre_screen_passed=set(getattr(svc, "_pre_screen_passed", None) or []),
        mlto_handled_keys=set(getattr(svc, "_mlto_handled_keys", None) or []),
        clear_master_strat_cache=svc._clear_master_strat_cache,
        get_trading_account_id=svc._get_trading_account_id,
        sync_hold_timeout_alerts=svc._sync_hold_timeout_alerts,
        append_event=svc._append_event,
        annotate_auto_coin_meta=svc._annotate_auto_coin_meta,
        build_fast_stability_result=svc._build_fast_stability_result,
        run_with_timeout=svc._run_with_timeout,
        record_ai_failure=svc._record_ai_failure,
        record_ai_success=svc._record_ai_success,
        validate_ai_decisions=svc._validate_ai_decisions,
        inject_orch_scheduled_stubs=svc._inject_orch_scheduled_stubs,
        execute_master_decisions=svc._execute_master_decisions,
        maintain_mlto_theses_for_session=svc._maintain_mlto_theses_for_session,
        execute_ai_decisions=svc._execute_ai_decisions,
        execute_defensive_analysis=svc._execute_defensive_analysis,
    )


def run_analyst_system(
    db: Session,
    session,
    active_ids: list,
    market_summary: dict,
    host: AnalystSystemHost,
) -> None:
'''

unified_header = '''

def run_analyst_system_unified(
    db: Session,
    session,
    account,
    active_ids: list,
    market_summary: dict,
    host: AnalystSystemHost,
) -> None:
'''


def dedent(body: str) -> str:
    lines_out = []
    for line in body.splitlines():
        if line.startswith("        "):
            lines_out.append("    " + line[8:])
        else:
            lines_out.append(line)
    return "\n".join(lines_out)

out = ROOT / "backend/services/full_auto/analyst_system_cycle.py"
content = (
    header
    + dedent(wrapper_body)
    + "\n"
    + unified_header
    + dedent(unified_body)
    + "\n"
)
out.write_text(content, encoding="utf-8")
print(f"wrote {out} ({content.count(chr(10))} lines)")
