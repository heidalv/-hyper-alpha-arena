import sqlite3, json

db = r"d:\001Alpha\Hyper-Alpha-Arena\data\alpha_arena.db"
conn = sqlite3.connect(db)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

# Get paper_positions columns
cur.execute("PRAGMA table_info(paper_positions)")
cols = [r["name"] for r in cur.fetchall()]
print("Paper Positions columns:", cols)

# Check all positions (not just open)
print()
print("=== All paper_positions tier distribution ===")
cur.execute("SELECT timeframe_tier, COUNT(*) as cnt FROM paper_positions GROUP BY timeframe_tier")
for r in cur.fetchall():
    print(f"  {r['timeframe_tier']}: {r['cnt']}")

print()
print("=== AI_Strategies tier distribution ===")
cur.execute("SELECT timeframe_tier, COUNT(*) as cnt FROM ai_strategies GROUP BY timeframe_tier")
for r in cur.fetchall():
    print(f"  {r['timeframe_tier']}: {r['cnt']}")

# Check if ANY strategy has trade_nature set
print()
print("=== Strategies with genome trade_nature set ===")
cur.execute("SELECT id, name, genome, timeframe_tier FROM ai_strategies LIMIT 100")
found = 0
for r in cur.fetchall():
    try:
        g = json.loads(r["genome"]) if r["genome"] else {}
        if g.get("trade_nature"):
            print(f"  [{r['timeframe_tier']:6s}] {r['name'][:40]:40s} trade_nature={g['trade_nature']!r}")
            found += 1
    except:
        pass
if found == 0:
    print("  NO strategies have trade_nature set in genome (all are NOT_SET)")
else:
    print(f"  Found {found} strategies with trade_nature")

# Check positions with full data
print()
print("=== Paper Positions detail ===")
cur.execute("SELECT symbol, timeframe_tier, side, entry_price, status FROM paper_positions LIMIT 20")
for r in cur.fetchall():
    print(f"  {r['symbol']:10s} tier={r['timeframe_tier']:6s} side={r['side']:6s} price={r['entry_price']} status={r['status']}")

conn.close()
