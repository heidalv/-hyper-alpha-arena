"""Assemble mlto_cycle.py from extracted bodies."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FA = ROOT / "backend/services/full_auto"


def dedent(body: str) -> str:
    return "\n".join(
        ("    " + line[8:] if line.startswith("        ") else line)
        for line in body.splitlines()
    )


header = '''"""MLTO 中长线维护与执行 — 从 monolith 迁出（整改#8 Phase2）。"""
from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


@dataclass
class MltoCycleHost:
    mlto_handled_keys: Set[str] = field(default_factory=set)
    mlto_handled_lock: Any = None
    midlong_persistence_state: Dict[str, Dict] = field(default_factory=dict)
    current_ai_tiers: Optional[List[str]] = None
    last_orch_decisions: Dict[str, Any] = field(default_factory=dict)
    last_orch_decisions_ts: float = 0.0

    inject_midlong_indicators: Callable = field(repr=False, default=lambda *a, **k: None)
    append_event: Callable = field(repr=False, default=lambda *a, **k: None)
    format_agent_event_detail: Callable = field(repr=False, default=lambda *a, **k: "")
    try_execute_independent_agent_open: Callable = field(repr=False, default=lambda *a, **k: False)
    persist_independent_scan_log: Callable = field(repr=False, default=lambda *a, **k: None)
    build_midlong_agent_envelope: Callable = field(repr=False, default=lambda *a, **k: {})


def build_mlto_cycle_host(svc) -> MltoCycleHost:
    lock = getattr(svc, "_mlto_handled_lock", None)
    if lock is None:
        lock = threading.Lock()
        svc._mlto_handled_lock = lock
    handled = getattr(svc, "_mlto_handled_keys", None)
    if handled is None:
        handled = set()
        svc._mlto_handled_keys = handled
    return MltoCycleHost(
        mlto_handled_keys=handled,
        mlto_handled_lock=lock,
        midlong_persistence_state=svc._midlong_persistence_state,
        current_ai_tiers=getattr(svc, "_current_ai_tiers", None),
        last_orch_decisions=getattr(svc, "_last_orch_decisions", None) or {},
        last_orch_decisions_ts=float(getattr(svc, "_last_orch_decisions_ts", 0) or 0),
        inject_midlong_indicators=svc._inject_midlong_indicators,
        append_event=svc._append_event,
        format_agent_event_detail=svc._format_agent_event_detail,
        try_execute_independent_agent_open=svc._try_execute_independent_agent_open,
        persist_independent_scan_log=svc._persist_independent_scan_log,
        build_midlong_agent_envelope=svc._build_midlong_agent_envelope,
    )


def maintain_mlto_theses_for_session(
    *,
    session,
    market_summary: dict,
    analyst_reports: dict,
    mode: str,
    portfolio: dict,
    host: MltoCycleHost,
    symbols_batch: Optional[List[str]] = None,
    run_mid: bool = True,
    run_long: bool = True,
    light_context: bool = False,
) -> None:
'''

maintain = dedent((FA / "_mlto_maintain_body.tmp").read_text(encoding="utf-8"))
execute_header = '''

def execute_mlto_lane(
    *,
    sym: str,
    dec: dict,
    tier: str,
    agent_source: str,
    market_summary: dict,
    analyst_reports: dict,
    db,
    session,
    mode: str,
    portfolio: dict,
    host: MltoCycleHost,
) -> tuple:
'''
execute = dedent((FA / "_mlto_execute_body.tmp").read_text(encoding="utf-8"))

(FA / "mlto_cycle.py").write_text(
    header + maintain + execute_header + execute + "\n",
    encoding="utf-8",
)
print("assembled mlto_cycle.py")
