import pathlib, psycopg
def dsn():
    env={}
    for line in pathlib.Path(".env").read_text(encoding="utf-8",errors="replace").splitlines():
        line=line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        k,v=line.split("=",1); env[k.strip()]=v.strip().strip('"').strip("'")
    s=env["DATABASE_URL"]
    for j in ("+psycopg2","+psycopg","+asyncpg"): s=s.replace(j,"")
    return s
OUT=[]
def p(*a): OUT.append(" ".join(str(x) for x in a))
with psycopg.connect(dsn()) as c:
    cur=c.cursor()
    p("== 独立复算（不复用之前任何 CTE） ==")
    cur.execute("""select count(*),
        round(sum(net_bp*notional/10000.0)::numeric,4),
        round(sum(spread_bp*notional/10000.0)::numeric,4),
        round(sum(price_bp*notional/10000.0)::numeric,4),
        round(sum(fee_bp*notional/10000.0)::numeric,4),
        round(sum(notional)::numeric,1)
      from lane_ledger where lane_id='mm_asterdex' and event='fill'
        and ts >= timestamptz '2026-09-21 18:00:00+08' and ts < timestamptz '2026-09-22 09:16:00+08'""")
    p("  n / net / spread / price / fee / notional:", cur.fetchone())
    cur.execute("""select count(*) from lane_ledger where lane_id='mm_asterdex'
        and ts >= timestamptz '2026-09-21 18:00:00+08' and ts < timestamptz '2026-09-22 09:16:00+08'
        and meta_json->>'flatten' = 'true'""")
    p("  flatten（字符串精确匹配）= true 的行数:", cur.fetchone()[0])
    cur.execute("""select count(*) from lane_ledger where lane_id='mm_asterdex'
        and ts >= timestamptz '2026-09-21 18:00:00+08' and ts < timestamptz '2026-09-22 09:16:00+08'
        and (meta_json->>'flatten')::boolean is true""")
    p("  flatten（布尔转换）行数:", cur.fetchone()[0])
    cur.execute("""select symbol, count(*), round(sum(net_bp*notional/10000.0)::numeric,4)
      from lane_ledger where lane_id='mm_asterdex' and ts >= timestamptz '2026-09-21 18:00:00+08'
        and ts < timestamptz '2026-09-22 09:16:00+08' group by 1 order by 3""")
    p("  按币:")
    for r in cur.fetchall(): p("    ", r)
    cur.execute("""select count(*) from lane_ledger
      where ts >= timestamptz '2026-09-21 18:00:00+08' and ts < timestamptz '2026-09-22 09:16:00+08'
        and lane_id <> 'mm_asterdex'""")
    p("  窗口内其它 lane 的行数:", cur.fetchone()[0])
    cur.execute("""select round(sum(net_bp*notional/10000.0)::numeric,4) from lane_ledger
      where lane_id='mm_asterdex' and ts >= timestamptz '2026-09-20 18:00:00+08'
        and ts < timestamptz '2026-09-21 09:15:00+08'""")
    p("  前晚同口径净额:", cur.fetchone()[0])
pathlib.Path("logs/_tmp_timeline/verify.txt").write_text("\n".join(OUT), encoding="utf-8")
print("\n".join(OUT))
