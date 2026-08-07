"""Assemble ai_decisions.py from extracted body."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
body = (ROOT / "backend/services/full_auto/_ai_decisions_body.tmp").read_text(encoding="utf-8")

header = '''"""AI 决策执行 — 从 monolith _execute_ai_decisions 迁出（整改#8 Phase2）。"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


@dataclass
class AiDecisionsHost:
    """monolith 状态与回调切片。"""

    last_unified_snapshot: Any = None

    get_trading_account_id: Callable = field(repr=False, default=lambda *a, **k: 0)
    append_event: Callable = field(repr=False, default=lambda *a, **k: None)
    expand_multi_tier_decisions: Callable = field(repr=False, default=lambda *a, **k: [])
    ensure_bound_strategy: Callable = field(repr=False, default=lambda *a, **k: None)
    resolve_decision_leverage: Callable = field(repr=False, default=lambda *a, **k: (10, ""))
    extract_ai_position_pct: Callable = field(repr=False, default=lambda *a, **k: None)
    resolve_alignment_scale: Callable = field(repr=False, default=lambda *a, **k: 1.0)
    execute_paper_trade: Callable = field(repr=False, default=lambda *a, **k: False)
    execute_live_trade: Callable = field(repr=False, default=lambda *a, **k: None)


def build_ai_decisions_host(svc) -> AiDecisionsHost:
    return AiDecisionsHost(
        last_unified_snapshot=getattr(svc, "_last_unified_snapshot", None),
        get_trading_account_id=svc._get_trading_account_id,
        append_event=svc._append_event,
        expand_multi_tier_decisions=svc._expand_multi_tier_decisions,
        ensure_bound_strategy=svc._ensure_bound_strategy,
        resolve_decision_leverage=svc._resolve_decision_leverage,
        extract_ai_position_pct=svc._extract_ai_position_pct,
        resolve_alignment_scale=svc._resolve_alignment_scale,
        execute_paper_trade=svc._execute_paper_trade,
        execute_live_trade=svc._execute_live_trade,
    )


def execute_ai_decisions(
    db: Session,
    session,
    active_ids: list,
    market_data: dict,
    host: AiDecisionsHost,
) -> None:
'''

dedented = []
for line in body.splitlines():
    if line.startswith("        "):
        dedented.append("    " + line[8:])
    else:
        dedented.append(line)
body = "\n".join(dedented)

out = ROOT / "backend/services/full_auto/ai_decisions.py"
out.write_text(header + body + "\n", encoding="utf-8")
print(f"wrote {out} ({out.read_text(encoding='utf-8').count(chr(10))} lines)")
