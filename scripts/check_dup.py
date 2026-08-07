import sqlite3, json
from collections import defaultdict

db = r"d:\001Alpha\Hyper-Alpha-Arena\data\alpha_arena.db"
conn = sqlite3.connect(db)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

# 1. Check duplicate active strategies by symbol:tier
print("=== ACTIVE策略 symbol:tier 重复分析 ===")
cur.execute("""
    SELECT account_id, primary_symbol, timeframe_tier, COUNT(*) as cnt
    FROM ai_strategies
    WHERE status IN ('active', 'paused')
    GROUP BY account_id, primary_symbol, timeframe_tier
    HAVING cnt > 1
    ORDER BY cnt DESC
""")
dup_rows = cur.fetchall()
if dup_rows:
    total_dup = sum(r['cnt'] for r in dup_rows)
    print(f"  发现 {len(dup_rows)} 组重复 (共 {total_dup} 条策略)")
    for r in dup_rows[:20]:
        print(f"  account={r['account_id']} symbol={r['primary_symbol']} tier={r['timeframe_tier']} count={r['cnt']}")
else:
    print("  无 active/paused 重复")

# 2. Check ALL strategies by symbol (including archived)
print()
print("=== 全部策略 symbol:tier 重复分析（含archived）===")
cur.execute("""
    SELECT account_id, primary_symbol, timeframe_tier, status, COUNT(*) as cnt
    FROM ai_strategies
    GROUP BY account_id, primary_symbol, timeframe_tier, status
    HAVING cnt > 1
    ORDER BY cnt DESC
""")
all_dup = cur.fetchall()
if all_dup:
    print(f"  发现 {len(all_dup)} 组 (含不同状态)")
    for r in all_dup[:20]:
        print(f"  account={r['account_id']} symbol={r['primary_symbol']} tier={r['timeframe_tier']} status={r['status']} count={r['cnt']}")
else:
    print("  无任何重复")

# 3. Total strategy count by status
print()
print("=== 策略状态分布 ===")
cur.execute("SELECT status, COUNT(*) as cnt FROM ai_strategies GROUP BY status ORDER BY cnt DESC")
for r in cur.fetchall():
    print(f"  {r['status']}: {r['cnt']}")

# 4. FullAutoSession analysis
print()
print("=== FullAutoSession 分析 ===")
cur.execute("SELECT session_id, status, active_strategy_ids, terminated_strategy_ids, total_strategies_created FROM full_auto_sessions")
for r in cur.fetchall():
    active = json.loads(r['active_strategy_ids'] or '[]') if r['active_strategy_ids'] else []
    terminated = json.loads(r['terminated_strategy_ids'] or '[]') if r['terminated_strategy_ids'] else []
    print(f"  session={r['session_id'][:15]}... status={r['status']} active={len(active)} terminated={len(terminated)} total_created={r['total_strategies_created']}")

# 5. Strategy creation timeline
print()
print("=== 策略创建时间线 (最近50条) ===")
cur.execute("""
    SELECT primary_symbol, timeframe_tier, status, created_at
    FROM ai_strategies
    ORDER BY created_at DESC
    LIMIT 50
""")
for r in cur.fetchall():
    print(f"  {r['created_at']} {r['primary_symbol']:10s} tier={r['timeframe_tier']:6s} status={r['status']}")

# 6. Check strategies without session
print()
print("=== 孤儿策略 (active但不在任何session) ===")
cur.execute("SELECT active_strategy_ids, terminated_strategy_ids FROM full_auto_sessions")
all_session_sids = set()
for r in cur.fetchall():
    active = json.loads(r['active_strategy_ids'] or '[]') if r['active_strategy_ids'] else []
    terminated = json.loads(r['terminated_strategy_ids'] or '[]') if r['terminated_strategy_ids'] else []
    all_session_sids.update(active)
    all_session_sids.update(terminated)

cur.execute("SELECT strategy_id, primary_symbol, timeframe_tier, status FROM ai_strategies WHERE status IN ('active', 'paused')")
orphans = []
for r in cur.fetchall():
    if r['strategy_id'] not in all_session_sids:
        orphans.append(r)

if orphans:
    print(f"  发现 {len(orphans)} 个孤儿策略:")
    for o in orphans[:10]:
        print(f"    {o['strategy_id'][:15]}... {o['primary_symbol']} tier={o['timeframe_tier']} status={o['status']}")
else:
    print("  无孤儿策略")

conn.close()
