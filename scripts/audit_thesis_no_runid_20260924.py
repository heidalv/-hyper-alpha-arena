import sys
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
from sqlalchemy import text
from backend.database.connection import analytics_engine
with analytics_engine.connect() as c:
    print("=== 全库：缺 analysis_run_id 的论题 ===")
    r = c.execute(text("""SELECT count(*) total,
                                 count(*) FILTER (WHERE coalesce(analysis_run_id,'')='') no_run,
                                 count(*) FILTER (WHERE coalesce(analysis_run_id,'')='' AND updated_at > now() - interval '24 hours') no_run_recent
                          FROM mlto_thesis""")).first()
    print(f"  总 {r[0]}  缺 run_id {r[1]}  其中近 24h 内有更新 {r[2]}")
    print("--- 缺 run_id 的按 session/tier 与时间 ---")
    for x in c.execute(text("""SELECT session_id, tier, count(*) n, min(updated_at) first_upd, max(updated_at) last_upd
                               FROM mlto_thesis WHERE coalesce(analysis_run_id,'')=''
                               GROUP BY 1,2 ORDER BY n DESC LIMIT 8""")):
        print(f"  {str(x[0])[:18]:18s} {str(x[1]):6s} n={x[2]:3d}  {x[3]} -> {x[4]}")
    print("--- 这些行是否在活会话里（fa_7e12e7a1b6）且已过期 ---")
    for x in c.execute(text("""SELECT symbol, tier, accepted, expires_at, updated_at
                               FROM mlto_thesis WHERE session_id='fa_7e12e7a1b6'
                                 AND coalesce(analysis_run_id,'')='' ORDER BY updated_at DESC LIMIT 8""")):
        print(f"  {x[0]:10s} {x[1]:6s} accepted={x[2]} expires={x[3]} updated={x[4]}")
