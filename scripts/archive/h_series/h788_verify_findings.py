# -*- coding: utf-8 -*-
"""[h788 2026-10-04 用户指令:确认错误→深度调查→测试→验证方向]
验证研究:拷问 24h 解剖的四条铁证是否存在**混杂变量**(我的历史教训:
两次"幻影成交"都是测量错误)。

对照设计:
  V1. 尺寸曲线是否被"腿型"混杂?  入口腿 vs 出场腿 分开看每桶净
  V2. 完整仓位(往返)的口径:entry 捕获 + exit 成本 = 每仓净;分仓看
  V3. 止损反事实:若止损 40bp(替代 150bp),24h 能少亏多少(用真实腿截断模拟)
  V4. 方向验证:捕获引擎的证据链(严格审计、<20 桶、markout)
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

W = " AND ts > now() - interval '24 hours'"

with psycopg.connect(h.read_env_dsn(), autocommit=True) as c, c.cursor() as cur:
    print("== V1. 尺寸曲线:入口腿 vs 出场腿(排除腿型混杂)== ")
    for leg_type, cond in (("入口腿(flatten=false)", "COALESCE(meta_json->>'flatten','false')='false'"),
                           ("出场腿(flatten=true)", "COALESCE(meta_json->>'flatten','false')='true'")):
        print(f"  [{leg_type}]")
        cur.execute(
            "SELECT CASE WHEN notional<20 THEN '<$20' WHEN notional<40 THEN '$20-40'"
            " WHEN notional<78 THEN '$40-78' ELSE '>$78' END b, count(*),"
            " AVG(net_bp), SUM(net_bp*notional)/10000.0"
            " FROM lane_ledger WHERE lane_id='mm_asterdex' AND event='fill' AND " + cond + W +
            " GROUP BY 1 ORDER BY 1")
        for r in cur.fetchall():
            print(f"    {str(r[0]):<8} n={r[1]:>4} 每腿 {float(r[2] or 0):+7.2f}bp 净 {float(r[3] or 0):+7.2f}U")

    print("== V2. 完整仓位往返(position_id 配平,仅样本内)== ")
    cur.execute(
        "SELECT position_id, count(*), SUM(net_bp*notional)/10000.0 u,"
        " MAX(notional) mx,"
        " SUM(CASE WHEN COALESCE(meta_json->>'flatten','false')='false' THEN net_bp*notional/10000.0 ELSE 0 END) eu,"
        " SUM(CASE WHEN COALESCE(meta_json->>'flatten','false')='true' THEN net_bp*notional/10000.0 ELSE 0 END) xu"
        " FROM lane_ledger WHERE lane_id='mm_asterdex' AND event='fill'"
        " AND position_id IS NOT NULL" + W +
        " GROUP BY 1 HAVING SUM(CASE WHEN COALESCE(meta_json->>'flatten','false')='true' THEN 1 ELSE 0 END)>0"
        " AND count(*) >= 2 ORDER BY 3")
    rows = cur.fetchall()
    wins = [r for r in rows if float(r[2] or 0) > 0]
    losses = [r for r in rows if float(r[2] or 0) <= 0]
    su = sum(float(r[2] or 0) for r in rows)
    seu = sum(float(r[4] or 0) for r in rows)
    sxu = sum(float(r[5] or 0) for r in rows)
    print(f"  可配平仓位 {len(rows)} 个:净 {su:+.2f}U | 入口合计 {seu:+.2f}U 出场合计 {sxu:+.2f}U")
    print(f"  赢仓 {len(wins)} 个 / 亏仓 {len(losses)} 个 ⇒ 仓位级胜率 {len(wins)/max(1,len(rows))*100:.0f}%")
    if losses:
        aw = su / len(wins) if wins else 0
        al = sum(float(r[2] or 0) for r in losses) / len(losses)
        print(f"  每赢仓 {aw:+.4f}U vs 每亏仓 {al:+.4f}U ⇒ 盈亏比 1:{abs(al)/max(abs(aw),1e-9):.1f}")
    print("  最差 10 仓:")
    for r in sorted(rows, key=lambda x: float(x[2] or 0))[:10]:
        print(f"    {str(r[0])[:16]:<16} n腿={r[1]} 净 {float(r[2] or 0):+7.3f}U 最大名义 ${float(r[3] or 0):>5.0f} "
              f"(入口 {float(r[4] or 0):+.3f} / 出场 {float(r[5] or 0):+.3f})")

    print("== V3. 止损反事实:150bp → 40bp 能少亏多少(截断模拟)== ")
    cur.execute(
        "SELECT net_bp, notional, COALESCE(meta_json->>'flatten','false') fl"
        " FROM lane_ledger WHERE lane_id='mm_asterdex' AND event='fill'" + W)
    saved = 0.0
    n_affected = 0
    for r in cur.fetchall():
        nb, ntl, fl = float(r[0] or 0), float(r[1] or 0), str(r[2])
        if fl != "true":
            continue
        if nb < -40.0:
            # 截断:原亏 nb,改为亏 -40(理想化:止损在 40bp 处成交,不计滑点)
            saved += (nb + 40.0) * ntl / 10000.0
            n_affected += 1
    print(f"  出场腿中 net<-40bp 的 {n_affected} 条;理想化截断(不计滑点)可少亏 {saved:+.2f}U")
    print(f"  注:跳空腿(滑点)截断收益会打折;真实收益介于 0 ~ {saved:.1f}U 之间")

    print("== V4. 方向验证(捕获引擎的证据链)== ")
    cur.execute(
        "SELECT AVG(net_bp), SUM(net_bp*notional)/10000.0 FROM lane_ledger"
        " WHERE lane_id='mm_asterdex' AND event='fill'"
        " AND COALESCE(meta_json->>'flatten','false')='false'" + W)
    a, u = cur.fetchone()
    print(f"  入口腿(概率引擎的产物):n 见 V1,每腿 {float(a or 0):+.2f}bp,合计 {float(u or 0):+.2f}U")
    import json
    try:
        fa = json.loads(open(r"D:\001Alpha\Hyper-Alpha-Arena\data\fill_audit_last.json", encoding="utf-8").read())
        print(f"  A1 严格成交审计:合法率 {fa.get('strict_rate')} 每腿 {fa.get('strict_per_leg_bp')}bp n={fa.get('checked')}")
    except Exception:
        pass
