"""Diagnose why scalp/short-tier trades stopped."""
from __future__ import annotations

import json
import sys
import urllib.request
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "scalp_status_latest.txt"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SESSION = "fa_e55efe8e92"


def say(lines: list, text: str = "") -> None:
    lines.append(text)


def main() -> None:
    lines: list[str] = []
    say(lines, "=== scalp/short diagnostic ===")

    # API status
    with urllib.request.urlopen(
        f"http://127.0.0.1:8000/api/full-auto/status/{SESSION}", timeout=60
    ) as r:
        status = json.load(r)
    events = status.get("recent_events") or []
    say(lines, f"recent_events={len(events)}")

    ctr = Counter(e.get("event") for e in events)
    for k, v in ctr.most_common(15):
        say(lines, f"  event {k}: {v}")

    scalp_ev = [e for e in events if "scalp" in str(e.get("event", "")).lower()
                or "短线" in str(e.get("detail", ""))
                or "ScalpRouter" in str(e.get("detail", ""))]
    say(lines, f"\nscalp-related events in recent {len(events)}: {len(scalp_ev)}")
    for e in scalp_ev[-12:]:
        say(lines, f"  [{e.get('time','')}] {e.get('event')}: {str(e.get('detail',''))[:180]}")

    blocks = [e for e in events if any(x in str(e.get("detail", "")) for x in
              ("门控", "拦截", "block", "V5", "short/scalp", "trade_error", "trade_failed"))]
    say(lines, f"\nblock/error events: {len(blocks)}")
    for e in blocks[-10:]:
        say(lines, f"  [{e.get('time','')}] {e.get('event')}: {str(e.get('detail',''))[:180]}")

    # DB positions & strategies
    from backend.database.connection import SessionLocal
    from backend.database.models import FullAutoSession, PaperPosition, AIStrategy

    db = SessionLocal()
    sess = db.query(FullAutoSession).filter(FullAutoSession.session_id == SESSION).first()
    acct = sess.paper_account_id or sess.account_id
    say(lines, f"\nsession symbols={sess.symbols}")
    say(lines, f"auto_coin={sess.auto_coin_symbols} risk={sess.risk_level}")

    short_pos = db.query(PaperPosition).filter(
        PaperPosition.account_id == acct,
        PaperPosition.status == "open",
    ).all()
    say(lines, f"\nopen positions total={len(short_pos)}")
    for p in short_pos:
        tier = getattr(p, "timeframe_tier", None) or "?"
        nature = getattr(p, "trade_nature", None) or "?"
        say(lines, f"  {p.symbol} {p.side} tier={tier} nature={nature} pnl={getattr(p,'unrealized_pnl',0)}")

    short_only = [p for p in short_pos if (getattr(p, "timeframe_tier", "") or "").lower() == "short"
                  or (getattr(p, "trade_nature", "") or "").lower() == "scalp"]
    say(lines, f"short/scalp open positions={len(short_only)}")

    strats = db.query(AIStrategy).filter(
        AIStrategy.account_id == sess.account_id,
        AIStrategy.status.in_(["active", "paused"]),
    ).all()
    by_tier = Counter((getattr(s, "timeframe_tier", None) or "?") for s in strats)
    say(lines, f"\nactive/paused strategies={len(strats)} by_tier={dict(by_tier)}")

    # settings
    from backend.config import settings as cfg
    say(lines, "\n=== key scalp settings ===")
    say(lines, f"  V5_SCALP_MIN_CONFIDENCE={cfg.V5_SCALP_MIN_CONFIDENCE}")
    say(lines, f"  SHORT_TIER_CONFIDENCE_EXTRA={cfg.SHORT_TIER_CONFIDENCE_EXTRA}")
    say(lines, f"  SHORT_TIER_SAME_DIR_COOLDOWN_S={cfg.SHORT_TIER_SAME_DIR_COOLDOWN_S}")
    say(lines, f"  SCALP_EXECUTION_LANE_ENABLED={cfg.SCALP_EXECUTION_LANE_ENABLED}")
    say(lines, f"  SCALP_FACTOR_EXECUTE_THRESHOLD={cfg.SCALP_FACTOR_EXECUTE_THRESHOLD}")
    say(lines, f"  SHORT_TIER_DISABLED_NATURES={cfg.SHORT_TIER_DISABLED_NATURES!r}")

    try:
        import os
        say(lines, f"  ENABLE_ORCHESTRATOR_OVERRIDE={os.getenv('ENABLE_ORCHESTRATOR_OVERRIDE')}")
    except Exception:
        pass

    # recent trades with short tier from event log in DB
    if sess.event_log:
        evs = sess.event_log
        trades = [e for e in evs if e.get("event") in ("trade_executed", "trade_success", "paper_trade")
                  or "成交" in str(e.get("detail", ""))]
        scalp_trades = [e for e in evs if "scalp" in str(e.get("detail", "")).lower()
                        or "短线" in str(e.get("detail", ""))]
        say(lines, f"\nDB event_log total={len(evs)} scalp mentions={len(scalp_trades)}")
        for e in scalp_trades[-8:]:
            say(lines, f"  [{e.get('time','')}] {str(e.get('detail',''))[:160]}")

    db.close()
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(OUT)


if __name__ == "__main__":
    main()
