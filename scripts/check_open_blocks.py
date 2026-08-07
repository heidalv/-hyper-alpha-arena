"""Check recent full-auto events for wrongly blocked open signals."""
from __future__ import annotations

import json
import re
import sys
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "open_block_check_latest.txt"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SESSION_ID = "fa_e55efe8e92"
BASE = "http://127.0.0.1:8000"


def fetch(path: str) -> dict:
    with urllib.request.urlopen(f"{BASE}{path}", timeout=60) as r:
        return json.load(r)


def main() -> None:
    lines: list[str] = []

    def say(text: str = "") -> None:
        lines.append(text)

    status = fetch(f"/api/full-auto/status/{SESSION_ID}")
    events = status.get("recent_events") or []
    say(f"generated_at={datetime.now(timezone.utc).isoformat()}")
    say(f"session={SESSION_ID} recent_events={len(events)}")

    def etype(e: dict) -> str:
        return str(e.get("event") or e.get("type") or "unknown")

    def etext(e: dict) -> str:
        return str(e.get("detail") or e.get("message") or "")

    ctr = Counter(etype(e) for e in events)
    say("\n=== event types (top 25) ===")
    for k, v in ctr.most_common(25):
        say(f"  {k}: {v}")

    open_block_re = re.compile(
        r"gate_block|reopen|deferred|fact_guard|persistence|budget_block|"
        r"universe_block|pace_|layer_budget|consistency|direction_gate|"
        r"data_gate|legacy_gate|rebound_gate|pyramid_gate|dca_blocked|"
        r"training_universe|frozen_block|拦截",
        re.I,
    )
    close_re = re.compile(
        r"close_blocked|reduce_blocked|master_close|UnifiedExit|trend_review|"
        r"master_running|浮亏不足|profit|sl_proximity",
        re.I,
    )

    open_blocks: list[tuple] = []
    close_blocks: list[tuple] = []
    overrides: list[tuple] = []
    for e in events:
        t = etype(e)
        txt = etext(e)
        combo = f"{t} {txt}"
        if t == "orchestrator_override":
            overrides.append((e.get("time", ""), txt))
        if open_block_re.search(combo) and not close_re.search(combo):
            open_blocks.append((e.get("time", ""), t, txt))
        if close_re.search(combo):
            close_blocks.append((e.get("time", ""), t, txt))

    say(f"\n=== orchestrator_override (NOT blocks, forced opens): {len(overrides)} ===")
    for ts, txt in overrides:
        say(f"  [{ts}] {txt[:220]}")

    say(f"\n=== OPEN gate blocks in recent {len(events)} events: {len(open_blocks)} ===")
    for ts, t, txt in open_blocks:
        say(f"  [{ts}] {t}: {txt[:220]}")

    say(f"\n=== CLOSE/exit blocks in recent {len(events)} events: {len(close_blocks)} ===")
    for ts, t, txt in close_blocks[-10:]:
        say(f"  [{ts}] {t}: {txt[:220]}")

    # DB event_log
    try:
        from backend.database.connection import SessionLocal
        from backend.database.models import FullAutoSession

        db = SessionLocal()
        sess = db.query(FullAutoSession).filter(
            FullAutoSession.session_id == SESSION_ID
        ).first()
        if sess and sess.event_log:
            all_ev = sess.event_log
            say(f"\n=== DB event_log total: {len(all_ev)} ===")
            db_ctr = Counter(etype(e) for e in all_ev[-2000:])
            say("last 2000 event types:")
            for k, v in db_ctr.most_common(20):
                say(f"  {k}: {v}")
            db_open = []
            for e in all_ev[-2000:]:
                t = etype(e)
                txt = etext(e)
                combo = f"{t} {txt}"
                if open_block_re.search(combo) and not close_re.search(combo):
                    db_open.append((e.get("time", ""), t, txt))
            say(f"\nopen gate blocks in last 2000 DB events: {len(db_open)}")
            for ts, t, txt in db_open[-20:]:
                say(f"  [{ts}] {t}: {txt[:220]}")
        else:
            say("\nDB event_log empty or missing")
        db.close()
    except Exception as exc:
        say(f"\nDB scan skipped: {exc}")

    # risk_control_events table
    try:
        from backend.database.connection import SessionLocal
        from sqlalchemy import text

        db = SessionLocal()
        rows = db.execute(text(
            "SELECT created_at, event_type, symbol, detail FROM risk_control_events "
            "WHERE created_at >= datetime('now', '-24 hours') "
            "AND (event_type LIKE '%block%' OR event_type LIKE '%gate%' OR detail LIKE '%拦截%') "
            "ORDER BY created_at DESC LIMIT 30"
        )).fetchall()
        say(f"\n=== risk_control_events last 24h gate/block rows: {len(rows)} ===")
        for r in rows:
            say(f"  [{r[0]}] {r[1]} {r[2]}: {str(r[3])[:180]}")
        db.close()
    except Exception as exc:
        say(f"\nrisk_control_events scan: {exc}")

    # Settings + module activation
    try:
        from backend.config import settings as s
        from backend.services.unified_exit_executor import unified_exit_executor

        say("\n=== backend code activation ===")
        say(f"  UNIFIED_EXIT_EXECUTOR_ENABLED={s.UNIFIED_EXIT_EXECUTOR_ENABLED}")
        say(f"  RISK_P3_MASTER_CLOSE_REQUIRES_HARDFACT={getattr(s, 'RISK_P3_MASTER_CLOSE_REQUIRES_HARDFACT', 'n/a')}")
        say(f"  short min_hold_emergency_loss_pct={s.TIER_PROTECTION_PARAMS['short'].get('min_hold_emergency_loss_pct')}")
        say(f"  unified_exit_executor loaded={unified_exit_executor is not None}")
    except Exception as exc:
        say(f"settings import failed: {exc}")

    # Frontend
    try:
        with urllib.request.urlopen(BASE + "/", timeout=10) as r:
            html = r.read().decode("utf-8", errors="ignore")
        if "Force light theme" in html:
            say("\n=== frontend :8000 === OLD bundle (forces light, dark mode NOT deployed)")
        elif "hyper-alpha-arena-theme" in html:
            say("\n=== frontend :8000 === NEW dark-mode index.html deployed")
    except Exception as exc:
        say(f"frontend check failed: {exc}")

    src_index = ROOT / "frontend" / "index.html"
    dist_index = ROOT / "frontend" / "dist" / "index.html"
    if src_index.exists():
        say(f"  source index.html mtime={src_index.stat().st_mtime}")
    if dist_index.exists():
        say(f"  dist index.html mtime={dist_index.stat().st_mtime}")

    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(str(OUT))


if __name__ == "__main__":
    main()
