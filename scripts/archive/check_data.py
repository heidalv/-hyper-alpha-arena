import sqlite3
db_path = './data/alpha_arena.db'
conn = sqlite3.connect(db_path)
cur = conn.cursor()
# 检查是否有数据
cur.execute("SELECT COUNT(*) FROM strategy_memories")
count = cur.fetchone()[0]
print(f'Total records in strategy_memories: {count}')
# 查看新字段的数据
if count > 0:
    cur.execute("""SELECT strategy_id, partial_pnl, partial_close_count, last_reduce_at 
                    FROM strategy_memories 
                    WHERE partial_pnl IS NOT NULL OR partial_close_count > 0 
                    LIMIT 5""")
    rows = cur.fetchall()
    print(f'Records with partial close data: {len(rows)}')
    for row in rows:
        print(f'  Strategy {row[0][:8]}: pnl={row[1]}, count={row[2]}, last_at={row[3]}')
conn.close()
