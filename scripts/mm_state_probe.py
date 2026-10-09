# -*- coding: utf-8 -*-
"""[probe] Current lane state: registry meta, recent ops_changes, ledger by symbol.

ASCII-only on purpose (PowerShell mangles non-ASCII sources; inline python with nested
quotes also breaks on this host). Read-only.
"""
from __future__ import annotations

import io
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from backend.core.tenant import system_identity  # noqa: E402
from backend.database.connection import SessionLocal  # noqa: E402

CST = timezone(timedelta(hours=8))


def main() -> int:
    t0 = datetime(2026, 9, 16, 13, 55, 0, tzinfo=CST)
    with system_identity():
        with SessionLocal() as db:
            meta = db.execute(text(
                "SELECT mode, status, meta_json FROM lane_registry WHERE lane_id='mm_asterdex'"
            )).mappings().first()
            if meta:
                m = meta["meta_json"]
                m = json.loads(m) if isinstance(m, str) else (m or {})
                print("registry: mode=%s status=%s" % (meta["mode"], meta["status"]))
                print("  symbols =", m.get("symbols"))
                p = m.get("params") or {}
                print("  w_base_bp =", p.get("w_base_bp"), " vol_pause_sigma =", p.get("vol_pause_sigma"),
                      " min_width_reduce_bp =", p.get("min_width_reduce_bp"))
                print("  replay_baseline =", (m.get("replay_baseline") or {}).get("vol_baseline_bp"))
                print("  evolution.prev_params =", (m.get("evolution") or {}).get("prev_params"))
                print("  evolution.reason =", str((m.get("evolution") or {}).get("reason"))[:160])
            try:
                # [fix] 上一次用 detail_json 列名导致整条事务失效（后续查询全 InFailedSqlTransaction）
                # ⇒ 先 SELECT * 探明列，再打印关键字段；每个查询独立 try + rollback。
                ops = db.execute(text(
                    "SELECT * FROM ops_changes WHERE lane_id='mm_asterdex'"
                    " ORDER BY ts DESC LIMIT 6")).mappings().all()
                print("ops_changes (last 6):")
                for o in ops:
                    d = dict(o)
                    keys = [k for k in ("ts", "op", "action", "reason", "detail", "payload",
                                        "before_json", "after_json") if k in d]
                    print("  ", {k: (str(d[k])[:140] if d[k] is not None else None) for k in keys})
            except Exception as e:
                print("ops_changes read failed:", str(e)[:200])
                try:
                    db.rollback()
                except Exception:
                    pass
            rows = db.execute(text(
                "SELECT symbol, COUNT(*) n, ROUND(SUM(notional)::numeric,2) notional,"
                " ROUND(SUM(points_usd)::numeric,4) pts, ROUND(AVG(net_bp)::numeric,2) net_bp"
                " FROM lane_ledger WHERE lane_id='mm_asterdex' AND ts >= :t"
                "   AND COALESCE((meta_json->>'source'),'') <> 'reconcile'"
                " GROUP BY 1 ORDER BY 2 DESC"), {"t": t0}).mappings().all()
            print("ledger since 13:55 by symbol:")
            for r in rows:
                print("  ", dict(r))
            tot = db.execute(text(
                "SELECT COUNT(*) n, ROUND(COALESCE(SUM(points_usd),0)::numeric,4) pts"
                " FROM lane_ledger WHERE lane_id='mm_asterdex' AND ts >= :t"
                "   AND COALESCE((meta_json->>'source'),'') <> 'reconcile'"), {"t": t0}).mappings().first()
            print("total since 13:55: fills=%s realized=%s" % (tot["n"], tot["pts"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
