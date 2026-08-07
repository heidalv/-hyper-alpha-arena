"""Extract hold-timeout + trend review from monolith."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "backend/services/full_auto_trading_service.py"
lines = path.read_text(encoding="utf-8").splitlines(True)
FA = ROOT / "backend/services/full_auto"


def extract(start_pat, end_pat, method):
    start = next(i for i, l in enumerate(lines) if start_pat in l)
    end = next(i for i, l in enumerate(lines) if i > start and end_pat in l)
    chunk = "".join(lines[start:end])
    m = re.search(r'"""[\s\S]*?"""\n(.*)', chunk.split(f"def {method}", 1)[1], re.DOTALL)
    if not m:
        raise SystemExit(f"no body for {method}")
    return m.group(1).rstrip() + "\n", start, end


def apply(text: str) -> str:
    text = re.sub(r"\bself\.", "host.", text)
    for attr in (
        "active_db_sessions", "last_hold_timeout_ai_review", "last_analyst_reports",
        "last_unified_snapshot", "TIER_PROTECTION",
    ):
        text = text.replace(f"host._{attr}", f"host.{attr}")
    for fn in (
        "get_trading_account_id", "append_event", "run_hold_timeout_ai_review",
        "active_exchange", "run_analyst_system", "run_trend_review",
        "get_account_risk_score", "clear_hold_timeout_queue_entry",
        "orch_payload_from_decision", "safe_commit",
    ):
        text = text.replace(f"host._{fn}", f"host.{fn}")
    # recursive call inside if_needed should use module function
    text = text.replace(
        "host.run_hold_timeout_ai_review(db, session, pending)",
        "run_hold_timeout_ai_review(db, session, pending, host)",
    )
    text = text.replace(
        "host.run_trend_review(db, session, acct, market_summary)",
        "run_trend_review(db, session, acct, market_summary, host)",
    )
    return text


if_body, _, _ = extract(
    "def _run_hold_timeout_ai_review_if_needed(",
    "def _run_hold_timeout_ai_review(",
    "_run_hold_timeout_ai_review_if_needed",
)
rev_body, _, _ = extract(
    "def _run_hold_timeout_ai_review(",
    "def _run_trend_review(",
    "_run_hold_timeout_ai_review",
)
trend_body, _, _ = extract(
    "def _run_trend_review(",
    "def _run_light_trading_cycle(",
    "_run_trend_review",
)

(FA / "_hold_if_body.tmp").write_text(apply(if_body), encoding="utf-8")
(FA / "_hold_rev_body.tmp").write_text(apply(rev_body), encoding="utf-8")
(FA / "_trend_rev_body.tmp").write_text(apply(trend_body), encoding="utf-8")
print(
    "extracted hold/trend",
    len(if_body.splitlines()),
    len(rev_body.splitlines()),
    len(trend_body.splitlines()),
)
