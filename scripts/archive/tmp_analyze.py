import sqlite3
import json

db_path = 'd:/001Alpha/Hyper-Alpha-Arena/alpha_arena.db'
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

print('=== 所有表 ===')
cursor.execute(" SELECT name FROM sqlite_master WHERE type=table\)
tables = [row[0] for row in cursor.fetchall()]
print(json.dumps(tables, indent=2))

if 'ai_strategies' in tables:
 print('\n=== ai_strategies 表结构 ===')
 cursor.execute('PRAGMA table_info(ai_strategies)')
 columns = cursor.fetchall()
 for col in columns:
 print(f'{col[1]}: {col[2]}')
 
 cursor.execute('SELECT COUNT(*) FROM ai_strategies')
 total = cursor.fetchone()[0]
 print(f'\n=== 总策略数: {total} ===')
 
 print('\n=== 策略状态分布 ===')
 cursor.execute('SELECT status, COUNT(*) as cnt FROM ai_strategies GROUP BY status')
 for status, cnt in cursor.fetchall():
 print(f'{status}: {cnt}')
 
 print('\n=== symbol 总数 ===')
 cursor.execute('SELECT COUNT(DISTINCT symbol) FROM ai_strategies')
 distinct_symbols = cursor.fetchone()[0]
 print(f'总共 {distinct_symbols} 个不同 symbol')
 
 print('\n=== timeframe_tier 分布 ===')
 cursor.execute('SELECT timeframe_tier, COUNT(*) as cnt FROM ai_strategies GROUP BY timeframe_tier ORDER BY cnt DESC')
 for tier, cnt in cursor.fetchall():
 print(f'{tier}: {cnt}')
 
 print('\n=== 每个 symbol 的策略数 (Top 20) ===')
 cursor.execute('SELECT symbol, COUNT(*) as cnt FROM ai_strategies GROUP BY symbol ORDER BY cnt DESC LIMIT 20')
 for symbol, cnt in cursor.fetchall():
 print(f'{symbol}: {cnt}')

conn.close()
