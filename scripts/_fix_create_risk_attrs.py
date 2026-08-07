"""Fix strategy_creation infer_slot overflow + symbol_risk host attrs."""
from pathlib import Path

FA = Path(__file__).resolve().parents[1] / "backend/services/full_auto"

# --- fix strategy_creation infer_timeframe_slot ---
p = FA / "strategy_creation.py"
t = p.read_text(encoding="utf-8")
# Replace broken trailing section from "def infer_timeframe_slot" if present,
# else from leftover "def _infer_timeframe_slot"
marker = "\ndef infer_timeframe_slots(market_info: dict) -> list:\n"
if marker not in t:
    raise SystemExit("infer_timeframe_slots missing")

# Rebuild from slots body only (clean), then proper slot + bg
slots_body = (FA / "__infer_timeframe_slots_body.tmp").read_text(encoding="utf-8")


def dedent(body: str) -> str:
    return "\n".join(
        ("    " + line[8:] if line.startswith("        ") else line)
        for line in body.splitlines()
    )


head = t.split(marker, 1)[0]
tail = (
    marker
    + dedent(slots_body)
    + "\n\n"
    "def infer_timeframe_slot(market_info: dict) -> str:\n"
    '    """兼容旧调用：返回单个最优 slot"""\n'
    "    slots = infer_timeframe_slots(market_info)\n"
    '    return slots[0] if slots else "mid"\n\n\n'
    "def bg_create_strategy(session_id: str, account_id: int, symbol: str,\n"
    "                      market_info: dict,\n"
    "                      risk_level: str, trading_mode: str, symbols: list,\n"
    "                      reason: str) -> None:\n"
    '    logger.warning("[FullAuto-BG] _bg_create_strategy 已废弃，不应被调用")\n'
)
p.write_text(head + tail, encoding="utf-8")
print("fixed strategy_creation")

# --- fix symbol_risk host ---
p2 = FA / "symbol_risk.py"
t2 = p2.read_text(encoding="utf-8")

old_host = '''@dataclass
class SymbolRiskHost:
    symbol_daily_pnl: Dict[str, Dict[str, float]] = field(default_factory=dict)
    frozen_symbols: Dict[str, set] = field(default_factory=dict)
    defensive_entered_at: Dict[str, float] = field(default_factory=dict)
    recovery_until: Dict[str, float] = field(default_factory=dict)
    PEAK_DECAY_GRACE_HOURS: float = 2.0
    PEAK_DECAY_RATE_PER_HOUR: float = 0.10
    PEAK_DECAY_ACCEL_HOURS: float = 6.0
    RECOVERY_DURATION_HOURS: float = 2.0
    RECOVERY_POSITION_SCALE: float = 0.5

    get_trading_account_id: Callable = field(repr=False, default=lambda *a, **k: 0)
    get_lock_profile: Callable = field(repr=False, default=lambda *a, **k: None)
    append_event: Callable = field(repr=False, default=lambda *a, **k: None)
    should_log_pause_event: Callable = field(repr=False, default=lambda *a, **k: True)
    record_strategy_pause: Callable = field(repr=False, default=lambda *a, **k: None)
    clear_strategy_pause_meta: Callable = field(repr=False, default=lambda *a, **k: None)


def build_symbol_risk_host(svc) -> SymbolRiskHost:
    return SymbolRiskHost(
        symbol_daily_pnl=getattr(svc, "_symbol_daily_pnl", None) or {},
        frozen_symbols=getattr(svc, "_frozen_symbols", None) or {},
        defensive_entered_at=svc._defensive_entered_at,
        recovery_until=svc._recovery_until,
        PEAK_DECAY_GRACE_HOURS=svc._PEAK_DECAY_GRACE_HOURS,
        PEAK_DECAY_RATE_PER_HOUR=svc._PEAK_DECAY_RATE_PER_HOUR,
        PEAK_DECAY_ACCEL_HOURS=svc._PEAK_DECAY_ACCEL_HOURS,
        RECOVERY_DURATION_HOURS=svc._RECOVERY_DURATION_HOURS,
        RECOVERY_POSITION_SCALE=svc._RECOVERY_POSITION_SCALE,
        get_trading_account_id=svc._get_trading_account_id,
        get_lock_profile=svc._get_lock_profile,
        append_event=svc._append_event,
        should_log_pause_event=svc._should_log_pause_event,
        record_strategy_pause=svc._record_strategy_pause,
        clear_strategy_pause_meta=svc._clear_strategy_pause_meta,
    )
'''

new_host = '''@dataclass
class SymbolRiskHost:
    symbol_daily_pnl: Dict[str, Dict[str, float]] = field(default_factory=dict)
    symbol_frozen_set: Dict[str, set] = field(default_factory=dict)
    strat_pause_meta: Dict[Any, Dict[str, Any]] = field(default_factory=dict)
    defensive_entered_at: Dict[str, float] = field(default_factory=dict)
    recovery_until: Dict[str, float] = field(default_factory=dict)
    state_lock: Any = None
    SYMBOL_FREEZE_COOLDOWN_MINUTES: float = 60.0
    PEAK_DECAY_GRACE_HOURS: float = 2.0
    PEAK_DECAY_RATE_PER_HOUR: float = 0.10
    PEAK_DECAY_ACCEL_HOURS: float = 6.0
    RECOVERY_DURATION_HOURS: float = 2.0
    RECOVERY_POSITION_SCALE: float = 0.5

    get_trading_account_id: Callable = field(repr=False, default=lambda *a, **k: 0)
    get_lock_profile: Callable = field(repr=False, default=lambda *a, **k: None)
    paper_loss_locks_disabled: Callable = field(repr=False, default=lambda *a, **k: False)
    append_event: Callable = field(repr=False, default=lambda *a, **k: None)
    should_log_pause_event: Callable = field(repr=False, default=lambda *a, **k: True)
    record_strategy_pause: Callable = field(repr=False, default=lambda *a, **k: None)
    clear_strategy_pause_meta: Callable = field(repr=False, default=lambda *a, **k: None)


def build_symbol_risk_host(svc) -> SymbolRiskHost:
    return SymbolRiskHost(
        symbol_daily_pnl=getattr(svc, "_symbol_daily_pnl", None) or {},
        symbol_frozen_set=getattr(svc, "_symbol_frozen_set", None) or {},
        strat_pause_meta=getattr(svc, "_strat_pause_meta", None) or {},
        defensive_entered_at=svc._defensive_entered_at,
        recovery_until=svc._recovery_until,
        state_lock=getattr(svc, "_state_lock", None),
        SYMBOL_FREEZE_COOLDOWN_MINUTES=float(getattr(svc, "_SYMBOL_FREEZE_COOLDOWN_MINUTES", 60) or 60),
        PEAK_DECAY_GRACE_HOURS=svc._PEAK_DECAY_GRACE_HOURS,
        PEAK_DECAY_RATE_PER_HOUR=svc._PEAK_DECAY_RATE_PER_HOUR,
        PEAK_DECAY_ACCEL_HOURS=svc._PEAK_DECAY_ACCEL_HOURS,
        RECOVERY_DURATION_HOURS=svc._RECOVERY_DURATION_HOURS,
        RECOVERY_POSITION_SCALE=svc._RECOVERY_POSITION_SCALE,
        get_trading_account_id=svc._get_trading_account_id,
        get_lock_profile=svc._get_lock_profile,
        paper_loss_locks_disabled=svc._paper_loss_locks_disabled,
        append_event=svc._append_event,
        should_log_pause_event=svc._should_log_pause_event,
        record_strategy_pause=svc._record_strategy_pause,
        clear_strategy_pause_meta=svc._clear_strategy_pause_meta,
    )
'''

if old_host not in t2:
    raise SystemExit("old SymbolRiskHost block not found")
t2 = t2.replace(old_host, new_host)

# fix body attribute names
repls = [
    ("host._state_lock", "host.state_lock"),
    ("host._paper_loss_locks_disabled", "host.paper_loss_locks_disabled"),
    ("host._symbol_frozen_set", "host.symbol_frozen_set"),
    ("host._SYMBOL_FREEZE_COOLDOWN_MINUTES", "host.SYMBOL_FREEZE_COOLDOWN_MINUTES"),
    ("host._strat_pause_meta", "host.strat_pause_meta"),
]
for a, b in repls:
    t2 = t2.replace(a, b)

p2.write_text(t2, encoding="utf-8")
print("fixed symbol_risk")

# --- fix monolith shim sync attrs ---
mono = Path(__file__).resolve().parents[1] / "backend/services/full_auto_trading_service.py"
mt = mono.read_text(encoding="utf-8")
mt = mt.replace("self._frozen_symbols = host.frozen_symbols", "self._symbol_frozen_set = host.symbol_frozen_set")
mono.write_text(mt, encoding="utf-8")
print("fixed monolith sync")
