import sys
from pathlib import Path
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena"); sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv; load_dotenv(ROOT/".env")
import os
from backend.services.position_construction import LaneLimits, _lane_param
for lane in ("mid", "long"):
    L = LaneLimits.for_lane(lane)
    print(f"  lane={lane}: risk_per_trade_pct={L.risk_per_trade_pct} max_weight={L.max_weight_per_symbol} cluster_cap={L.cluster_cap}")
print("  env PC_RISK_PER_TRADE_PCT_LONG =", os.getenv("PC_RISK_PER_TRADE_PCT_LONG"))
print("  env PC_RISK_PER_TRADE_PCT      =", os.getenv("PC_RISK_PER_TRADE_PCT"))
print("  _lane_param('RISK_PER_TRADE_PCT','long',1.0) =", _lane_param("RISK_PER_TRADE_PCT", "long", 1.0))
from backend.services.llm_budget_governor import *  # noqa
import backend.services.llm_budget_governor as g
print("  LLM2_CAP_MASTER env =", os.getenv("LLM2_CAP_MASTER"))
if hasattr(g, "cap_for"):
    try:
        print("  governor cap_for('master') =", g.cap_for("master"))
    except Exception as e:
        print("  cap_for err", str(e)[:80])
