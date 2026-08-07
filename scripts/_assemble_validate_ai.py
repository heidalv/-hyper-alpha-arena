"""Assemble ai_decision_audit.py."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
body = (ROOT / "backend/services/full_auto/_validate_ai_body.tmp").read_text(encoding="utf-8")
header = '''"""AI 决策审核 — 从 monolith _validate_ai_decisions 迁出（整改#8 Phase2）。"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List

logger = logging.getLogger(__name__)

VALID_ACTIONS = {"hold", "buy", "sell", "close", "reduce", "pyramid", "dca"}
VALID_RISK_LEVELS = {"low", "medium", "high", "critical"}


@dataclass
class AiDecisionAuditHost:
    nature_to_tier_map: Dict[str, str]
    health_status: Dict[str, Any]
    last_unified_snapshot: Any = None
    append_event: Callable = field(repr=False, default=lambda *a, **k: None)
    event_scope_label: Callable = field(repr=False, default=lambda *a, **k: "")


def build_ai_decision_audit_host(svc) -> AiDecisionAuditHost:
    return AiDecisionAuditHost(
        nature_to_tier_map=svc._NATURE_TO_TIER_MAP,
        health_status=svc._health_status,
        last_unified_snapshot=getattr(svc, "_last_unified_snapshot", None),
        append_event=svc._append_event,
        event_scope_label=svc._event_scope_label,
    )


def validate_ai_decisions(
    session,
    master_result: Dict,
    session_symbols: List[str],
    positions_list: List[Dict],
    host: AiDecisionAuditHost,
) -> Dict:
'''
dedented = ["    " + line[8:] if line.startswith("        ") else line for line in body.splitlines()]
out = ROOT / "backend/services/full_auto/ai_decision_audit.py"
out.write_text(header + "\n".join(dedented) + "\n", encoding="utf-8")
print(f"wrote {out}")
