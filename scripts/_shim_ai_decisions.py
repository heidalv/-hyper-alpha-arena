"""Replace _execute_ai_decisions in monolith with thin shim."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "backend/services/full_auto_trading_service.py"
lines = path.read_text(encoding="utf-8").splitlines(True)
start = next(i for i, l in enumerate(lines) if "def _execute_ai_decisions(self" in l)
end = next(i for i, l in enumerate(lines) if i > start and l.strip().startswith("def _is_unified_executor_on(self"))

shim = '''    def _execute_ai_decisions(self, db: Session, session, active_ids: list,
                              market_data: dict):
        from backend.services.full_auto.ai_decisions import (
            build_ai_decisions_host,
            execute_ai_decisions,
        )
        host = build_ai_decisions_host(self)
        execute_ai_decisions(db, session, active_ids, market_data, host)

'''

new_lines = lines[:start] + [shim] + lines[end:]
path.write_text("".join(new_lines), encoding="utf-8")
print(f"replaced lines {start+1}-{end} with shim ({end-start} -> {shim.count(chr(10))} lines)")
