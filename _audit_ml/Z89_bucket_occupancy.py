import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena"); sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv; load_dotenv(ROOT/".env")
from sqlalchemy import text
from backend.database.connection import SessionLocal
db = SessionLocal()
try:
    for st, look in (("swing_agent_score", 45), ("trend_agent_score", 45)):
        cut = datetime.now(timezone.utc) - timedelta(days=look)
        rows = db.execute(text("select signal_value from signal_trade_feedback where signal_type=:s and created_at >= :c and trade_pnl is not null"), {"s": st, "c": cut.replace(tzinfo=None)}).fetchall()
        vals = [float(r[0] or 0) for r in rows]
        buckets = {}
        for v in vals:
            b = int(v // 10) * 10
            buckets[b] = buckets.get(b, 0) + 1
        good = {k: v for k, v in buckets.items() if v >= 8}
        print(f"{st}: 有效样本 {len(vals)}  桶分布 {dict(sorted(buckets.items()))}  ≥8 的桶数 {len(good)}  -> is_calibrated={len(good)>=2}")
finally:
    db.close()
