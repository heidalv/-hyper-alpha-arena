import io, sys, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
from sqlalchemy import text
from backend.core.tenant import system_identity
from backend.database.connection import SessionLocal
from collections import Counter

with system_identity():
    with SessionLocal() as db:
        rows = db.execute(text(
            "SELECT ts, symbol, meta_json FROM lane_ledger "
            "WHERE lane_id='mm_asterdex' AND event='fill' "
            "AND ts >= CAST('2026-09-15T14:05:00+00:00' AS timestamptz)"
        )).mappings().all()
        sig = Counter()
        for r in rows:
            m = r["meta_json"] or {}
            if isinstance(m, str):
                m = json.loads(m)
            sig[(str(r["ts"]), r["symbol"],
                 round(float(m.get("qty") or 0), 6),
                 round(float(m.get("fill_px") or 0), 6),
                 str(m.get("side") or ""))] += 1
        dups = {k: v for k, v in sig.items() if v > 1}
        print(f"时代内成交 {len(rows)} 笔，重复签名 {len(dups)} 组")
        for k, v in list(dups.items())[:10]:
            print(f"  dup×{v}: {k}")
