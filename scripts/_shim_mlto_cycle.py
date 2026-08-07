"""Shim MLTO methods in monolith."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "backend/services/full_auto_trading_service.py"
lines = path.read_text(encoding="utf-8").splitlines(True)

start = next(i for i, l in enumerate(lines) if "def _maintain_mlto_theses_for_session(" in l)
end = next(i for i, l in enumerate(lines) if i > start and "def _build_midlong_agent_envelope(" in l)

shim = '''    def _maintain_mlto_theses_for_session(
        self,
        *,
        session,
        market_summary: dict,
        analyst_reports: dict,
        mode: str,
        portfolio: dict,
        symbols_batch: Optional[List[str]] = None,
        run_mid: bool = True,
        run_long: bool = True,
        light_context: bool = False,
    ) -> None:
        from backend.services.full_auto.mlto_cycle import (
            build_mlto_cycle_host,
            maintain_mlto_theses_for_session,
        )
        host = build_mlto_cycle_host(self)
        maintain_mlto_theses_for_session(
            session=session,
            market_summary=market_summary,
            analyst_reports=analyst_reports,
            mode=mode,
            portfolio=portfolio,
            host=host,
            symbols_batch=symbols_batch,
            run_mid=run_mid,
            run_long=run_long,
            light_context=light_context,
        )
        self._mlto_handled_keys = host.mlto_handled_keys
        self._mlto_handled_lock = host.mlto_handled_lock

    def _execute_mlto_lane(
        self,
        *,
        sym: str,
        dec: dict,
        tier: str,
        agent_source: str,
        market_summary: dict,
        analyst_reports: dict,
        db,
        session,
        mode: str,
        portfolio: dict,
    ) -> tuple:
        from backend.services.full_auto.mlto_cycle import (
            build_mlto_cycle_host,
            execute_mlto_lane,
        )
        host = build_mlto_cycle_host(self)
        result = execute_mlto_lane(
            sym=sym,
            dec=dec,
            tier=tier,
            agent_source=agent_source,
            market_summary=market_summary,
            analyst_reports=analyst_reports,
            db=db,
            session=session,
            mode=mode,
            portfolio=portfolio,
            host=host,
        )
        self._mlto_handled_keys = host.mlto_handled_keys
        self._mlto_handled_lock = host.mlto_handled_lock
        return result

'''

new_lines = lines[:start] + [shim] + lines[end:]
path.write_text("".join(new_lines), encoding="utf-8")
print(f"shimmed MLTO: removed {end - start} lines")
