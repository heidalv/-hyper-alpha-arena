"""Shim market scan, qaa v3 tick, forced logs."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "backend/services/full_auto_trading_service.py"
lines = path.read_text(encoding="utf-8").splitlines(True)

sync_ms = '''        self._market_scan_cache = host.market_scan_cache
        self._market_scan_cache_ts = host.market_scan_cache_ts
        self._bg_scan_running = host.bg_scan_running

'''

ms_shim = '''    def _bg_market_scan(self, symbols: List[str]):
        from backend.services.full_auto.market_scan_cycle import (
            build_market_scan_host,
            run_bg_market_scan,
        )
        host = build_market_scan_host(self)
        run_bg_market_scan(symbols, host)
''' + sync_ms + '''
    def _scan_markets(self, db: Session, symbols: List[str]) -> Dict[str, Any]:
        from backend.services.full_auto.market_scan_cycle import (
            build_market_scan_host,
            run_scan_markets,
        )
        host = build_market_scan_host(self)
        result = run_scan_markets(db, symbols, host)
''' + sync_ms + '''        return result

'''

tick_start = next(i for i, l in enumerate(lines) if "def _run_qaa_v3_tick(self" in l)
tick_end = next(i for i, l in enumerate(lines) if i > tick_start and "def _run_analyst_system_v3(" in l)
tick_shim = '''    def _run_qaa_v3_tick(self, session_id: str):
        from backend.services.full_auto.qaa_v3_tick_cycle import (
            build_qaa_v3_tick_host,
            run_qaa_v3_tick,
        )
        host = build_qaa_v3_tick_host(self)
        run_qaa_v3_tick(session_id, host)
        self._market_scan_cache = host.market_scan_cache
        self._market_scan_cache_ts = host.market_scan_cache_ts
        self._active_positions_cache = host.active_positions_cache
        self._pre_screen_results = host.pre_screen_results
        self._pre_screen_passed = host.pre_screen_passed
        self._qaa_ctx = host.qaa_ctx

'''

logs_start = next(i for i, l in enumerate(lines) if "def _write_qaa_v3_forced_decision_logs(" in l)
logs_end = next(i for i, l in enumerate(lines) if i > logs_start and "full_auto_service =" in l)
logs_shim = '''    def _write_qaa_v3_forced_decision_logs(
        self,
        *,
        session_orm_id: int,
        account_id: int,
        decisions: list,
        balance_info: dict,
        positions_list: list,
        market_summary: dict,
    ) -> None:
        from backend.services.full_auto.qaa_v3_forced_logs import write_qaa_v3_forced_decision_logs
        write_qaa_v3_forced_decision_logs(
            session_orm_id=session_orm_id,
            account_id=account_id,
            decisions=decisions,
            balance_info=balance_info,
            positions_list=positions_list,
            market_summary=market_summary,
        )

'''

ms_start = next(i for i, l in enumerate(lines) if "def _bg_market_scan(self" in l)
ms_end = next(i for i, l in enumerate(lines) if i > ms_start and "def _is_champion_strategy(self" in l)

# Apply in reverse order of file position to keep indices valid - do one write
new_lines = (
    lines[:ms_start] + [ms_shim]
    + lines[ms_end:tick_start] + [tick_shim]
    + lines[tick_end:logs_start] + [logs_shim]
    + lines[logs_end:]
)
path.write_text("".join(new_lines), encoding="utf-8")
print("shimmed market scan, qaa v3 tick, forced logs")
