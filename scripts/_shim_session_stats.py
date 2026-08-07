"""Replace _update_session_stats in monolith with thin shim."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "backend/services/full_auto_trading_service.py"
lines = path.read_text(encoding="utf-8").splitlines(True)
start = next(i for i, l in enumerate(lines) if "def _update_session_stats(self" in l)
end = next(i for i, l in enumerate(lines) if i > start and "def cleanup_stale_strategies(self" in l)
shim = '''    def _update_session_stats(self, db: Session, session, active_ids: list):
        from backend.services.full_auto.session_stats import (
            build_session_stats_host,
            update_session_stats,
        )
        update_session_stats(db, session, active_ids, build_session_stats_host(self))

'''
path.write_text("".join(lines[:start] + [shim] + lines[end:]), encoding="utf-8")
print(f"replaced lines {start+1}-{end}")
