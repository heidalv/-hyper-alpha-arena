import sqlite3
import json

db_path = 'd:/001Alpha/Hyper-Alpha-Arena/alpha_arena.db'
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# 1. 获取所有表名
print('=== 所有表 ===')
cursor.execute(" SELECT name FROM sqlite_master WHERE type=table\)
tables = [row[0] for row in cursor.fetchall()]
print(json.dumps(tables, indent=2))

# 2. 查看ai_strategies表的结构
if 'ai_strategies' in tables:
 print('\n=== ai_strategies 表结构 ===')
 cursor.execute('PRAGMA table_info(ai_strategies)')
 columns = cursor.fetchall()
 for col in columns:
 print(f'{col[1]}: {col[2]}')
 
 # 3. 查看策略总数
 cursor.execute('SELECT COUNT(*) FROM ai_strategies')
 total = cursor.fetchone()[0]
 print(f'\n=== 总策略数: {total} ===')
 
 # 4. 查看策略状态分布
 print('\n=== 策略状态分布 ===')
 cursor.execute('SELECT status, COUNT(*) as cnt FROM ai_strategies GROUP BY status')
 for status, cnt in cursor.fetchall():
 print(f'{status}: {cnt}')
 
 # 5. 查看 symbol:tier 的重复情况
 print('\n=== symbol:tier 重复统计 (active/paused) ===')
 cursor.execute('''
 SELECT symbol, timeframe_tier, status, COUNT(*) as cnt 
 FROM ai_strategies 
 WHERE status IN (\active\, \paused\)
 GROUP BY symbol, timeframe_tier, status
 HAVING cnt > 1
 ORDER BY cnt DESC
 LIMIT 20
 ''')
 for row in cursor.fetchall():
 print(f'{row[0]} | {row[1]} | {row[2]}: {row[3]}')
 
 # 6. 查看每个 symbol 的策略数
 print('\n=== 每个 symbol 的策略数 (Top 20) ===')
 cursor.execute('SELECT symbol, COUNT(*) as cnt FROM ai_strategies GROUP BY symbol ORDER BY cnt DESC LIMIT 20')
 for symbol, cnt in cursor.fetchall():
 print(f'{symbol}: {cnt}')
 
 # 7. 查看symbol总数
 print('\n=== symbol 总数 ===')
 cursor.execute('SELECT COUNT(DISTINCT symbol) FROM ai_strategies')
 distinct_symbols = cursor.fetchone()[0]
 print(f'总共 {distinct_symbols} 个不同 symbol')
 
 # 8. 查看timeframe_tier分布
 print('\n=== timeframe_tier 分布 ===')
 cursor.execute('SELECT timeframe_tier, COUNT(*) as cnt FROM ai_strategies GROUP BY timeframe_tier ORDER BY cnt DESC')
 for tier, cnt in cursor.fetchall():
 print(f'{tier}: {cnt}')
 
 # 9. 检查一些重复的例子
 print('\n=== 重复最多的 symbol:tier:status 组合的详细信息 ===')
 cursor.execute('''
 SELECT symbol, timeframe_tier, status, COUNT(*) as cnt 
 FROM ai_strategies 
 WHERE status IN (\active\, \paused\)
 GROUP BY symbol, timeframe_tier, status
 ORDER BY cnt DESC
 LIMIT 1
 ''')
 top_dup = cursor.fetchone()
 if top_dup:
 symbol, tier, status, cnt = top_dup
 print(f'查看 {symbol} | {tier} | {status} 的所有策略 (共{cnt}条)')
 cursor.execute('''
 SELECT id, strategy_name, status, created_at, updated_at, confidence_level
 FROM ai_strategies
 WHERE symbol = ? AND timeframe_tier = ? AND status = ?
 ORDER BY created_at ASC
 ''', (symbol, tier, status))
 for row in cursor.fetchall():
 print(f' ID: {row[0]}, 名称: {row[1]}, 状态: {row[2]}, 创建: {row[3]}, 更新: {row[4]}, 置信度: {row[5]}')

conn.close()
