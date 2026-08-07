"""Shim hold-timeout + trend review in monolith."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "backend/services/full_auto_trading_service.py"
lines = path.read_text(encoding="utf-8").splitlines(True)

start = next(i for i, l in enumerate(lines) if "def _run_hold_timeout_ai_review_if_needed(" in l)
end = next(i for i, l in enumerate(lines) if i > start and "def _run_light_trading_cycle(" in l)

shim = '''    def _run_hold_timeout_ai_review_if_needed(
        self, session_id: str, *, priority_expired: bool = False,
    ) -> None:
        from backend.services.full_auto.hold_timeout_trend_review import (
            build_hold_trend_review_host,
            run_hold_timeout_ai_review_if_needed,
        )
        host = build_hold_trend_review_host(self)
        run_hold_timeout_ai_review_if_needed(
            session_id, host, priority_expired=priority_expired,
        )
        self._last_hold_timeout_ai_review = host.last_hold_timeout_ai_review

    def _run_hold_timeout_ai_review(
        self, db: Session, session, pending: list,
    ) -> None:
        from backend.services.full_auto.hold_timeout_trend_review import (
            build_hold_trend_review_host,
            run_hold_timeout_ai_review,
        )
        run_hold_timeout_ai_review(
            db, session, pending, build_hold_trend_review_host(self),
        )

    def _run_trend_review(self, db, session, account_id, market_summary):
        from backend.services.full_auto.hold_timeout_trend_review import (
            build_hold_trend_review_host,
            run_trend_review,
        )
        run_trend_review(
            db, session, account_id, market_summary,
            build_hold_trend_review_host(self),
        )

'''

new_lines = lines[:start] + [shim] + lines[end:]
path.write_text("".join(new_lines), encoding="utf-8")
print(f"shimmed hold/trend: removed {end - start} lines")
