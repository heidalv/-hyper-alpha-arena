"""Assemble analyst_system_v3_cycle.py."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
body = (ROOT / "backend/services/full_auto/_analyst_v3_body.tmp").read_text(encoding="utf-8")
header = '''"""QAA v3 分析师路径 — 从 monolith _run_analyst_system_v3 迁出（整改#8 Phase2）。"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Set

logger = logging.getLogger(__name__)


@dataclass
class AnalystV3Host:
    active_db_sessions: Dict[str, Any]
    mlto_handled_keys: Set[str] = field(default_factory=set)
    annotate_auto_coin_meta: Callable = field(repr=False, default=lambda *a, **k: None)
    build_fast_stability_result: Callable = field(repr=False, default=lambda *a, **k: {})
    run_with_timeout: Callable = field(repr=False, default=lambda *a, **k: None)
    inject_orch_scheduled_stubs: Callable = field(repr=False, default=lambda *a, **k: [])
    execute_master_decisions: Callable = field(repr=False, default=lambda *a, **k: None)
    maintain_mlto_theses_for_session: Callable = field(repr=False, default=lambda *a, **k: None)
    write_qaa_v3_forced_decision_logs: Callable = field(repr=False, default=lambda *a, **k: None)


def build_analyst_v3_host(svc) -> AnalystV3Host:
    return AnalystV3Host(
        active_db_sessions=svc._active_db_sessions,
        mlto_handled_keys=set(getattr(svc, "_mlto_handled_keys", None) or []),
        annotate_auto_coin_meta=svc._annotate_auto_coin_meta,
        build_fast_stability_result=svc._build_fast_stability_result,
        run_with_timeout=svc._run_with_timeout,
        inject_orch_scheduled_stubs=svc._inject_orch_scheduled_stubs,
        execute_master_decisions=svc._execute_master_decisions,
        maintain_mlto_theses_for_session=svc._maintain_mlto_theses_for_session,
        write_qaa_v3_forced_decision_logs=svc._write_qaa_v3_forced_decision_logs,
    )


def run_analyst_system_v3(
    session_id: str,
    session_status: str,
    session_orm_id: int,
    account_id: int,
    active_ids: list,
    market_summary: dict,
    host: AnalystV3Host,
) -> None:
'''
dedented = ["    " + line[8:] if line.startswith("        ") else line for line in body.splitlines()]
out = ROOT / "backend/services/full_auto/analyst_system_v3_cycle.py"
out.write_text(header + "\n".join(dedented) + "\n", encoding="utf-8")
print(f"wrote {out}")
