"""Shim _validate_ai_decisions; remove class constants."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "backend/services/full_auto_trading_service.py"
lines = path.read_text(encoding="utf-8").splitlines(True)
# include _VALID_* constants before method
start = next(i for i, l in enumerate(lines) if "_VALID_ACTIONS = {" in l)
end = next(i for i, l in enumerate(lines) if i > start and "def _register_qaa_agents(self" in l)
shim = '''    def _validate_ai_decisions(self, session, master_result: Dict,
                                session_symbols: List[str],
                                positions_list: List[Dict]) -> Dict:
        from backend.services.full_auto.ai_decision_audit import (
            build_ai_decision_audit_host,
            validate_ai_decisions,
        )
        return validate_ai_decisions(
            session, master_result, session_symbols, positions_list,
            build_ai_decision_audit_host(self),
        )

'''
path.write_text("".join(lines[:start] + [shim] + lines[end:]), encoding="utf-8")
print(f"shimmed validate lines {start+1}-{end}")
