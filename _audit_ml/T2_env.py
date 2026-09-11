import os, sys
sys.path.insert(0, os.getcwd())
from dotenv import load_dotenv
load_dotenv('.env', override=False)
for k in ("F59_FILL_NOTIONAL","F59_HOLD","F59_MAX_ONE_SIDE_SNAPSHOTS","F59_TAKER_FEE_BP","F59_QUEUE_SHARE","F60_FILL_NOTIONAL","F60_TAKER_FEE_BP"):
    print(f"  {k} = {os.getenv(k, '(默认)')}")
from backend.services.market_maker import replay as rp
print("  FILL_NOTIONAL:", rp.FILL_NOTIONAL, "| HOLD_SNAPSHOTS:", rp.HOLD_SNAPSHOTS,
      "| MAX_ONE_SIDE_SNAPSHOTS:", rp.MAX_ONE_SIDE_SNAPSHOTS,
      "| TAKER_FEE_BP:", rp.TAKER_FEE_BP, "| QUEUE_SHARE:", rp.QUEUE_SHARE)
