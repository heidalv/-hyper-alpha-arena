"""Shim _evaluate_and_execute_proposal in monolith."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "backend/services/full_auto_trading_service.py"
lines = path.read_text(encoding="utf-8").splitlines(True)

start = next(i for i, l in enumerate(lines) if "def _evaluate_and_execute_proposal(" in l)
end = next(i for i, l in enumerate(lines) if i > start and "def _try_execute_independent_agent_open(" in l)

shim = '''    def _evaluate_and_execute_proposal(
        self,
        *,
        db: Session,
        session,
        proposal,
        market_summary: dict,
        session_mode: str = "running",
        strat=None,
    ) -> bool:
        from backend.services.full_auto.proposal_execution import (
            build_proposal_execution_host,
            evaluate_and_execute_proposal,
        )
        return evaluate_and_execute_proposal(
            db=db,
            session=session,
            proposal=proposal,
            market_summary=market_summary,
            host=build_proposal_execution_host(self),
            session_mode=session_mode,
            strat=strat,
        )

'''

new_lines = lines[:start] + [shim] + lines[end:]
path.write_text("".join(new_lines), encoding="utf-8")
print(f"shimmed proposal exec: removed {end - start} lines")
