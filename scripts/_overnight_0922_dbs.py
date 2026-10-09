import pathlib, psycopg
def env():
    e={}
    for line in pathlib.Path(".env").read_text(encoding="utf-8",errors="replace").splitlines():
        line=line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        k,v=line.split("=",1); e[k.strip()]=v.strip().strip('"').strip("'")
    return e
def dsn(key):
    s=env().get(key,"")
    for j in ("+psycopg2","+psycopg","+asyncpg"): s=s.replace(j,"")
    return s
OUT=[]
def p(*a): OUT.append(" ".join(str(x) for x in a))
print("可用 DSN key:", [k for k in env() if "DATABASE" in k or k.endswith("_URL")])
with psycopg.connect(dsn("DATABASE_URL")) as c:
    cur=c.cursor()
    cur.execute("select datname from pg_database where datistemplate=false")
    p("数据库:", [r[0] for r in cur.fetchall()])
    cur.execute("""select table_schema, table_name from information_schema.tables
                   where table_name like '%%orderbook%%' or table_name like '%%market_%%' order by 1,2 limit 40""")
    p("\n相关表:")
    for r in cur.fetchall(): p("  ", r)
    for k in ("MARKET_DATABASE_URL","ALPHA_MARKET_URL","MARKET_URL"):
        if env().get(k): p(f"\n{k} 存在")
pathlib.Path("logs/_tmp_timeline/dbs.txt").write_text("\n".join(OUT), encoding="utf-8")
print("\n".join(OUT))
