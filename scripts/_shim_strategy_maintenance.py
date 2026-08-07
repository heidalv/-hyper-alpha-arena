"""Shim strategy maintenance public methods."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "backend/services/full_auto_trading_service.py"
lines = path.read_text(encoding="utf-8").splitlines(True)
start = next(i for i, l in enumerate(lines) if "def cleanup_stale_strategies(self" in l)
end = next(i for i, l in enumerate(lines) if i > start and "def _pause_all_strategies(self" in l)
shim = '''    def cleanup_stale_strategies(self, db: Session) -> dict:
        from backend.services.full_auto.strategy_maintenance import (
            build_strategy_maintenance_host,
            cleanup_stale_strategies,
        )
        return cleanup_stale_strategies(db, build_strategy_maintenance_host(self))

    def merge_duplicate_strategies(self, db: Session, session_id: str) -> dict:
        from backend.services.full_auto.strategy_maintenance import (
            build_strategy_maintenance_host,
            merge_duplicate_strategies,
        )
        return merge_duplicate_strategies(db, session_id, build_strategy_maintenance_host(self))

'''
path.write_text("".join(lines[:start] + [shim] + lines[end:]), encoding="utf-8")
print(f"shimmed strategy maintenance {start+1}-{end}")
