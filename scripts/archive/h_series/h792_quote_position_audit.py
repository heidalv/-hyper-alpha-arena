# -*- coding: utf-8 -*-
"""[h792 2026-10-04 用户指令"从底层一块一块啃"]
块#2 报价位置审计:我方报价相对真实盘口的位置(inside=插进价差内=全场最优/
touch=贴盘口/behind=盘口之后),以及**每种位置的 P&L**。

假设:如果"inside"位置(插进价差)的每腿净显著差于"touch/behind",
⇒ 我们的宽度政策太激进 —— 我们总是成为全场最优价,把**全部毒性流量**吃下来,
"漂移亏损"的根源就是"花钱买全场第一的队列位置"。
"""
import importlib.util
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
_spec = importlib.util.spec_from_file_location(
    "h425_trial", r"D:\001Alpha\Hyper-Alpha-Arena\scripts\h425_repair_trial.py")
h = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h)
import psycopg

with psycopg.connect(h.read_env_dsn(), autocommit=True) as c, c.cursor() as cur:
    # h755 起每笔成交记录 qpos(inside/touch/behind)+ qpos_bp
    cur.execute(
        "SELECT COALESCE(meta_json->>'qpos','unknown') q, count(*),"
        " AVG(net_bp), AVG(COALESCE((meta_json->>'qpos_bp')::float,0)),"
        " SUM(net_bp*notional)/10000.0, AVG(notional),"
        " AVG(price_bp)"
        " FROM lane_ledger WHERE lane_id='mm_asterdex' AND event='fill'"
        " AND meta_json ? 'qpos' AND ts > now() - interval '10 hours'"
        " GROUP BY 1 ORDER BY 1")
    print("== 报价位置 vs P&L(近 10h,带 qpos 标签的成交)== ")
    for r in cur.fetchall():
        print(f"  {str(r[0])[:9]:<9} n={r[1]:>4} 每腿净 {float(r[2] or 0):+7.2f}bp | "
              f"平均位置 {float(r[3] or 0):+6.2f}bp | 合计 {float(r[4] or 0):+7.2f}U | "
              f"均名义 ${float(r[5] or 0):>5.0f} | 价格项 {float(r[6] or 0):+6.2f}bp")

    # 分币看:每个币 inside 占比 vs 净额
    print("== 分币:inside 占比 vs 每腿净(哪些币插价差插得最狠)== ")
    cur.execute(
        "SELECT symbol,"
        " SUM(CASE WHEN COALESCE(meta_json->>'qpos','')='inside' THEN 1 ELSE 0 END)::float/NULLIF(count(*),0) inside_share,"
        " count(*), AVG(net_bp), SUM(net_bp*notional)/10000.0"
        " FROM lane_ledger WHERE lane_id='mm_asterdex' AND event='fill'"
        " AND meta_json ? 'qpos' AND ts > now() - interval '10 hours'"
        " GROUP BY 1 HAVING count(*) >= 15 ORDER BY 2 DESC")
    for r in cur.fetchall():
        print(f"  {str(r[0])[:10]:<10} inside {float(r[1] or 0)*100:>3.0f}%  n={r[2]:>3} "
              f"每腿 {float(r[3] or 0):+6.1f}bp 净 {float(r[4] or 0):+7.2f}U")

    # 结论对比:同币内 inside vs 非 inside
    print("== 同币内:inside 成交 vs touch/behind 成交的每腿净 == ")
    cur.execute(
        "SELECT symbol,"
        " AVG(net_bp) FILTER (WHERE COALESCE(meta_json->>'qpos','')='inside') i,"
        " AVG(net_bp) FILTER (WHERE COALESCE(meta_json->>'qpos','') IN ('touch','behind')) t,"
        " count(*) FILTER (WHERE COALESCE(meta_json->>'qpos','')='inside') ni,"
        " count(*) FILTER (WHERE COALESCE(meta_json->>'qpos','') IN ('touch','behind')) nt"
        " FROM lane_ledger WHERE lane_id='mm_asterdex' AND event='fill'"
        " AND meta_json ? 'qpos' AND ts > now() - interval '10 hours'"
        " GROUP BY 1 HAVING count(*) FILTER (WHERE COALESCE(meta_json->>'qpos','')='inside')>=10"
        " AND count(*) FILTER (WHERE COALESCE(meta_json->>'qpos','') IN ('touch','behind'))>=10"
        " ORDER BY 1")
    rows = cur.fetchall()
    print(f"  (可比币 {len(rows)} 个)")
    for r in rows:
        print(f"    {str(r[0])[:10]:<10} inside {float(r[1] or 0):+7.2f}bp(n={r[3]}) vs "
              f"touch/behind {float(r[2] or 0):+7.2f}bp(n={r[4]})")
