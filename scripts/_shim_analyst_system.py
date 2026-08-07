"""Replace _run_analyst_system* in monolith with thin shims."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "backend/services/full_auto_trading_service.py"
lines = path.read_text(encoding="utf-8").splitlines(True)
start = next(i for i, l in enumerate(lines) if "def _run_analyst_system(self" in l)
end = next(i for i, l in enumerate(lines) if i > start and l.strip().startswith("def _build_tier_protection"))

sync_block = '''        self._long_tier_staged_tp_state = host.long_tier_staged_tp_state
        self._pre_screen_results = host.pre_screen_results
        self._pre_screen_passed = host.pre_screen_passed
        self._mlto_handled_keys = host.mlto_handled_keys

'''

shim = '''    def _run_analyst_system(self, db: Session, session, active_ids: list, market_summary: dict):
        from backend.services.full_auto.analyst_system_cycle import (
            build_analyst_system_host,
            run_analyst_system,
        )
        host = build_analyst_system_host(self)
        run_analyst_system(db, session, active_ids, market_summary, host)
''' + sync_block + '''
    def _run_analyst_system_unified(self, db: Session, session, account, active_ids: list, market_summary: dict):
        from backend.services.full_auto.analyst_system_cycle import (
            build_analyst_system_host,
            run_analyst_system_unified,
        )
        host = build_analyst_system_host(self)
        run_analyst_system_unified(db, session, account, active_ids, market_summary, host)
''' + sync_block

new_lines = lines[:start] + [shim] + lines[end:]
path.write_text("".join(new_lines), encoding="utf-8")
print(f"replaced lines {start+1}-{end} with shim ({end-start} -> {shim.count(chr(10))} lines)")
