# -*- coding: utf-8 -*-
"""LLM 配置与路由调研"""
import json
import psycopg

conn = psycopg.connect('postgresql://laobao:alpha_pass@localhost:5432/alpha_arena')
conn.autocommit = False
cur = conn.cursor()
cur.execute("SET LOCAL app.tenant_id = '326'")
try:
    cur.execute("SET LOCAL app.is_admin = 'on'")
except Exception:
    pass

cur.execute('SELECT * FROM llm_configurations ORDER BY id')
cols = [d.name for d in cur.description]
print('== llm_configurations cols:', cols)
for r in cur.fetchall():
    row = dict(zip(cols, r))
    row.pop('api_key', None)
    print(json.dumps(row, ensure_ascii=False, default=str)[:340])

conn.rollback(); conn.close()
