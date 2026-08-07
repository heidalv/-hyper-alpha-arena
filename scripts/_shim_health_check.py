"""Replace _run_health_check in monolith with thin shim."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "backend/services/full_auto_trading_service.py"
lines = path.read_text(encoding="utf-8").splitlines(True)
start = next(i for i, l in enumerate(lines) if "def _run_health_check(self" in l)
end = next(i for i, l in enumerate(lines) if i > start and l.strip().startswith("def _sanitize_market_summary_for_qaa"))

shim = '''    def _run_health_check(self, session_id: str, *, maintenance_only: bool = False):
        from backend.services.full_auto.health_check_cycle import (
            build_health_check_host,
            run_health_check,
        )
        host = build_health_check_host(self)
        run_health_check(session_id, host, maintenance_only=maintenance_only)
        self._current_trace_id = host.current_trace_id
        FullAutoTradingService._current_trace_id = host.current_trace_id
        self._last_orch_decisions = host.last_orch_decisions
        self._last_orch_decisions_ts = host.last_orch_decisions_ts
        self._last_unified_snapshot = host.last_unified_snapshot

'''

new_lines = lines[:start] + [shim] + lines[end:]
path.write_text("".join(new_lines), encoding="utf-8")
print(f"replaced lines {start+1}-{end} with shim ({end-start} -> {shim.count(chr(10))} lines)")
