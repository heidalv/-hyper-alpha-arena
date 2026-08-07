"""Assemble light_trading_cycle, v3_factor_pipeline, strategy_lifecycle."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FA = ROOT / "backend/services/full_auto"


def dedent(body: str) -> str:
    return "\n".join(
        ("    " + line[8:] if line.startswith("        ") else line)
        for line in body.splitlines()
    )


def load(name: str) -> str:
    return (FA / name).read_text(encoding="utf-8")


# --- light trading ---
light = '''"""轻量交易循环 — 从 monolith _run_light_trading_cycle 迁出（整改#8 Phase2）。"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict

logger = logging.getLogger(__name__)


@dataclass
class LightTradingHost:
    active_db_sessions: Dict[str, Any]
    last_unified_snapshot: Any = None

    get_trading_account_id: Callable = field(repr=False, default=lambda *a, **k: 0)
    active_exchange: Callable = field(repr=False, default=lambda: "binance")
    orch_payload_from_decision: Callable = field(repr=False, default=lambda *a, **k: {})
    run_analyst_system: Callable = field(repr=False, default=lambda *a, **k: None)
    safe_commit: Callable = field(repr=False, default=lambda *a, **k: True)


def build_light_trading_host(svc) -> LightTradingHost:
    return LightTradingHost(
        active_db_sessions=svc._active_db_sessions,
        last_unified_snapshot=getattr(svc, "_last_unified_snapshot", None),
        get_trading_account_id=svc._get_trading_account_id,
        active_exchange=svc._active_exchange,
        orch_payload_from_decision=svc._orch_payload_from_decision,
        run_analyst_system=svc._run_analyst_system,
        safe_commit=svc._safe_commit,
    )


def run_light_trading_cycle(session_id: str, host: LightTradingHost) -> None:
''' + dedent(load("_light_trading_body.tmp")) + "\n"

(FA / "light_trading_cycle.py").write_text(light, encoding="utf-8")

# --- v3 factor ---
v3 = '''"""V3 因子管道 — 从 monolith _run_v3_factor_pipeline 迁出（整改#8 Phase2）。"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


@dataclass
class V3FactorHost:
    v3_factor_cache: Dict[str, dict] = field(default_factory=dict)
    V3_FACTOR_CACHE_TTL: float = 90.0


def build_v3_factor_host(svc) -> V3FactorHost:
    return V3FactorHost(
        v3_factor_cache=getattr(svc, "_v3_factor_cache", None) or {},
        V3_FACTOR_CACHE_TTL=float(getattr(svc, "_V3_FACTOR_CACHE_TTL", 90) or 90),
    )


def run_v3_factor_pipeline(
    *,
    host: V3FactorHost,
    db: Session = None,
    session=None,
    symbols: List[str] = None,
    market_summary: Dict[str, Any] = None,
    unified_snapshot=None,
    force: bool = False,
) -> tuple:
''' + dedent(load("_v3_factor_body.tmp")) + "\n"

(FA / "v3_factor_pipeline.py").write_text(v3, encoding="utf-8")

# --- strategy lifecycle ---
# parse REGIME profiles — strip class indent
profiles_raw = load("_regime_profiles.tmp")
# lines like "    REGIME_PARAM_PROFILES = {" — make module-level
prof_lines = []
for line in profiles_raw.splitlines():
    if line.startswith("    "):
        prof_lines.append(line[4:])
    else:
        prof_lines.append(line)
profiles = "\n".join(prof_lines).rstrip() + "\n"

life = '''"""策略生命周期 — champion/terminate/adapt 从 monolith 迁出（整改#8 Phase2）。"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

''' + profiles + '''

@dataclass
class StrategyLifecycleHost:
    NATURE_TO_TIER_MAP: Dict[str, str] = field(default_factory=dict)


def build_strategy_lifecycle_host(svc) -> StrategyLifecycleHost:
    return StrategyLifecycleHost(
        NATURE_TO_TIER_MAP=getattr(svc, "_NATURE_TO_TIER_MAP", {}) or {},
    )


def is_champion_strategy(mem) -> bool:
''' + dedent(load("_is_champion_strategy_body.tmp")) + '''

def should_terminate_strategy(db: Session, strategy, session) -> tuple:
''' + dedent(load("_should_terminate_strategy_body.tmp")) + '''

def pause_champion_strategy(db: Session, strategy, reason: str) -> None:
''' + dedent(load("_pause_champion_strategy_body.tmp")) + '''

def snapshot_strategy_genome(db: Session, strategy, memory) -> None:
''' + dedent(load("_snapshot_strategy_genome_body.tmp")) + '''

def terminate_strategy(db: Session, strategy, reason: str, host: StrategyLifecycleHost) -> None:
''' + dedent(load("_terminate_strategy_body.tmp")) + '''

def get_regime_profile(regime: str) -> dict:
''' + dedent(load("_get_regime_profile_body.tmp")) + '''

def adapt_strategy_params(db: Session, strategy, market_info: dict) -> bool:
''' + dedent(load("_adapt_strategy_params_body.tmp")) + "\n"

# fix get_regime_profile body if it still calls host.get_regime_profile
life = life.replace("host.get_regime_profile(", "get_regime_profile(")
# adapt may call get_regime_profile via host — already replaced in extract to host.get_regime_profile then we map
life = life.replace("host.get_regime_profile(", "get_regime_profile(")

(FA / "strategy_lifecycle.py").write_text(life, encoding="utf-8")
print("assembled 3 modules")
