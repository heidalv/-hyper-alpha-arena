# -*- coding: utf-8 -*-
"""[h787 2026-10-04 用户指令"认真研究全部"] 做市车道 24h 完整解剖。

回答八个问题(全部用账本实测,不猜):
  A. 总账:24h 每块钱的去向(捕获/价格/费用 三维分解)
  B. 按出场路径:数量/净额/中位 bp/平均名义
  C. 按币:净额排名(谁是赚钱的,谁是失血的)
  D. 按小时:时间线(什么时候赚/亏)
  E. 胜率与盈亏比:赢腿 vs 亏腿的 bp 与 USD 分布 + 打平胜率 vs 实际
  F. 持仓龄 vs 净:开仓→平仓时间差与结果(用 position_id 配平,样本内)
  G. 名义分桶 vs 每腿净(检验 L14:大仓腿是否更差)
  H. 尾部:最差 15 腿明细 + 占 24h 亏损的比例
"""
import importlib.util
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
_spec = importlib.util.spec_from_file_location(
    "h425_trial", r"D:\001Alpha\Hyper-Alpha-Arena\scripts\h425_repair_trial.py")
h = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h)
import psycopg

with psycopg.connect(h.read_env_dsn(), autocommit=True) as c, c.cursor() as cur:
    W = " AND ts > now() - interval '24 hours'"
    print("== A. 总账(近 24h)== ")
    cur.execute(
        "SELECT count(*), SUM(notional), SUM(spread_bp*notional)/10000.0,"
        " SUM(price_bp*notional)/10000.0, SUM(fee_bp*notional)/10000.0,"
        " SUM(net_bp*notional)/10000.0, AVG(net_bp)"
        " FROM lane_ledger WHERE lane_id='mm_asterdex' AND event='fill'" + W)
    n, notional, cap, price, fee, net, avg = cur.fetchone()
    print(f"  腿数 {n} | 总名义 ${float(notional or 0):,.0f}")
    print(f"  捕获 {float(cap or 0):+.2f}U | 价格 {float(price or 0):+.2f}U | "
          f"费用 {float(fee or 0):+.2f}U | **净 {float(net or 0):+.2f}U** | 每腿 {float(avg or 0):+.2f}bp")

    print("== B. 按出场路径 == ")
    cur.execute(
        "SELECT COALESCE(meta_json->>'exit_path','maker(自然)') p, count(*),"
        " SUM(net_bp*notional)/10000.0 u, percentile_disc(0.5) WITHIN GROUP (ORDER BY net_bp) med,"
        " AVG(notional) an, AVG(net_bp) anb,"
        " SUM(spread_bp*notional)/10000.0 sc, SUM(price_bp*notional)/10000.0 pc,"
        " SUM(fee_bp*notional)/10000.0 fc"
        " FROM lane_ledger WHERE lane_id='mm_asterdex' AND event='fill'" + W +
        " GROUP BY 1 ORDER BY 3")
    for r in cur.fetchall():
        print(f"  {str(r[0])[:26]:<26} n={r[1]:>4} 净 {float(r[2] or 0):+8.2f}U | "
              f"中位 {float(r[3] or 0):+6.1f}bp 均名义 ${float(r[4] or 0):>5.0f} | "
              f"捕获 {float(r[6] or 0):+6.2f} 价 {float(r[7] or 0):+7.2f} 费 {float(r[8] or 0):+5.2f}")

    print("== C. 按币(净额升序,全 24h)== ")
    cur.execute(
        "SELECT symbol, count(*), SUM(net_bp*notional)/10000.0 u, AVG(net_bp),"
        " SUM(CASE WHEN COALESCE(meta_json->>'exit_path','')='' THEN net_bp*notional/10000.0 ELSE 0 END) maker_u,"
        " SUM(CASE WHEN COALESCE(meta_json->>'exit_path','')<>'' THEN net_bp*notional/10000.0 ELSE 0 END) exit_u"
        " FROM lane_ledger WHERE lane_id='mm_asterdex' AND event='fill'" + W +
        " GROUP BY 1 ORDER BY 3")
    for r in cur.fetchall():
        print(f"  {str(r[0])[:10]:<10} n={r[1]:>4} 净 {float(r[2] or 0):+8.2f}U 每腿 {float(r[3] or 0):+6.1f}bp | "
              f"maker {float(r[4] or 0):+7.2f}U 出场 {float(r[5] or 0):+7.2f}U")

    print("== D. 按小时时间线 == ")
    cur.execute(
        "SELECT to_timestamp(floor(extract(epoch from ts)/3600)*3600) h, count(*),"
        " SUM(net_bp*notional)/10000.0 u, AVG(net_bp)"
        " FROM lane_ledger WHERE lane_id='mm_asterdex' AND event='fill'" + W +
        " GROUP BY 1 ORDER BY 1")
    for r in cur.fetchall():
        print(f"  {r[0]:%m-%d %H:%M} n={r[1]:>4} 净 {float(r[2] or 0):+7.2f}U 每腿 {float(r[3] or 0):+6.1f}bp")

    print("== E. 胜率与盈亏比(按腿,近 24h)== ")
    cur.execute(
        "SELECT count(*) FILTER (WHERE net_bp>0), count(*) FILTER (WHERE net_bp<=0),"
        " AVG(net_bp) FILTER (WHERE net_bp>0), AVG(net_bp) FILTER (WHERE net_bp<=0),"
        " SUM(net_bp*notional/10000.0) FILTER (WHERE net_bp>0),"
        " SUM(net_bp*notional/10000.0) FILTER (WHERE net_bp<=0)"
        " FROM lane_ledger WHERE lane_id='mm_asterdex' AND event='fill'" + W)
    nw, nl, aw, al, uw, ul = cur.fetchone()
    nw, nl = int(nw or 0), int(nl or 0)
    aw, al = float(aw or 0), float(al or 0)
    uw, ul = float(uw or 0), float(ul or 0)
    wr = nw / (nw + nl) * 100 if nw + nl else 0
    print(f"  赢 {nw} 腿(均 {aw:+.1f}bp,合 {uw:+.2f}U) vs 亏 {nl} 腿(均 {al:+.1f}bp,合 {ul:+.2f}U)")
    print(f"  胜率 {wr:.0f}% | 盈亏比(均) {abs(aw):.1f}:{abs(al):.1f} | 打平所需胜率 {abs(al)/(abs(aw)+abs(al))*100:.0f}%")
    print(f"  USD 口径:每赢腿 {uw/nw:+.4f}U vs 每亏腿 {ul/nl:+.4f}U ⇒ 比 {abs(ul/nl)/max(abs(uw/nw),1e-9):.0f}:1")

    print("== F. 持仓龄 vs 净(position_id 配平,能配上的样本)== ")
    cur.execute(
        "SELECT position_id, min(ts) o, max(ts) c,"
        " SUM(net_bp*notional)/10000.0 u"
        " FROM lane_ledger WHERE lane_id='mm_asterdex' AND event='fill'"
        " AND position_id IS NOT NULL" + W +
        " GROUP BY 1 HAVING count(*) >= 2 ORDER BY 4")
    ages = []
    for r in cur.fetchall():
        age = (r[2] - r[1]).total_seconds() / 60.0
        if age > 0.2:
            ages.append((age, float(r[3] or 0)))
    if ages:
        for lo, hi in ((0, 10), (10, 30), (30, 60), (60, 120), (120, 9999)):
            seg = [a for a in ages if lo <= a[0] < hi]
            if seg:
                su = sum(x[1] for x in seg)
                print(f"  持仓 {lo:>3}-{hi:>4}min: n={len(seg):>3} 净 {su:+.2f}U 均 {su/len(seg):+.4f}U")

    print("== G. 名义分桶 vs 每腿净(检验大仓腿是否更差)== ")
    cur.execute(
        "SELECT CASE WHEN notional<20 THEN '<$20' WHEN notional<40 THEN '$20-40'"
        " WHEN notional<78 THEN '$40-78' ELSE '>$78' END b, count(*),"
        " AVG(net_bp), SUM(net_bp*notional)/10000.0"
        " FROM lane_ledger WHERE lane_id='mm_asterdex' AND event='fill'" + W +
        " GROUP BY 1 ORDER BY 1")
    for r in cur.fetchall():
        print(f"  {str(r[0]):<8} n={r[1]:>4} 每腿 {float(r[2] or 0):+7.2f}bp 净 {float(r[3] or 0):+7.2f}U")

    print("== H. 最差 15 腿 + 占 24h 亏损比 == ")
    cur.execute(
        "SELECT ts, symbol, COALESCE(meta_json->>'exit_path','maker'), net_bp, notional,"
        " net_bp*notional/10000.0 u"
        " FROM lane_ledger WHERE lane_id='mm_asterdex' AND event='fill'" + W +
        " ORDER BY net_bp*notional LIMIT 15")
    tot_bad = 0.0
    for r in cur.fetchall():
        u = float(r[5] or 0)
        tot_bad += u
        print(f"  {r[0]:%m-%d %H:%M} {str(r[1])[:9]:<9} {str(r[2])[:20]:<20} {float(r[3] or 0):+8.1f}bp "
              f"${float(r[4] or 0):>6.0f} {u:+7.3f}U")
    cur.execute(
        "SELECT SUM(net_bp*notional)/10000.0 FROM lane_ledger WHERE lane_id='mm_asterdex'"
        " AND event='fill' AND net_bp < 0" + W)
    tot_neg = float(cur.fetchone()[0] or 0)
    print(f"  最差 15 腿合计 {tot_bad:+.2f}U = 全部亏损腿({tot_neg:+.2f}U)的 {tot_bad/tot_neg*100 if tot_neg else 0:.0f}%")
