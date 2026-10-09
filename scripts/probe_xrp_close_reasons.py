# -*- coding: utf-8 -*-
"""XRP 两笔平仓的原因（解释 30 分钟 vs 120 分钟冷却）。只读。"""
from __future__ import annotations

import io
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import text  # noqa: E402
from backend.database.connection import SessionLocal  # noqa: E402

SQL = """
SELECT opened_at, closed_at, side, entry_price, close_price, close_reason,
       round(coalesce(partial_realized_pnl,0)::numeric, 4) AS pnl, size
FROM paper_positions
WHERE timeframe_tier = 'mid' AND symbol = 'XRP'
  AND opened_at >= now() - interval '2 days'
ORDER BY opened_at DESC
LIMIT 10
"""

db = SessionLocal()
try:
    db.execute(text("SET app.is_admin='on'"))
    print("=== XRP tier=mid 近 2 天持仓明细 ===")
    for r in db.execute(text(SQL)).fetchall():
        dur = ""
        try:
            dur = f"{(r[1] - r[0]).total_seconds()/60:.1f} 分钟"
        except Exception:  # noqa: BLE001
            pass
        print(f"\n  open={r[0]}  close={r[1]}  时长={dur}  side={r[2]}")
        print(f"    entry={r[3]}  close_px={r[4]}  pnl={r[6]}  size={r[7]}")
        print(f"    close_reason={r[5]!r}")
finally:
    db.close()

env = (ROOT / ".env").read_text(encoding="utf-8", errors="replace")
print("\n=== .env 中冷却/独立闸相关键 ===")
for ln in env.splitlines():
    s = ln.strip()
    if s.startswith(("REENTRY_", "MIDLONG_INDEPENDENT_COOLDOWN", "MIDLONG_SL_CAP",
                     "MIDLONG_LOCATION")):
        print("  " + s)
