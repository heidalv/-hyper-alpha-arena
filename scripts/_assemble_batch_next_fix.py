"""Assemble light_trading_cycle, v3_factor_pipeline, strategy_lifecycle (fixed tmp names)."""
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


light = (
    '"""轻量交易循环 — 从 monolith _run_light_trading_cycle 迁出（整改#8 Phase2）。"""\n'
    "from __future__ import annotations\n\n"
    "import logging\n"
    "from dataclasses import dataclass, field\n"
    "from typing import Any, Callable, Dict\n\n"
    "logger = logging.getLogger(__name__)\n\n\n"
    "@dataclass\n"
    "class LightTradingHost:\n"
    "    active_db_sessions: Dict[str, Any]\n"
    "    last_unified_snapshot: Any = None\n\n"
    "    get_trading_account_id: Callable = field(repr=False, default=lambda *a, **k: 0)\n"
    "    active_exchange: Callable = field(repr=False, default=lambda: \"binance\")\n"
    "    orch_payload_from_decision: Callable = field(repr=False, default=lambda *a, **k: {})\n"
    "    run_analyst_system: Callable = field(repr=False, default=lambda *a, **k: None)\n"
    "    safe_commit: Callable = field(repr=False, default=lambda *a, **k: True)\n\n\n"
    "def build_light_trading_host(svc) -> LightTradingHost:\n"
    "    return LightTradingHost(\n"
    "        active_db_sessions=svc._active_db_sessions,\n"
    "        last_unified_snapshot=getattr(svc, \"_last_unified_snapshot\", None),\n"
    "        get_trading_account_id=svc._get_trading_account_id,\n"
    "        active_exchange=svc._active_exchange,\n"
    "        orch_payload_from_decision=svc._orch_payload_from_decision,\n"
    "        run_analyst_system=svc._run_analyst_system,\n"
    "        safe_commit=svc._safe_commit,\n"
    "    )\n\n\n"
    "def run_light_trading_cycle(session_id: str, host: LightTradingHost) -> None:\n"
    + dedent(load("_light_trading_body.tmp"))
    + "\n"
)
(FA / "light_trading_cycle.py").write_text(light, encoding="utf-8")
print("light", len(light.splitlines()))

v3 = (
    '"""V3 因子管道 — 从 monolith _run_v3_factor_pipeline 迁出（整改#8 Phase2）。"""\n'
    "from __future__ import annotations\n\n"
    "import logging\n"
    "import time\n"
    "from dataclasses import dataclass, field\n"
    "from typing import Any, Dict, List, Optional, Tuple\n\n"
    "from sqlalchemy.orm import Session\n\n"
    "logger = logging.getLogger(__name__)\n\n\n"
    "@dataclass\n"
    "class V3FactorHost:\n"
    "    v3_factor_cache: Dict[str, dict] = field(default_factory=dict)\n"
    "    V3_FACTOR_CACHE_TTL: float = 90.0\n\n\n"
    "def build_v3_factor_host(svc) -> V3FactorHost:\n"
    "    return V3FactorHost(\n"
    "        v3_factor_cache=getattr(svc, \"_v3_factor_cache\", None) or {},\n"
    "        V3_FACTOR_CACHE_TTL=float(getattr(svc, \"_V3_FACTOR_CACHE_TTL\", 90) or 90),\n"
    "    )\n\n\n"
    "def run_v3_factor_pipeline(\n"
    "    *,\n"
    "    host: V3FactorHost,\n"
    "    db: Session = None,\n"
    "    session=None,\n"
    "    symbols: List[str] = None,\n"
    "    market_summary: Dict[str, Any] = None,\n"
    "    unified_snapshot=None,\n"
    "    force: bool = False,\n"
    ") -> tuple:\n"
    + dedent(load("_v3_factor_body.tmp"))
    + "\n"
)
(FA / "v3_factor_pipeline.py").write_text(v3, encoding="utf-8")
print("v3", len(v3.splitlines()))

profiles_raw = load("_regime_profiles.tmp")
prof_lines = []
for line in profiles_raw.splitlines():
    if line.startswith("    "):
        prof_lines.append(line[4:])
    else:
        prof_lines.append(line)
profiles = "\n".join(prof_lines).rstrip() + "\n"

life = (
    '"""策略生命周期 — champion/terminate/adapt 从 monolith 迁出（整改#8 Phase2）。"""\n'
    "from __future__ import annotations\n\n"
    "import logging\n"
    "from dataclasses import dataclass, field\n"
    "from datetime import datetime, timezone\n"
    "from typing import Any, Callable, Dict, Optional\n\n"
    "from sqlalchemy.orm import Session\n\n"
    "logger = logging.getLogger(__name__)\n\n"
    + profiles
    + "\n"
    "@dataclass\n"
    "class StrategyLifecycleHost:\n"
    "    NATURE_TO_TIER_MAP: Dict[str, str] = field(default_factory=dict)\n\n\n"
    "def build_strategy_lifecycle_host(svc) -> StrategyLifecycleHost:\n"
    "    return StrategyLifecycleHost(\n"
    "        NATURE_TO_TIER_MAP=getattr(svc, \"_NATURE_TO_TIER_MAP\", {}) or {},\n"
    "    )\n\n\n"
    "def is_champion_strategy(mem) -> bool:\n"
    + dedent(load("__is_champion_strategy_body.tmp"))
    + "\n\n"
    "def should_terminate_strategy(db: Session, strategy, session) -> tuple:\n"
    + dedent(load("__should_terminate_strategy_body.tmp"))
    + "\n\n"
    "def pause_champion_strategy(db: Session, strategy, reason: str) -> None:\n"
    + dedent(load("__pause_champion_strategy_body.tmp"))
    + "\n\n"
    "def snapshot_strategy_genome(db: Session, strategy, memory) -> None:\n"
    + dedent(load("__snapshot_strategy_genome_body.tmp"))
    + "\n\n"
    "def terminate_strategy(db: Session, strategy, reason: str, host: StrategyLifecycleHost) -> None:\n"
    + dedent(load("__terminate_strategy_body.tmp"))
    + "\n\n"
    "def get_regime_profile(regime: str) -> dict:\n"
    + dedent(load("__get_regime_profile_body.tmp"))
    + "\n\n"
    "def adapt_strategy_params(db: Session, strategy, market_info: dict) -> bool:\n"
    + dedent(load("__adapt_strategy_params_body.tmp"))
    + "\n"
)
life = life.replace("host.get_regime_profile(", "get_regime_profile(")
(FA / "strategy_lifecycle.py").write_text(life, encoding="utf-8")
print("life", len(life.splitlines()))
print("assembled ok")
