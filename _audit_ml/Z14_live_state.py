# -*- coding: utf-8 -*-
"""Z14：观察期线上核验（读日志尾部 + DB 实时状态）。"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sqlalchemy import create_engine, text  # noqa: E402

URL = os.getenv("DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")

LOG = ROOT / "logs" / "backend-console.log"
if LOG.exists():
    size = LOG.stat().st_size
    with LOG.open("rb") as f:
        f.seek(max(0, size - 3_000_000))
        tail = f.read().decode("utf-8", "ignore").splitlines()
    pats = ("learned_block", "long_learned", "midlong_long", "EXIT_POLICY_MID_TRAILING",
            "circuit_gate", "long_mode")
    hits = [ln for ln in tail if any(p in ln for p in pats)]
    print(f"=== 日志尾部 {len(tail)} 行（{size/1e6:.1f}MB 文件），命中 {len(hits)} 条 ===")
    for ln in hits[-15:]:
        print("  " + ln[:220])
    print("\n=== 日志最后 6 行 ===")
    for ln in tail[-6:]:
        print("  " + ln[:220])
else:
    print("无 backend-console.log")

eng = create_engine(URL)
with eng.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    print("\n=== 当前 open 持仓（含策略快照）===")
    rows = [dict(r._mapping) for r in c.execute(text("""
        select id, symbol, timeframe_tier, trade_nature, side, entry_price, sl_price, size,
               leverage, opened_at, peak_pnl_pct, unrealized_pnl, partial_realized_pnl,
               partial_fee_paid, exit_state_json
        from paper_positions where status='open' order by opened_at
    """)).fetchall()]
    for r in rows:
        es = r["exit_state_json"] or {}
        if isinstance(es, str):
            import json
            try:
                es = json.loads(es)
            except Exception:
                es = {}
        pol = (es.get("exit_policy") or {}) if isinstance(es, dict) else {}
        usd = (float(r["unrealized_pnl"] or 0) + float(r["partial_realized_pnl"] or 0)
               - float(r["partial_fee_paid"] or 0))
        print(f"  #{r['id']} {r['symbol']:<9}{r['timeframe_tier']:<5}{r['trade_nature'] or '':<13}"
              f"entry={r['entry_price']:<12.6g} sl={r['sl_price']:<12.6g} "
              f"lev={r['leverage']} peak={float(r['peak_pnl_pct'] or 0)*100:>6.2f}% "
              f"USD={usd:>+8.2f}  opened={str(r['opened_at'])[:16]}")
        if pol:
            print(f"        policy: trail_act={pol.get('trailing_activation_pct')} "
                  f"trail_cb={pol.get('trailing_callback_pct')}")

    print("\n=== 9/10 00:59 重启后新开仓 ===")
    for r in c.execute(text("""
        select id, symbol, timeframe_tier, trade_nature, opened_at, status
        from paper_positions where opened_at >= '2026-09-10 00:59:00+08'
        order by opened_at
    """)).fetchall():
        print(f"  #{r[0]} {r[1]:<9}{r[2]:<5}{r[3] or '':<13}{str(r[4])[:19]} {r[5]}")

    print("\n=== 9/10 重启后新平仓 ===")
    for r in c.execute(text("""
        select id, symbol, timeframe_tier, close_reason, peak_pnl_pct, unrealized_pnl,
               partial_realized_pnl, partial_fee_paid, closed_at
        from paper_positions where closed_at >= '2026-09-10 00:59:00+08'
        order by closed_at
    """)).fetchall():
        usd = float(r[5] or 0) + float(r[6] or 0) - float(r[7] or 0)
        print(f"  #{r[0]} {r[1]:<9}{r[2]:<5}peak={float(r[4] or 0)*100:>6.2f}% "
              f"USD={usd:>+8.2f} {str(r[3])[:30]} {str(r[8])[:19]}")

    print("\n=== 账户权益 ===")
    for r in c.execute(text("""
        select id, total_equity, available_balance from paper_balances order by id limit 5
    """)).fetchall():
        print(f"  acct={r[0]} equity={r[1]} avail={r[2]}")
