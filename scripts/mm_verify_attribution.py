import io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
from backend.services import lane_ledger

SINCE = "2026-09-15T22:05:23+08:00"
att = lane_ledger.attribution(days=2, lane_id="mm_asterdex", since=SINCE)
t = att.get("total") or {}
print(f"时代归因（排除校正行后）: n={t.get('n')} notional={t.get('notional')} "
      f"net_bp={t.get('net_bp')} net_usd={t.get('net_usd')}")
print(f"  （修复前 n=276 名义含 +$13.6k 校正行；真实成交 263 笔）")
ds = lane_ledger.daily_series(lane_id="mm_asterdex", days=2, since=SINCE)
for r in ds:
    print(f"  day={r['date']} n={r['n']} net_usd={r['net_usd']}")
