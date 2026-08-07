"""Shim _run_quick_orchestrator_eval in monolith."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "backend/services/full_auto_trading_service.py"
lines = path.read_text(encoding="utf-8").splitlines(True)

start = next(i for i, l in enumerate(lines) if "def _run_quick_orchestrator_eval(self" in l)
end = next(
    i for i, l in enumerate(lines)
    if i > start and "# ══════════════════════════════════════════════════" in l
    and "工具" in "".join(lines[i:i + 3])
)

shim = '''    def _run_quick_orchestrator_eval(self, session_id: str):
        from backend.services.full_auto.quick_orchestrator_eval import (
            build_quick_orch_host,
            run_quick_orchestrator_eval,
        )
        host = build_quick_orch_host(self)
        run_quick_orchestrator_eval(session_id, host)
        self._deadlock_rescue_count = host.deadlock_rescue_count

'''

new_lines = lines[:start] + [shim] + lines[end:]
path.write_text("".join(new_lines), encoding="utf-8")
print(f"shimmed quick orch: removed {end - start} lines")
