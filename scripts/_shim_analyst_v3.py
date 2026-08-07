"""Shim _run_analyst_system_v3."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "backend/services/full_auto_trading_service.py"
lines = path.read_text(encoding="utf-8").splitlines(True)
start = next(i for i, l in enumerate(lines) if "def _run_analyst_system_v3(" in l)
end = next(i for i, l in enumerate(lines) if i > start and "def _write_qaa_v3_forced_decision_logs(" in l)
shim = '''    def _run_analyst_system_v3(
        self,
        session_id: str,
        session_status: str,
        session_orm_id: int,
        account_id: int,
        active_ids: list,
        market_summary: dict,
    ):
        from backend.services.full_auto.analyst_system_v3_cycle import (
            build_analyst_v3_host,
            run_analyst_system_v3,
        )
        host = build_analyst_v3_host(self)
        run_analyst_system_v3(
            session_id, session_status, session_orm_id, account_id,
            active_ids, market_summary, host,
        )
        self._mlto_handled_keys = host.mlto_handled_keys

'''
path.write_text("".join(lines[:start] + [shim] + lines[end:]), encoding="utf-8")
print(f"shimmed analyst v3 {start+1}-{end}")
