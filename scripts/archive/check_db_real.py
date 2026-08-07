import sqlite3
import os
db_path = './data/alpha_arena.db'
if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [t[0] for t in cur.fetchall()]
    print('Tables found:', tables)
    if 'strategy_memories' in tables:
        cur.execute("PRAGMA table_info(strategy_memories)")
        cols = [row[1] for row in cur.fetchall()]
        print('Columns:', cols)
        print('Has partial_pnl:', 'partial_pnl' in cols)
        print('Has partial_close_count:', 'partial_close_count' in cols)
        print('Has last_reduce_at:', 'last_reduce_at' in cols)
    conn.close()
else:
    print('DB file not found at:', db_path)
