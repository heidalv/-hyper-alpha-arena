import sqlite3
conn = sqlite3.connect('alpha_arena.db')
cur = conn.cursor()
cur.execute("PRAGMA table_info(strategy_memories)")
cols = [row[1] for row in cur.fetchall()]
print('Columns:', cols)
print('Has partial_pnl:', 'partial_pnl' in cols)
print('Has partial_close_count:', 'partial_close_count' in cols)
print('Has last_reduce_at:', 'last_reduce_at' in cols)
conn.close()
