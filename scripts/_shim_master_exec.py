"""Replace _execute_master_decisions in monolith with thin shim."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "backend/services/full_auto_trading_service.py"
lines = path.read_text(encoding="utf-8").splitlines(True)
start = next(i for i, l in enumerate(lines) if "def _execute_master_decisions" in l)
end = next(i for i, l in enumerate(lines) if i > start and l.strip().startswith("def _validate_tp_sl_by_nature"))

shim = '''    def _execute_master_decisions(self, db: Session, session, account_id: int,
                                   decisions: List[Dict], positions_list: List[Dict],
                                   active_ids: list, market_summary: dict,
                                   mode: str, analyst_reports: dict = None,
                                   balance_info: dict = None,
                                   orch_directions: dict = None,
                                   strat_tier_map: dict = None):
        from backend.services.full_auto.master_execution import (
            build_master_execution_host,
            execute_master_decisions,
        )
        host = build_master_execution_host(self)
        execute_master_decisions(
            db, session, account_id, decisions, positions_list, active_ids,
            market_summary, mode, host,
            analyst_reports=analyst_reports,
            balance_info=balance_info,
            orch_directions=orch_directions,
            strat_tier_map=strat_tier_map,
        )
        self._current_decision_tier = getattr(host, "current_decision_tier", "")

'''

new_lines = lines[:start] + [shim] + lines[end:]
path.write_text("".join(new_lines), encoding="utf-8")
print(f"replaced lines {start+1}-{end} with shim ({end-start} -> {shim.count(chr(10))} lines)")
