import os, sys
sys.path.insert(0, os.getcwd())
from dotenv import load_dotenv
load_dotenv('.env', override=False)
from backend.services.market_maker.core import QuoteParams, LaneRiskLimits
p = QuoteParams(); l = LaneRiskLimits()
print("=== 新默认参数 ===")
print(f"  QuoteParams: w_base={p.w_base_bp}bp k_inv={p.k_inv}")
print(f"  LaneRiskLimits: max_one_side={l.max_one_side_seconds}s stop_loss={l.stop_loss_bp}bp")
