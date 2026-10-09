import sys
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
from backend.services.agent_quant_feature_table import render_quant_feature_table
from backend.database.connection import AnalyticsSessionLocal
with AnalyticsSessionLocal() as db:
    for nature in ("trend_follow", "swing"):
        t = render_quant_feature_table("BTC", {}, db, 14, nature=nature)
        t = t or ""
        print(f"--- nature={nature} len={len(t)} lines={t.count(chr(10))+1} ---")
        print(t[:700])
        print("...")
