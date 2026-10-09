# -*- coding: utf-8 -*-
"""[h791 2026-10-04] 反事实回测:"600s taker 硬顶" vs "全 maker 等到对手方回来"。

对近 12h 的每条 timeout_hard_taker 腿:
  · 找到同 position_id 的入场腿加权均价 = 成本价;
  · 实际结果 = 该腿的已实现净 bp;
  · 反事实 A(等到回来):出场时刻之后,该币中价**第一次**回到成本价的时间 T;
    T ≤ 2h ⇒ 反事实净 ≈ 0(以成本价 maker 离场,零漂移 + 小正捕获);
    T > 2h ⇒ 反事实净 = 2h 时点的价格项(可能远比实际更差 = maker 等待的尾部风险)。
输出 data/maker_exit_backtest_last.json。
"""
import importlib.util
import json
import sys
import time
from datetime import datetime, timezone

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
ROOT = r"D:\001Alpha\Hyper-Alpha-Arena"
_spec = importlib.util.spec_from_file_location(
    "h425_trial", r"D:\001Alpha\Hyper-Alpha-Arena\scripts\h425_repair_trial.py")
h = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h)
import psycopg
from backend.services.market_maker.attribution import _market_dsn

with psycopg.connect(h.read_env_dsn(), autocommit=True) as c, c.cursor() as cur:
    cur.execute(
        "SELECT position_id, symbol, ts, net_bp, notional"
        " FROM lane_ledger WHERE lane_id='mm_asterdex' AND event='fill'"
        " AND COALESCE(meta_json->>'exit_path','')='timeout_hard_taker'"
        " AND ts > now() - interval '12 hours' ORDER BY ts")
    legs = cur.fetchall()
    ids = sorted({str(r[0]) for r in legs if r[0]})
    cost = {}
    if ids:
        cur.execute(
            "SELECT position_id, SUM(qty*px*CASE WHEN qty>0 THEN 1 ELSE -1 END)/"
            " NULLIF(SUM(ABS(qty)),0) avg_px"
            " FROM (SELECT position_id,"
            "  (meta_json->>'qty')::float qty, (meta_json->>'fill_px')::float px"
            "  FROM lane_ledger WHERE lane_id='mm_asterdex' AND event='fill'"
            "  AND position_id = ANY(%s)) t"
            " WHERE qty IS NOT NULL AND px > 0 GROUP BY position_id", (ids,))
        cost = {str(r[0]): float(r[1]) for r in cur.fetchall() if r[1]}

print(f"timeout 腿样本 {len(legs)} 条,可配成本价的 {len(cost)} 个仓位")

results = []
with psycopg.connect(_market_dsn(), autocommit=True) as c, c.cursor() as cur:
    for pid, sym, ts, nb, ntl in legs:
        pid = str(pid)
        avg = cost.get(pid)
        if not avg or avg <= 0:
            continue
        t_ms = int(ts.timestamp() * 1000)
        cur.execute(
            "SELECT event_ts_ms, price FROM asterdex_trades WHERE symbol=%s"
            " AND event_ts_ms > %s ORDER BY event_ts_ms LIMIT 4000",
            (str(sym).upper() + "USDT", t_ms))
        trades = cur.fetchall()
        # 该腿是平仓(卖出=多头平仓,买入=空头平仓):等价格回到成本价
        got = None
        for ms, px in trades:
            # 双向都算"回到成本价"(±0.2% 容差)
            if abs(float(px) - avg) / avg <= 0.002:
                got = (float(ms) - t_ms) / 60000.0
                break
        if got is None:
            # 2h 未回来:取 2h 时点的价格算尾部
            cur.execute(
                "SELECT price FROM asterdex_trades WHERE symbol=%s"
                " AND event_ts_ms <= %s ORDER BY event_ts_ms DESC LIMIT 1",
                (str(sym).upper() + "USDT", t_ms + 7200000))
            last = cur.fetchone()
            tail_px = float(last[0]) if last else avg
            # 多头平仓=卖出;若价格更低则亏;简化用 |tail-avg|/avg 的方向性近似
            tail_bp = ((avg - tail_px) / avg * 1e4)  # 多头:价格低于成本 = 亏损
            results.append({"symbol": str(sym), "wait_min": None, "outcome": "no_return",
                            "hypothetical_bp": round(tail_bp, 1), "actual_bp": round(float(nb or 0), 1)})
        else:
            results.append({"symbol": str(sym), "wait_min": round(got, 1), "outcome": "returned",
                            "hypothetical_bp": 0.0, "actual_bp": round(float(nb or 0), 1)})

if not results:
    print("无可配样本")
    sys.exit(0)
ret = [r for r in results if r["outcome"] == "returned"]
noret = [r for r in results if r["outcome"] == "no_return"]
print(f"\n== 反事实结果(n={len(results)})== ")
print(f"  A) 等到回来(2h 内): {len(ret)} 条 = {len(ret)/len(results)*100:.0f}%,"
      f" 中位等待 {sorted(r['wait_min'] for r in ret)[len(ret)//2]:.1f} 分钟")
print(f"     这些腿的实际净 {sum(r['actual_bp'] for r in ret)/max(1,len(ret)):+.1f}bp")
print(f"     ⇒ 若改为等 maker:每腿省 {sum(r['actual_bp'] for r in ret)/max(1,len(ret)):+.1f}bp(0 - 实际)")
print(f"  B) 2h 没回来(尾部风险): {len(noret)} 条 = {len(noret)/len(results)*100:.0f}%")
if noret:
    hb = [r["hypothetical_bp"] for r in noret]
    print(f"     若等到 2h:这些腿的模拟净 {sum(hb)/len(hb):+.1f}bp(实际净 {sum(r['actual_bp'] for r in noret)/len(noret):+.1f}bp)")
    worse = sum(1 for r in noret if r["hypothetical_bp"] < r["actual_bp"])
    print(f"     其中 {worse}/{len(noret)} 条会比现在的 taker 更差")
out = {"ts": time.time(), "n": len(results),
       "returned_pct": round(len(ret)/len(results), 3),
       "median_wait_min": (sorted(r['wait_min'] for r in ret)[len(ret)//2] if ret else None),
       "avg_actual_bp_all": round(sum(r['actual_bp'] for r in results)/len(results), 2),
       "avg_hypo_bp_all": round(sum(r['hypothetical_bp'] for r in results)/len(results), 2),
       "no_return_pct": round(len(noret)/len(results), 3),
       "no_return_worse_n": (sum(1 for r in noret if r["hypothetical_bp"] < r["actual_bp"]) if noret else 0)}
with open(ROOT + "/data/maker_exit_backtest_last.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
print("  ✓ 已写 data/maker_exit_backtest_last.json")
