import sqlite3
conn = sqlite3.connect('alpha_arena.db')
cur = conn.cursor()
cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = cur.fetchall()
print('Tables:', [t[0] for t in tables])
conn.close()
