import pathlib, psycopg
def env():
    e={}
    for line in pathlib.Path(".env").read_text(encoding="utf-8",errors="replace").splitlines():
        line=line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        k,v=line.split("=",1); e[k.strip()]=v.strip().strip('"').strip("'")
    return e
def dsn():
    s=env()["DATABASE_URL"]
    for j in ("+psycopg2","+psycopg","+asyncpg"): s=s.replace(j,"")
    return s
OUT=[]
def p(*a): OUT.append(" ".join(str(x) for x in a))
with psycopg.connect(dsn()) as c:
    cur=c.cursor()
    p("== 全历史 fee_bp 取值分布（mm_asterdex） ==")
    cur.execute("""select fee_bp, count(*), round(sum(notional)::numeric,0),
                          round(sum(fee_bp*notional/1e4)::numeric,4)
                   from lane_ledger where lane_id='mm_asterdex' group by 1 order by 2 desc limit 10""")
    cur.execute("""select round(fee_bp::numeric,6) b, count(*), round(sum(notional)::numeric,0),
                          round(sum(fee_bp*notional/1e4)::numeric,4)
                   from lane_ledger where lane_id='mm_asterdex' group by 1 order by 2 desc limit 12""")
    for r in cur.fetchall(): p("   fee_bp=%s  n=%s  名义=%s  USD=%s" % r)
    p("\n== 只有 flatten 腿有费？ ==")
    cur.execute("""select (lower(coalesce(meta_json->>'flatten','false')) in ('true','1')) flat,
                          count(*), count(*) filter (where fee_bp <> 0),
                          round(sum(fee_bp*notional/1e4)::numeric,4)
                   from lane_ledger where lane_id='mm_asterdex' group by 1""")
    for r in cur.fetchall(): p("   flatten=%s  n=%s  有费笔数=%s  费额=%s" % r)
    p("\n== 实测费率（费额/名义，按 flatten） ==")
    cur.execute("""select round((sum(fee_bp*notional)/sum(notional))::numeric,6)
                   from lane_ledger where lane_id='mm_asterdex' and fee_bp <> 0""")
    p("   加权平均 fee_bp =", cur.fetchone()[0])
    p("\n== 账户总手续费 vs 账本 ==")
    cur.execute("select round(sum(amount_usd)::numeric,4), count(*) from arbitrage_paper_ledgers where account_id=101 and action='paper_fee'")
    p("   account101 paper_fee:", cur.fetchone())
    cur.execute("""select round(sum(fee_bp*notional/1e4)::numeric,4) from lane_ledger
                   where lane_id='mm_asterdex' and fee_bp <> 0""")
    p("   lane_ledger 同口径:", cur.fetchone())
    p("\n== 出库腿 taker 费率一致性（每笔费/名义×1e4） ==")
    cur.execute("""select round((fee_bp)::numeric,6), count(*), round(min(notional)::numeric,1),
                          round(max(notional)::numeric,1), round(avg(notional)::numeric,1)
                   from lane_ledger where lane_id='mm_asterdex'
                     and (lower(coalesce(meta_json->>'flatten','false')) in ('true','1'))
                   group by 1 order by 2 desc limit 6""")
    for r in cur.fetchall(): p("   fee_bp=%s n=%s 名义 min/avg/max = %s / %s / %s" % r)
pathlib.Path("logs/_tmp_timeline/feecheck.txt").write_text("\n".join(OUT), encoding="utf-8")
print("\n".join(OUT))
