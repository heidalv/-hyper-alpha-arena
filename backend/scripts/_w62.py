import psycopg, json, io, sys, datetime, collections, time
sys.stdout = io.TextIOWrapper(open("logs/_w62.txt", "wb"), encoding="utf-8")
print("now =", datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
c = psycopg.connect('postgresql://laobao:alpha_pass@localhost:5432/alpha_arena', autocommit=False)
cur = c.cursor()
cur.execute("set local app.tenant_id='326'")
cur.execute("set local app.is_admin='on'")
cur.execute("""select timeframe_tier, symbol, side, opened_at, status
               from paper_positions where opened_at >= '2026-09-18 09:00' order by opened_at""")
rows = cur.fetchall()
print("09-18 09:00 后开仓:", len(rows))
for r in rows:
    print(f"   {str(r[0]):5s} {str(r[1]):8s} {str(r[2]):5s} {str(r[3])[11:19]} {r[4]}")
cur.execute("select count(*) from paper_positions where status='open'")
print("当前 open 总数:", cur.fetchone()[0])
cur.execute("""select timeframe_tier, count(*) from paper_positions
               where status='open' group by 1""")
print("  open 按 tier:", dict(cur.fetchall()))
c.rollback()

lo = datetime.datetime(2026, 9, 18, 9, 20).timestamp()
rows = []
for line in io.open("data/midlong_direction_audit.jsonl", encoding="utf-8", errors="ignore"):
    line = line.strip()
    if not line:
        continue
    try:
        d = json.loads(line)
    except Exception:
        continue
    if (d.get("epoch") or 0) >= lo:
        rows.append(d)
print(f"\n09:20 后审计行数: {len(rows)}")
if rows:
    print("  tier:", dict(collections.Counter(str(d.get('tier')) for d in rows)))
    print("  stage:", dict(collections.Counter(str(d.get('stage')) for d in rows)))
    print("  outcome:", dict(collections.Counter(str(d.get('outcome')) for d in rows)))
    print("  reason:")
    for k, n in collections.Counter(str(d.get('reason') or '').split(':')[0][:40] for d in rows).most_common(8):
        print(f"     {k:42s} n={n}")
    print("  最后 6 行:")
    for d in sorted(rows, key=lambda x: x['epoch'])[-6:]:
        print("    ", datetime.datetime.fromtimestamp(d['epoch']).strftime('%H:%M:%S'),
              'tier=', str(d.get('tier')), 'dir=', str(d.get('dir')), 'stage=', str(d.get('stage')),
              '|', str(d.get('reason'))[:58])
