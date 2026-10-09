import sys
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
from sqlalchemy import text
from backend.database.connection import SessionLocal, AnalyticsSessionLocal
NET = """((CASE WHEN lower(side) IN ('long','buy') THEN (close_price-entry_price)
             ELSE (entry_price-close_price) END) * size)
         - coalesce(partial_fee_paid,0) - coalesce(final_fee_paid,0)"""
trades = {}
with SessionLocal() as s:
    for r in s.execute(text(f"""
        SELECT date_trunc('day', closed_at)::date d, count(*), round(sum({NET})::numeric,2)
        FROM paper_positions WHERE status='closed' AND closed_at >= '2026-09-15'
        GROUP BY 1 ORDER BY 1
    """)):
        trades[str(r[0])] = (r[1], float(r[2]))
pm = {}
bump = {}
with AnalyticsSessionLocal() as a:
    for r in a.execute(text("""
        SELECT date_trunc('day', ts)::date d, event_type, count(*)
        FROM mlto_thesis_events
        WHERE ts >= '2026-09-15' AND event_type IN ('postmortem','owm_bump')
        GROUP BY 1,2 ORDER BY 1
    """)):
        (pm if r[1] == "postmortem" else bump)[str(r[0])] = r[2]
print(f"  {'日期':12s} {'平仓':>4s} {'净':>8s} {'postmortem':>10s} {'owm_bump':>9s}")
tot_t = tot_p = 0
for d in sorted(trades):
    n, net = trades[d]
    p = pm.get(d, 0); b = bump.get(d, 0)
    tot_t += n; tot_p += p
    print(f"  {d:12s} {n:4d} {net:8.2f} {p:10d} {b:9d}")
print(f"\n  合计: 平仓 {tot_t} 笔, postmortem {tot_p} 条 ⇒ 学习覆盖率 ≈ {tot_p/max(1,tot_t)*100:.0f}%")
print("  注：postmortem 有两个写入方（learning_bus 异步 / record_outcome），并非每笔都对应一次学习回写；")
print("      真实'学习回写'以 owm_bump + trace(done ...) 为准（本会话已实测 2 次）。")
