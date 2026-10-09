import io, sys, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
from sqlalchemy import text
from backend.core.tenant import system_identity
from backend.database.connection import SessionLocal

SINCE = "2026-09-15T22:05:23+08:00"  # 时代起点 stats_since

with system_identity():
    with SessionLocal() as db:
        rows = db.execute(text(
            "SELECT ts AT TIME ZONE 'Asia/Shanghai' AS ts_bj, symbol, "
            "spread_bp, price_bp, net_bp, notional, meta_json "
            "FROM lane_ledger WHERE lane_id='mm_asterdex' AND event='fill' "
            "AND ts >= CAST(:s AS timestamptz) ORDER BY ts"
        ), {"s": SINCE}).mappings().all()

print(f"时代内成交 {len(rows)} 笔（{SINCE} 起）")

# ── 按小时 × 币种 ──
from collections import defaultdict
h = defaultdict(lambda: {"n": 0, "notional": 0.0, "net": 0.0, "sp": 0.0, "pr": 0.0})
for r in rows:
    key = str(r["ts_bj"])[:13]  # YYYY-MM-DD HH
    h[key]["n"] += 1
    h[key]["notional"] += float(r["notional"] or 0)
    h[key]["net"] += float(r["net_bp"] or 0) * float(r["notional"] or 0) / 1e4
    h[key]["sp"] += float(r["spread_bp"] or 0) * float(r["notional"] or 0) / 1e4
    h[key]["pr"] += float(r["price_bp"] or 0) * float(r["notional"] or 0) / 1e4

print("\n== 按小时（北京）净收益 ==")
for k in sorted(h):
    v = h[k]
    print(f"  {k}  n={v['n']:4d} 名义={v['notional']:9.1f}  "
          f"价差={v['sp']:+7.3f} 价格={v['pr']:+7.3f} 净={v['net']:+7.3f}")

# ── 按时间段 ──
print("\n== 按时间段 ==")
seg = defaultdict(lambda: {"n": 0, "notional": 0.0, "net": 0.0, "sp": 0.0, "pr": 0.0})
for r in rows:
    t = r["ts_bj"]
    label = ("昨夜 22-24" if t.date().isoformat() == "2026-09-15"
             else ("凌晨 00-08" if t.hour < 8
                   else ("上午 08-12" if t.hour < 12
                         else ("午后 12-18" if t.hour < 18 else "晚间 18-24"))))
    s = seg[label]
    s["n"] += 1
    s["notional"] += float(r["notional"] or 0)
    s["net"] += float(r["net_bp"] or 0) * float(r["notional"] or 0) / 1e4
    s["sp"] += float(r["spread_bp"] or 0) * float(r["notional"] or 0) / 1e4
    s["pr"] += float(r["price_bp"] or 0) * float(r["notional"] or 0) / 1e4
order = ["昨夜 22-24", "凌晨 00-08", "上午 08-12", "午后 12-18", "晚间 18-24"]
for k in order:
    if k in seg:
        v = seg[k]
        print(f"  {k}: n={v['n']:4d} 名义={v['notional']:9.1f} 价差={v['sp']:+7.3f} "
              f"价格={v['pr']:+7.3f} 净={v['net']:+7.3f}")

# ── 按币种 ──
print("\n== 按币种（时代内） ==")
s = defaultdict(lambda: {"n": 0, "notional": 0.0, "net": 0.0, "sp": 0.0, "pr": 0.0})
for r in rows:
    v = s[r["symbol"]]
    v["n"] += 1
    v["notional"] += float(r["notional"] or 0)
    v["net"] += float(r["net_bp"] or 0) * float(r["notional"] or 0) / 1e4
    v["sp"] += float(r["spread_bp"] or 0) * float(r["notional"] or 0) / 1e4
    v["pr"] += float(r["price_bp"] or 0) * float(r["notional"] or 0) / 1e4
for k, v in sorted(s.items(), key=lambda kv: kv[1]["net"]):
    print(f"  {k:5s} n={v['n']:4d} 名义={v['notional']:9.1f} 价差={v['sp']:+8.3f} "
          f"价格={v['pr']:+8.3f} 净={v['net']:+8.3f}")
