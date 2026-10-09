import io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
from backend.services import lane_ledger
from backend.services.market_maker.runner import lane_day_pnl_usd

rows = lane_ledger.daily_series(lane_id="mm_asterdex", days=2)
print("daily_series(days=2):")
for r in rows:
    print("  ", r)
print("lane_day_pnl_usd(mm_asterdex) =", round(lane_day_pnl_usd("mm_asterdex"), 4))
