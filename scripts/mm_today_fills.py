import io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
from backend.services import lane_ledger

SINCE = "2026-09-16T00:00:00+00:00"

att = lane_ledger.attribution(days=3, lane_id="mm_asterdex", since=SINCE)
print("== 今日(UTC 09-16) 六维归因 ==")
t = att.get("total") or {}
print(f"  n={t.get('n')} notional={t.get('notional')}")
print(f"  spread_bp={t.get('spread_bp')}  price_bp={t.get('price_bp')}  fee_bp={t.get('fee_bp')}  net_bp={t.get('net_bp')}  net_usd={t.get('net_usd')}")
print("  逐币:")
for s in att.get("by_symbol") or []:
    print(f"    {s['symbol']:5s} n={s['n']:3d} notional={s['notional']:9.2f} "
          f"spread={s['spread_bp']:+7.2f}bp price={s['price_bp']:+8.2f}bp net={s['net_usd']:+8.4f}")

# 原始行：看 XRP/SOL 的逐笔序列
from sqlalchemy import text
from backend.core.tenant import system_identity
from backend.database.connection import SessionLocal

with system_identity():
    with SessionLocal() as db:
        rows = db.execute(text(
            "SELECT ts, symbol, meta_json, spread_bp, price_bp, net_bp, notional "
            "FROM lane_ledger WHERE lane_id='mm_asterdex' AND event='fill' "
            "AND ts >= CAST(:s AS timestamptz) ORDER BY ts"
        ), {"s": SINCE}).mappings().all()

print(f"\n== 今日逐笔明细 ({len(rows)} 笔) ==")
import json
for r in rows:
    m = r["meta_json"] or {}
    if isinstance(m, str):
        m = json.loads(m)
    side = str(m.get("side", "?")).ljust(4)
    qty = float(m.get("qty") or 0)
    px = float(m.get("fill_px") or 0)
    flat = bool(m.get("flatten"))
    print(f"  {str(r['ts'])[11:19]} {r['symbol']:5s} {side} qty={qty:+12.6f} px={px:<12.6g} "
          f"spread={r['spread_bp']:+7.2f}bp price={r['price_bp']:+8.2f}bp net={r['net_bp']:+7.2f}bp "
          f"{'[平仓]' if flat else ''}")
