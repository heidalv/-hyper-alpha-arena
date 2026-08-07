import sqlite3
db_path = './data/alpha_arena.db'
conn = sqlite3.connect(db_path)
cur = conn.cursor()
# 查看是否有任何记录的 partial_close_count > 0 或 partial_pnl != 0
cur.execute("""SELECT COUNT(*) FROM strategy_memories 
               WHERE (partial_pnl IS NOT NULL AND partial_pnl != 0.0) 
               OR (partial_close_count IS NOT NULL AND partial_close_count > 0)""")
count = cur.fetchone()[0]
print(f'Records with non-zero partial close data: {count}')
# 查看 last_reduce_at 的值
cur.execute("SELECT COUNT(*) FROM strategy_memories WHERE last_reduce_at IS NOT NULL")
last_reduce_count = cur.fetchone()[0]
print(f'Records with last_reduce_at set: {last_reduce_count}')
conn.close()
