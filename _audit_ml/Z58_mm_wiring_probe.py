import os, psycopg2
from dotenv import load_dotenv
load_dotenv()
c = psycopg2.connect(os.environ['DATABASE_URL'])
cur = c.cursor()
cur.execute("set app.is_admin='on'")
cur.execute("select table_name from information_schema.tables where table_schema='public' and (table_name like %s or table_name like %s) order by 1", ('%mm%','%maker%'))
print("MM tables:", [r[0] for r in cur.fetchall()])
c.rollback()
cur.execute("select count(*) from job_registry where name like %s", ('%maker%',))
print("job_registry maker:", cur.fetchall())
c.rollback()
cur.execute("select name, enabled, schedule from job_registry where name ilike %s or name ilike %s order by 1", ('%mm%','%trend_e1%'))
for r in cur.fetchall(): print(r)
