import pathlib, psycopg
def env():
    e={}
    for line in pathlib.Path(".env").read_text(encoding="utf-8",errors="replace").splitlines():
        line=line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        k,v=line.split("=",1); e[k.strip()]=v.strip().strip('"').strip("'")
    return e
def dsn(key="DATABASE_URL"):
    s=env().get(key,"")
    for j in ("+psycopg2","+psycopg","+asyncpg"): s=s.replace(j,"")
    return s
OUT=[]
def p(*a): OUT.append(" ".join(str(x) for x in a))
with psycopg.connect(dsn()) as c:
    cur=c.cursor()
    for t in ("rebate_orders","rebate_trade_outcomes","rebate_positions"):
        try:
            cur.execute("""select column_name, data_type from information_schema.columns
                           where table_name=%s order by ordinal_position""",(t,))
            p(f"== {t} ==")
            p("  ", [r[0] for r in cur.fetchall()])
            cur.execute(f"select count(*) from {t}")
            p("   rows:", cur.fetchone()[0])
        except Exception as e: p(f"  {t} err:", e)
    p("\n== 所有含 fee/commission 列的表 ==")
    cur.execute("""select table_name, column_name from information_schema.columns
                   where table_schema='public' and (column_name ilike '%%fee%%'
                   or column_name ilike '%%commission%%') order by 1,2""")
    for r in cur.fetchall(): p("  ", r)
pathlib.Path("logs/_tmp_timeline/feetables.txt").write_text("\n".join(OUT), encoding="utf-8")
print("\n".join(OUT))
