"""Replace _execute_defensive_* in monolith with thin shims."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "backend/services/full_auto_trading_service.py"
lines = path.read_text(encoding="utf-8").splitlines(True)
start = next(i for i, l in enumerate(lines) if "def _execute_defensive_analysis(self" in l)
end = next(i for i, l in enumerate(lines) if i > start and "def _execute_live_trade(self" in l)

shim = '''    def _execute_defensive_analysis(self, db: Session, session, market_summary: dict):
        from backend.services.full_auto.defensive_cycle import (
            build_defensive_host,
            run_defensive_analysis,
        )
        run_defensive_analysis(db, session, market_summary, build_defensive_host(self))

    def _execute_defensive_verdicts(self, db: Session, session, account_id: int,
                                     verdicts: list, positions_list: list):
        from backend.services.full_auto.defensive_cycle import (
            build_defensive_host,
            run_defensive_verdicts,
        )
        run_defensive_verdicts(db, session, account_id, verdicts, positions_list, build_defensive_host(self))

    def _rule_based_defensive(self, db: Session, session, positions_list: list, market_summary: dict):
        from backend.services.full_auto.defensive_cycle import (
            build_defensive_host,
            run_rule_based_defensive,
        )
        run_rule_based_defensive(db, session, positions_list, market_summary, build_defensive_host(self))

'''

path.write_text("".join(lines[:start] + [shim] + lines[end:]), encoding="utf-8")
print(f"replaced lines {start+1}-{end}")
