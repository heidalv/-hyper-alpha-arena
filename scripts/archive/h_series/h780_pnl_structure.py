# -*- coding: utf-8 -*-
"""[h780 2026-10-03] 盈亏结构诊断:利润是否"没跑出来就被平了"?

口径(用账本,不依赖 position_id 配平):
  · 每条**平仓腿**(is_flatten=true / exit_path 非 maker)的已实现净 bp;
  · 用同币**开仓腿**的均价与平仓前的中价极值近似 mfe(用 meta.mid_px + 相邻成交);
简化近似(单查询可算,给出方向性证据):
  · 全部 maker 腿(开仓+减仓)的捕获分布:中位/均值;
  · 全部出场腿(taker/flatten)的 net 分布:中位/均值 + 分位;
  · TP 腿的真实净 vs TP 参数(30bp)⇒ 看"到点被平的利润"是否总在 30bp 附近被截断;
  · **每个平仓腿在持仓期间的 mfe 代理**:同一 position_id 的开仓腿均值 vs 平仓腿净。
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
    print("== 近 3h 各出场路径的已实现净(分位数)== ")
    cur.execute(
        "SELECT COALESCE(meta_json->>'exit_path','maker') p, count(*),"
        " AVG(net_bp), percentile_disc(0.25) WITHIN GROUP (ORDER BY net_bp),"
        " percentile_disc(0.5) WITHIN GROUP (ORDER BY net_bp),"
        " percentile_disc(0.75) WITHIN GROUP (ORDER BY net_bp),"
        " SUM(net_bp*notional)/10000.0"
        " FROM lane_ledger WHERE lane_id='mm_asterdex' AND event='fill'"
        " AND ts > now() - interval '3 hours' GROUP BY 1 ORDER BY 7")
    for r in cur.fetchall():
        print(f"  {str(r[0])[:24]:<24} n={r[1]:>3} 均 {float(r[2] or 0):+7.1f} "
              f"[p25 {float(r[3] or 0):+6.1f} | p50 {float(r[4] or 0):+6.1f} | "
              f"p75 {float(r[5] or 0):+6.1f}] 净 {float(r[6] or 0):+7.2f}U")

    print("== 同一 position_id 的开仓捕获 vs 平仓净(近 3h,利润是否跑掉)== ")
    cur.execute(
        "SELECT position_id, count(*),"
        " SUM(CASE WHEN COALESCE(meta_json->>'flatten','false')='false'"
        "   THEN net_bp*notional/10000.0 ELSE 0 END) open_u,"
        " SUM(CASE WHEN COALESCE(meta_json->>'flatten','false')='true'"
        "   THEN net_bp*notional/10000.0 ELSE 0 END) close_u"
        " FROM lane_ledger WHERE lane_id='mm_asterdex' AND event='fill'"
        " AND position_id IS NOT NULL AND ts > now() - interval '3 hours'"
        " GROUP BY 1 HAVING SUM(CASE WHEN COALESCE(meta_json->>'flatten','false')='true' THEN 1 ELSE 0 END) > 0"
        " ORDER BY (SUM(CASE WHEN COALESCE(meta_json->>'flatten','false')='true' THEN net_bp*notional/10000.0 ELSE 0 END))"
        " LIMIT 12")
    rows = cur.fetchall()
    tot_open = tot_close = 0.0
    for r in rows:
        o, cl = float(r[2] or 0), float(r[3] or 0)
        tot_open += o
        tot_close += cl
        print(f"  {str(r[0])[:14]:<14} 开仓累计 {o:+7.3f}U  平仓累计 {cl:+7.3f}U")
    print(f"  合计(样本): 开仓 {tot_open:+.3f}U  平仓 {tot_close:+.3f}U")

    print("== 止盈腿(TP)的真实净分布:利润是否在 30bp 被截断 == ")
    cur.execute(
        "SELECT net_bp FROM lane_ledger WHERE lane_id='mm_asterdex' AND event='fill'"
        " AND COALESCE(meta_json->>'exit_path','')='take_profit_taker'"
        " AND ts > now() - interval '6 hours' ORDER BY net_bp")
    tp = [float(x[0] or 0) for x in cur.fetchall()]
    if tp:
        print(f"  TP 腿 n={len(tp)} 值: {[round(x, 0) for x in tp]}")
    else:
        print("  近 6h 无 TP 腿(止盈从未触发?!)")
