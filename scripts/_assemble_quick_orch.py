"""Assemble quick_orchestrator_eval.py."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FA = ROOT / "backend/services/full_auto"


def dedent(body: str) -> str:
    return "\n".join(
        ("    " + line[8:] if line.startswith("        ") else line)
        for line in body.splitlines()
    )


header = '''"""快速编排器评估 — 从 monolith _run_quick_orchestrator_eval 迁出（整改#8 Phase2）。"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List

logger = logging.getLogger(__name__)


@dataclass
class QuickOrchHost:
    active_db_sessions: Dict[str, Any]
    deadlock_rescue_count: Dict[str, int]
    DEADLOCK_RESCUE_MAX: int = 3
    NATURE_TO_TIER_MAP: Dict[str, str] = field(default_factory=dict)

    get_trading_account_id: Callable = field(repr=False, default=lambda *a, **k: 0)
    active_exchange: Callable = field(repr=False, default=lambda: "binance")
    paper_loss_locks_disabled: Callable = field(repr=False, default=lambda *a, **k: False)
    get_lock_profile: Callable = field(repr=False, default=lambda *a, **k: type("P", (), {"ranging_pause": True})())
    record_strategy_pause: Callable = field(repr=False, default=lambda *a, **k: None)
    should_log_pause_event: Callable = field(repr=False, default=lambda *a, **k: True)
    append_event: Callable = field(repr=False, default=lambda *a, **k: None)
    clear_strategy_pause_meta: Callable = field(repr=False, default=lambda *a, **k: None)
    can_resume_strategy: Callable = field(repr=False, default=lambda *a, **k: True)
    safe_commit: Callable = field(repr=False, default=lambda *a, **k: True)


def build_quick_orch_host(svc) -> QuickOrchHost:
    return QuickOrchHost(
        active_db_sessions=svc._active_db_sessions,
        deadlock_rescue_count=svc._deadlock_rescue_count,
        DEADLOCK_RESCUE_MAX=svc._DEADLOCK_RESCUE_MAX,
        NATURE_TO_TIER_MAP=getattr(svc, "_NATURE_TO_TIER_MAP", {}) or {},
        get_trading_account_id=svc._get_trading_account_id,
        active_exchange=svc._active_exchange,
        paper_loss_locks_disabled=svc._paper_loss_locks_disabled,
        get_lock_profile=svc._get_lock_profile,
        record_strategy_pause=svc._record_strategy_pause,
        should_log_pause_event=svc._should_log_pause_event,
        append_event=svc._append_event,
        clear_strategy_pause_meta=svc._clear_strategy_pause_meta,
        can_resume_strategy=svc._can_resume_strategy,
        safe_commit=svc._safe_commit,
    )


def run_quick_orchestrator_eval(session_id: str, host: QuickOrchHost) -> None:
'''

body = dedent((FA / "_quick_orch_body.tmp").read_text(encoding="utf-8"))
# RiskCheckResult is used without import in body — ensure local import stays if present
(FA / "quick_orchestrator_eval.py").write_text(header + body + "\n", encoding="utf-8")
print("assembled quick_orchestrator_eval.py")
