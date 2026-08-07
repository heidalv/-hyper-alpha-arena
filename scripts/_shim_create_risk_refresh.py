"""Shim strategy creation, symbol risk, refresh positions into monolith."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "backend/services/full_auto_trading_service.py"
lines = path.read_text(encoding="utf-8").splitlines(True)


def replace_block(start_pat: str, end_pat: str, shim: str, label: str) -> None:
    global lines
    start = next(i for i, l in enumerate(lines) if start_pat in l)
    end = next(i for i, l in enumerate(lines) if i > start and end_pat in l)
    lines = lines[:start] + [shim] + lines[end:]
    print(f"{label}: removed {end - start} lines")


# 1) refresh positions (early in file)
refresh_shim = '''    def _refresh_positions_local(self, db, account_id: int,
                                  positions_list: list,
                                  position_map: dict,
                                  symbol_positions: dict,
                                  affected_symbol: str = None):
        from backend.services.full_auto.refresh_positions import (
            build_refresh_positions_host,
            refresh_positions_local,
        )
        return refresh_positions_local(
            db, account_id, positions_list, position_map, symbol_positions,
            build_refresh_positions_host(self),
            affected_symbol=affected_symbol,
        )

'''
replace_block(
    "def _refresh_positions_local(self",
    "def start_session(self",
    refresh_shim,
    "refresh",
)

# 2) strategy creation
create_shim = '''    def _try_create_from_template(self, db, symbol: str, tier: str,
                                   account_id: int, risk_level: str,
                                   trading_mode: str) -> Optional[str]:
        from backend.services.full_auto.strategy_creation import try_create_from_template
        return try_create_from_template(db, symbol, tier, account_id, risk_level, trading_mode)

    def _auto_create_strategy(self, db, session, symbol: str,
                              market_info: dict,
                              _account_id: int = None,
                              _risk_level: str = None,
                              _trading_mode: str = None,
                              _symbols: list = None) -> Optional[str]:
        from backend.services.full_auto.strategy_creation import (
            build_strategy_creation_host,
            auto_create_strategy,
        )
        host = build_strategy_creation_host(self)
        result = auto_create_strategy(
            db, session, symbol, market_info, host,
            _account_id=_account_id, _risk_level=_risk_level,
            _trading_mode=_trading_mode, _symbols=_symbols,
        )
        self._strategy_creation_ts = host.strategy_creation_ts
        return result

    def _infer_timeframe_slots(self, market_info: dict) -> list:
        from backend.services.full_auto.strategy_creation import infer_timeframe_slots
        return infer_timeframe_slots(market_info)

    def _infer_timeframe_slot(self, market_info: dict) -> str:
        from backend.services.full_auto.strategy_creation import infer_timeframe_slot
        return infer_timeframe_slot(market_info)

    def _bg_create_strategy(self, session_id: str, account_id: int, symbol: str,
                           market_info: dict,
                           risk_level: str, trading_mode: str, symbols: list,
                           reason: str):
        from backend.services.full_auto.strategy_creation import bg_create_strategy
        return bg_create_strategy(
            session_id, account_id, symbol, market_info,
            risk_level, trading_mode, symbols, reason,
        )

'''
replace_block(
    "def _try_create_from_template(self",
    "def _evaluate_dynamic_risk(self",
    create_shim,
    "strategy_creation",
)

# 3) symbol risk: from evaluate_dynamic through check_global (before analyst)
risk_shim = '''    def _evaluate_dynamic_risk(self, session, market_summary: Dict[str, Any]):
        from backend.services.full_auto.symbol_risk import (
            build_symbol_risk_host,
            evaluate_dynamic_risk,
        )
        evaluate_dynamic_risk(session, market_summary, build_symbol_risk_host(self))

    # per-symbol 风控结果类型（兼容旧引用）
    from backend.services.full_auto.symbol_risk import PerSymbolRiskResult as _PerSymbolRiskResult

    def _update_symbol_daily_pnl(self, db: Session, session):
        from backend.services.full_auto.symbol_risk import (
            build_symbol_risk_host,
            update_symbol_daily_pnl,
        )
        host = build_symbol_risk_host(self)
        update_symbol_daily_pnl(db, session, host)
        self._symbol_daily_pnl = host.symbol_daily_pnl

    def _freeze_symbol_strategies(self, db: Session, session, symbol: str, reason: str):
        from backend.services.full_auto.symbol_risk import (
            build_symbol_risk_host,
            freeze_symbol_strategies,
        )
        host = build_symbol_risk_host(self)
        freeze_symbol_strategies(db, session, symbol, reason, host)
        self._frozen_symbols = host.frozen_symbols

    def _unfreeze_recovered_symbols(self, db: Session, session, still_frozen: List[str]):
        from backend.services.full_auto.symbol_risk import (
            build_symbol_risk_host,
            unfreeze_recovered_symbols,
        )
        host = build_symbol_risk_host(self)
        unfreeze_recovered_symbols(db, session, still_frozen, host)
        self._frozen_symbols = host.frozen_symbols

    def _check_per_symbol_risk(self, db: Session, session) -> '_PerSymbolRiskResult':
        from backend.services.full_auto.symbol_risk import (
            build_symbol_risk_host,
            check_per_symbol_risk,
        )
        return check_per_symbol_risk(db, session, build_symbol_risk_host(self))

    def _check_global_risk(self, db: Session, session) -> Optional[str]:
        from backend.services.full_auto.symbol_risk import (
            build_symbol_risk_host,
            check_global_risk,
        )
        host = build_symbol_risk_host(self)
        result = check_global_risk(db, session, host)
        self._defensive_entered_at = host.defensive_entered_at
        return result

'''
replace_block(
    "def _evaluate_dynamic_risk(self",
    "def _run_analyst_system(self",
    risk_shim,
    "symbol_risk",
)

path.write_text("".join(lines), encoding="utf-8")
print("shim done")
