import re, sys
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
from sqlalchemy import text
from backend.database.connection import market_engine
from backend.services.events.funding_universe import FUNDING_INTERVAL_HOURS as CANON

print("=== 1) rate_8h 的 4 份实现是否同口径 ===")
print("  权威表(funding_universe.FUNDING_INTERVAL_HOURS):", CANON)
FILES = {
    "anomaly_agent": r"backend\services\agents\anomaly_agent.py",
    "context_pack": r"backend\services\analysis\context_pack.py",
    "e5_2_funding_shock": r"backend\services\strategies\event\e5_2_funding_shock.py",
}
for name, p in FILES.items():
    src = open(p, encoding="utf-8").read()
    i = src.find("def rate_8h")
    seg = src[i:i+700] if i >= 0 else ""
    m = re.search(r"\{[^{}]*\}", seg, re.S)
    print(f"  {name:20s} 内含映射表: {m.group(0)[:300] if m else '(无内联表 → 用权威表)'}")

print("\n=== 2) 垃圾标的规模（perp_funding）===")
with market_engine.connect() as c:
    for ex in ("asterdex", "gateio", "binance"):
        rows = c.execute(text("SELECT DISTINCT symbol FROM perp_funding WHERE exchange=:e"), {"e": ex}).fetchall()
        syms = [r[0] for r in rows]
        nonascii = [s for s in syms if any(ord(ch) > 127 for ch in s)]
        weird = [s for s in syms if re.match(r"^[A-Z0-9]{2,12}(USDT|USDC|PERP|USD)?$", s) is None]
        print(f"  {ex:10s} 总 {len(syms):4d}  非ASCII {len(nonascii):3d}  非规范命名 {len(weird):4d}")
        if weird:
            print(f"     样例: {weird[:12]}")
