# -*- coding: utf-8 -*-
import sqlite3, json, os
os.chdir(os.path.dirname(os.path.abspath(__file__)))

conn = sqlite3.connect('data/alpha_arena.db')
conn.row_factory = sqlite3.Row
cur = conn.cursor()

# Find proposals mentioning maturity_global_n1
print("=== MATURITY_N1 PROPOSALS ===")
rows = cur.execute("""
    SELECT id, title, status, 
           json_extract(after_json, '$.verdict') as verdict
    FROM opencode_evolution_proposals 
    WHERE proposal_json LIKE '%maturity_global_n1%'
    ORDER BY id DESC
""").fetchall()
for r in rows:
    print(f"#{r['id']} | status={r['status']} | verdict={r.get('verdict','?')} | {r['title'][:80]}")

# Latest proposals
print("\n=== LATEST 10 PROPOSALS ===")
rows = cur.execute("""
    SELECT id, title, status,
           json_extract(after_json, '$.verdict') as verdict
    FROM opencode_evolution_proposals 
    ORDER BY id DESC LIMIT 10
""").fetchall()
for r in rows:
    print(f"#{r['id']} | status={r['status']} | verdict={r.get('verdict','?')} | {r['title'][:80]}")

# Summary stats
total = cur.execute("SELECT COUNT(*) FROM opencode_evolution_proposals").fetchone()[0]
by_status = cur.execute("""
    SELECT status, COUNT(*) FROM opencode_evolution_proposals GROUP BY status
""").fetchall()
print(f"\n=== TOTAL: {total} ===")
for s in by_status:
    print(f"  {s[0]}: {s[1]}")

conn.close()
