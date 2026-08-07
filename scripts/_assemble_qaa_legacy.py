"""Assemble qaa_legacy_cycle.py from extracted bodies."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FA = ROOT / "backend/services/full_auto"


def dedent(body: str) -> str:
    return "\n".join("    " + line[8:] if line.startswith("        ") else line for line in body.splitlines())


def load(name: str) -> str:
    return (FA / name).read_text(encoding="utf-8")


header = '''"""Legacy QAA tick + Agent handlers — 从 monolith 迁出（整改#8 Phase2）。"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Set

logger = logging.getLogger(__name__)


@dataclass
class QaaLegacyHost:
    market_scan_cache: Dict[str, Any]
    active_positions_cache: Any
    pre_screen_results: Any = None
    pre_screen_passed: Set[str] = field(default_factory=set)
    qaa_last_decision: Any = None
    qaa_agents_registered: bool = False
    last_unified_snapshot: Any = None
    risk_assessor: Any = None

    get_or_capture_unified_snapshot: Callable = field(repr=False, default=lambda *a, **k: None)
    run_with_timeout: Callable = field(repr=False, default=lambda *a, **k: None)
    run_v3_factor_pipeline: Callable = field(repr=False, default=lambda *a, **k: None)
    run_analyst_system: Callable = field(repr=False, default=lambda *a, **k: None)
    safe_commit: Callable = field(repr=False, default=lambda *a, **k: True)
    clear_master_strat_cache: Callable = field(repr=False, default=lambda: None)


def build_qaa_legacy_host(svc) -> QaaLegacyHost:
    return QaaLegacyHost(
        market_scan_cache=svc._market_scan_cache,
        active_positions_cache=svc._active_positions_cache,
        pre_screen_results=getattr(svc, "_pre_screen_results", None),
        pre_screen_passed=set(getattr(svc, "_pre_screen_passed", None) or []),
        qaa_last_decision=getattr(svc, "_qaa_last_decision", None),
        qaa_agents_registered=getattr(svc, "_qaa_agents_registered", False),
        last_unified_snapshot=getattr(svc, "_last_unified_snapshot", None),
        risk_assessor=getattr(svc, "_risk_assessor", None),
        get_or_capture_unified_snapshot=svc._get_or_capture_unified_snapshot,
        run_with_timeout=svc._run_with_timeout,
        run_v3_factor_pipeline=svc._run_v3_factor_pipeline,
        run_analyst_system=svc._run_analyst_system,
        safe_commit=svc._safe_commit,
        clear_master_strat_cache=svc._clear_master_strat_cache,
    )


def get_qaa_handler(agent_id: str, host: QaaLegacyHost):
    """映射 agent_id → handler(action, payload)。"""
    handlers = {
        "market_data": lambda a, p: qaa_market_data(a, p, host),
        "risk_control": lambda a, p: qaa_risk_control(a, p, host),
        "factor_engine": lambda a, p: qaa_factor_engine(a, p, host),
        "intel_signal": lambda a, p: qaa_intel_signal(a, p, host),
        "mt_orchestrator": lambda a, p: qaa_mt_orchestrator(a, p, host),
        "master_controller": lambda a, p: qaa_master_controller(a, p, host),
        "trade_execution": lambda a, p: qaa_trade_execution(a, p, host),
        "genetic_optimizer": lambda a, p: qaa_genetic_optimizer(a, p, host),
        "signal_bus": lambda a, p: qaa_signal_bus(a, p, host),
    }
    return handlers.get(agent_id)


def register_qaa_agents(host: QaaLegacyHost) -> None:
'''

register = dedent(load("_qaa_register_body.tmp"))
# fix handler lookup in register body
register = register.replace("host._get_qaa_handler(agent_id)", "get_qaa_handler(agent_id, host)")

parts = [header + register + "\n\n\n"]

handler_defs = [
    ("build_qaa_snapshot", "_qaa_snap_body.tmp", "session_id: str, host: QaaLegacyHost"),
    ("run_qaa_tick", "_qaa_tick_body.tmp", "session_id: str, host: QaaLegacyHost"),
    ("qaa_market_data", "_qaa_qaa_market_data_body.tmp", "action: str, payload: dict, host: QaaLegacyHost"),
    ("qaa_risk_control", "_qaa_qaa_risk_control_body.tmp", "action: str, payload: dict, host: QaaLegacyHost"),
    ("qaa_factor_engine", "_qaa_qaa_factor_engine_body.tmp", "action: str, payload: dict, host: QaaLegacyHost"),
    ("qaa_compute_signals", "_qaa_qaa_compute_signals_body.tmp", "payload: dict, host: QaaLegacyHost"),
    ("qaa_compute_unified", "_qaa_qaa_compute_unified_body.tmp", "payload: dict, host: QaaLegacyHost"),
    ("qaa_intel_signal", "_qaa_qaa_intel_signal_body.tmp", "action: str, payload: dict, host: QaaLegacyHost"),
    ("qaa_mt_orchestrator", "_qaa_qaa_mt_orchestrator_body.tmp", "action: str, payload: dict, host: QaaLegacyHost"),
    ("qaa_master_controller", "_qaa_qaa_master_controller_body.tmp", "action: str, payload: dict, host: QaaLegacyHost"),
    ("qaa_trade_execution", "_qaa_qaa_trade_execution_body.tmp", "action: str, payload: dict, host: QaaLegacyHost"),
    ("qaa_genetic_optimizer", "_qaa_qaa_genetic_optimizer_body.tmp", "action: str, payload: dict, host: QaaLegacyHost"),
    ("qaa_signal_bus", "_qaa_qaa_signal_bus_body.tmp", "action: str, payload: dict, host: QaaLegacyHost"),
]

for fn, tmp, sig in handler_defs:
    body = dedent(load(tmp))
    parts.append(f"def {fn}({sig}):\n{body}\n\n")

(FA / "qaa_legacy_cycle.py").write_text("".join(parts), encoding="utf-8")
print("assembled qaa_legacy_cycle.py")
