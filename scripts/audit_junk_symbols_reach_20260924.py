import re, sys
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
from sqlalchemy import text
from backend.database.connection import analytics_engine
print("=== 垃圾标的是否进入分析/决策侧（analytics）===")
with analytics_engine.connect() as c:
    for tbl, col in (("market_analysis_snapshots", "symbol"), ("ATASFactorCache", "symbol"), ("ai_decision_logs", "symbol")):
        try:
            rows = c.execute(text(f"SELECT DISTINCT {col} FROM {tbl}")).fetchall()
            syms = [str(r[0]) for r in rows if r[0] is not None]
            bad = [s for s in syms if any(ord(ch) > 127 for ch in s)]
            weird = [s for s in syms if re.match(r"^[A-Z0-9]{2,12}(USDT|USDC|PERP|USD)?$", s) is None]
            print(f"  {tbl:26s} 共 {len(syms):4d} 币  非ASCII {len(bad)}  非规范 {len(weird)}  样例={weird[:8]}")
        except Exception as e:
            print(f"  {tbl:26s} ERR {str(e)[:80]}")
