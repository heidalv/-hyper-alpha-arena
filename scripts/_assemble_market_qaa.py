"""Assemble market_scan_cycle, qaa_v3_tick_cycle, qaa_v3_forced_logs."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def dedent(body: str) -> str:
    return "\n".join("    " + line[8:] if line.startswith("        ") else line for line in body.splitlines())

# --- market scan ---
ms_header = '''"""市场扫描 — 从 monolith _scan_markets/_bg_market_scan 迁出（整改#8 Phase2）。"""
from __future__ import annotations

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Tuple

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


@dataclass
class MarketScanHost:
    market_scan_cache: Dict[str, Any]
    market_scan_cache_ts: float
    market_scan_cache_ttl: float
    bg_scan_running: bool = False


def build_market_scan_host(svc) -> MarketScanHost:
    return MarketScanHost(
        market_scan_cache=svc._market_scan_cache,
        market_scan_cache_ts=svc._market_scan_cache_ts,
        market_scan_cache_ttl=svc._MARKET_SCAN_CACHE_TTL,
        bg_scan_running=getattr(svc, "_bg_scan_running", False),
    )


def run_scan_markets(db: Session, symbols: List[str], host: MarketScanHost) -> Dict[str, Any]:
'''

bg_header = '''

def run_bg_market_scan(symbols: List[str], host: MarketScanHost, scan_fn: Callable = run_scan_markets) -> None:
'''

ms_body = (ROOT / "backend/services/full_auto/_scan_markets_body.tmp").read_text(encoding="utf-8")
bg_body = (ROOT / "backend/services/full_auto/_bg_market_scan_body.tmp").read_text(encoding="utf-8")
# bg uses scan_fn instead of direct call
bg_body = bg_body.replace("run_scan_markets(db, symbols, host)", "scan_fn(db, symbols, host)")
(ROOT / "backend/services/full_auto/market_scan_cycle.py").write_text(
    ms_header + dedent(ms_body) + "\n" + bg_header + dedent(bg_body) + "\n", encoding="utf-8"
)

# --- forced logs ---
logs_header = '''"""QAA v3 超时降级决策日志 — 从 monolith 迁出（整改#8 Phase2）。"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def write_qaa_v3_forced_decision_logs(
    *,
    session_orm_id: int,
    account_id: int,
    decisions: list,
    balance_info: dict,
    positions_list: list,
    market_summary: dict,
) -> None:
'''
logs_body = (ROOT / "backend/services/full_auto/_qaa_v3_logs_body.tmp").read_text(encoding="utf-8")
(ROOT / "backend/services/full_auto/qaa_v3_forced_logs.py").write_text(
    logs_header + dedent(logs_body) + "\n", encoding="utf-8"
)

# --- qaa v3 tick ---
tick_header = '''"""QAA v3 tick — 从 monolith _run_qaa_v3_tick 迁出（整改#8 Phase2）。"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


@dataclass
class QaaV3TickHost:
    active_db_sessions: Dict[str, Any]
    market_scan_cache: Dict[str, Any]
    market_scan_cache_ts: float
    active_positions_cache: list
    unified_tick_count: Dict[str, int]
    pre_screen_results: Any = None
    pre_screen_passed: Set[str] = field(default_factory=set)
    qaa_ctx: Any = None
    qaa_last_decision: Any = None
    orch_bg_thread: Any = None

    bootstrap_qaa_v3_context: Callable = field(repr=False, default=lambda *a, **k: False)
    get_trading_account_id: Callable = field(repr=False, default=lambda *a, **k: 0)
    bootstrap_market_summary: Callable = field(repr=False, default=lambda *a, **k: {})
    get_or_capture_unified_snapshot: Callable = field(repr=False, default=lambda *a, **k: None)
    sanitize_market_summary_for_qaa: Callable = field(repr=False, default=lambda m: m)
    run_analyst_system_v3: Callable = field(repr=False, default=lambda *a, **k: None)
    safe_commit: Callable = field(repr=False, default=lambda *a, **k: True)


def build_qaa_v3_tick_host(svc) -> QaaV3TickHost:
    host = QaaV3TickHost(
        active_db_sessions=svc._active_db_sessions,
        market_scan_cache=svc._market_scan_cache,
        market_scan_cache_ts=svc._market_scan_cache_ts,
        active_positions_cache=svc._active_positions_cache,
        unified_tick_count=svc._unified_tick_count,
        pre_screen_results=getattr(svc, "_pre_screen_results", None),
        pre_screen_passed=set(getattr(svc, "_pre_screen_passed", None) or []),
        qaa_ctx=getattr(svc, "_qaa_ctx", None),
        qaa_last_decision=getattr(svc, "_qaa_last_decision", None),
        orch_bg_thread=getattr(svc, "_orch_bg_thread", None),
        get_trading_account_id=svc._get_trading_account_id,
        bootstrap_market_summary=svc._bootstrap_market_summary,
        get_or_capture_unified_snapshot=svc._get_or_capture_unified_snapshot,
        sanitize_market_summary_for_qaa=svc._sanitize_market_summary_for_qaa,
        run_analyst_system_v3=svc._run_analyst_system_v3,
        safe_commit=svc._safe_commit,
    )

    def _bootstrap(blocking: bool = False) -> bool:
        ok = svc.bootstrap_qaa_v3_context(blocking=blocking)
        host.qaa_ctx = getattr(svc, "_qaa_ctx", None)
        host.qaa_last_decision = getattr(svc, "_qaa_last_decision", None)
        return ok

    host.bootstrap_qaa_v3_context = _bootstrap
    return host


def run_qaa_v3_tick(session_id: str, host: QaaV3TickHost) -> None:
'''
tick_body = (ROOT / "backend/services/full_auto/_qaa_v3_tick_body.tmp").read_text(encoding="utf-8")
(ROOT / "backend/services/full_auto/qaa_v3_tick_cycle.py").write_text(
    tick_header + dedent(tick_body) + "\n", encoding="utf-8"
)
print("assembled 3 modules")
