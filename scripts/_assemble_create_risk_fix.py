"""Assemble strategy_creation + symbol_risk with correct __* tmp names."""
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


create = (
    '"""策略自动创建 — 从 monolith 迁出（整改#8 Phase2）。"""\n'
    "from __future__ import annotations\n\n"
    "import logging\n"
    "import time\n"
    "from dataclasses import dataclass, field\n"
    "from typing import Any, Callable, Dict, List, Optional\n\n"
    "logger = logging.getLogger(__name__)\n\n\n"
    "@dataclass\n"
    "class StrategyCreationHost:\n"
    "    strategy_creation_ts: Dict[str, float] = field(default_factory=dict)\n"
    "    STRATEGY_CREATION_COOLDOWN: float = 600.0\n\n"
    "    append_event: Callable = field(repr=False, default=lambda *a, **k: None)\n"
    "    get_trading_account_id: Callable = field(repr=False, default=lambda *a, **k: 0)\n"
    "    session_trading_mode: Callable = field(repr=False, default=lambda *a, **k: \"paper\")\n\n\n"
    "def build_strategy_creation_host(svc) -> StrategyCreationHost:\n"
    "    return StrategyCreationHost(\n"
    "        strategy_creation_ts=getattr(svc, \"_strategy_creation_ts\", None) or {},\n"
    "        STRATEGY_CREATION_COOLDOWN=float(getattr(svc, \"_STRATEGY_CREATION_COOLDOWN\", 600) or 600),\n"
    "        append_event=svc._append_event,\n"
    "        get_trading_account_id=svc._get_trading_account_id,\n"
    "        session_trading_mode=svc._session_trading_mode,\n"
    "    )\n\n\n"
    "def try_create_from_template(db, symbol: str, tier: str,\n"
    "                            account_id: int, risk_level: str,\n"
    "                            trading_mode: str) -> Optional[str]:\n"
    + dedent(load("__try_create_from_template_body.tmp"))
    + "\n\n"
    "def auto_create_strategy(db, session, symbol: str,\n"
    "                        market_info: dict,\n"
    "                        host: StrategyCreationHost,\n"
    "                        _account_id: int = None,\n"
    "                        _risk_level: str = None,\n"
    "                        _trading_mode: str = None,\n"
    "                        _symbols: list = None) -> Optional[str]:\n"
    + dedent(load("__auto_create_strategy_body.tmp"))
    + "\n\n"
    "def infer_timeframe_slots(market_info: dict) -> list:\n"
    + dedent(load("__infer_timeframe_slots_body.tmp"))
    + "\n\n"
    "def infer_timeframe_slot(market_info: dict) -> str:\n"
    + dedent(load("__infer_timeframe_slot_body.tmp"))
    + "\n\n"
    "def bg_create_strategy(session_id: str, account_id: int, symbol: str,\n"
    "                      market_info: dict,\n"
    "                      risk_level: str, trading_mode: str, symbols: list,\n"
    "                      reason: str) -> None:\n"
    + dedent(load("__bg_create_strategy_body.tmp"))
    + "\n"
)
(FA / "strategy_creation.py").write_text(create, encoding="utf-8")
print("strategy_creation", len(create.splitlines()))

cls = load("_per_symbol_risk_result.tmp").replace(
    "class _PerSymbolRiskResult", "class PerSymbolRiskResult"
)

risk = (
    '"""Per-symbol / 全局风控 — 从 monolith 迁出（整改#8 Phase2）。"""\n'
    "from __future__ import annotations\n\n"
    "import logging\n"
    "import time\n"
    "from dataclasses import dataclass, field\n"
    "from datetime import datetime\n"
    "from typing import Any, Callable, Dict, List, Optional\n\n"
    "from sqlalchemy.orm import Session\n\n"
    "logger = logging.getLogger(__name__)\n\n"
    + cls
    + "\n\n"
    "@dataclass\n"
    "class SymbolRiskHost:\n"
    "    symbol_daily_pnl: Dict[str, Dict[str, float]] = field(default_factory=dict)\n"
    "    frozen_symbols: Dict[str, set] = field(default_factory=dict)\n"
    "    defensive_entered_at: Dict[str, float] = field(default_factory=dict)\n"
    "    recovery_until: Dict[str, float] = field(default_factory=dict)\n"
    "    PEAK_DECAY_GRACE_HOURS: float = 2.0\n"
    "    PEAK_DECAY_RATE_PER_HOUR: float = 0.10\n"
    "    PEAK_DECAY_ACCEL_HOURS: float = 6.0\n"
    "    RECOVERY_DURATION_HOURS: float = 2.0\n"
    "    RECOVERY_POSITION_SCALE: float = 0.5\n\n"
    "    get_trading_account_id: Callable = field(repr=False, default=lambda *a, **k: 0)\n"
    "    get_lock_profile: Callable = field(repr=False, default=lambda *a, **k: None)\n"
    "    append_event: Callable = field(repr=False, default=lambda *a, **k: None)\n"
    "    should_log_pause_event: Callable = field(repr=False, default=lambda *a, **k: True)\n"
    "    record_strategy_pause: Callable = field(repr=False, default=lambda *a, **k: None)\n"
    "    clear_strategy_pause_meta: Callable = field(repr=False, default=lambda *a, **k: None)\n\n\n"
    "def build_symbol_risk_host(svc) -> SymbolRiskHost:\n"
    "    return SymbolRiskHost(\n"
    "        symbol_daily_pnl=getattr(svc, \"_symbol_daily_pnl\", None) or {},\n"
    "        frozen_symbols=getattr(svc, \"_frozen_symbols\", None) or {},\n"
    "        defensive_entered_at=svc._defensive_entered_at,\n"
    "        recovery_until=svc._recovery_until,\n"
    "        PEAK_DECAY_GRACE_HOURS=svc._PEAK_DECAY_GRACE_HOURS,\n"
    "        PEAK_DECAY_RATE_PER_HOUR=svc._PEAK_DECAY_RATE_PER_HOUR,\n"
    "        PEAK_DECAY_ACCEL_HOURS=svc._PEAK_DECAY_ACCEL_HOURS,\n"
    "        RECOVERY_DURATION_HOURS=svc._RECOVERY_DURATION_HOURS,\n"
    "        RECOVERY_POSITION_SCALE=svc._RECOVERY_POSITION_SCALE,\n"
    "        get_trading_account_id=svc._get_trading_account_id,\n"
    "        get_lock_profile=svc._get_lock_profile,\n"
    "        append_event=svc._append_event,\n"
    "        should_log_pause_event=svc._should_log_pause_event,\n"
    "        record_strategy_pause=svc._record_strategy_pause,\n"
    "        clear_strategy_pause_meta=svc._clear_strategy_pause_meta,\n"
    "    )\n\n\n"
    "def evaluate_dynamic_risk(session, market_summary: Dict[str, Any], host: SymbolRiskHost) -> None:\n"
    + dedent(load("_evaluate_dynamic_risk_body.tmp"))
    + "\n\n"
    "def update_symbol_daily_pnl(db: Session, session, host: SymbolRiskHost) -> None:\n"
    + dedent(load("__update_symbol_daily_pnl_body.tmp"))
    + "\n\n"
    "def freeze_symbol_strategies(db: Session, session, symbol: str, reason: str, host: SymbolRiskHost) -> None:\n"
    + dedent(load("__freeze_symbol_strategies_body.tmp"))
    + "\n\n"
    "def unfreeze_recovered_symbols(db: Session, session, still_frozen: List[str], host: SymbolRiskHost) -> None:\n"
    + dedent(load("__unfreeze_recovered_symbols_body.tmp"))
    + "\n\n"
    "def check_per_symbol_risk(db: Session, session, host: SymbolRiskHost) -> PerSymbolRiskResult:\n"
    + dedent(load("__check_per_symbol_risk_body.tmp"))
    + "\n\n"
    "def check_global_risk(db: Session, session, host: SymbolRiskHost) -> Optional[str]:\n"
    + dedent(load("__check_global_risk_body.tmp"))
    + "\n"
)
(FA / "symbol_risk.py").write_text(risk, encoding="utf-8")
print("symbol_risk", len(risk.splitlines()))
print("assembled ok")
