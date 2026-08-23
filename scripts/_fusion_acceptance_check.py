"""融合改造验收核对（S0-S3 指标，阶段4 起每轮运行）。

用法：.venv/Scripts/python.exe scripts/_fusion_acceptance_check.py
数据：live paper_orders/paper_positions（app.is_admin 上下文）+ data/fusion_attribution.json + scalp_signal_log。
"""
import json
import os
import sys

sys.path.insert(0, ".")

import psycopg

ADMIN = "on"

def q(cur, sql, *args):
    cur.execute(sql, args)
    return cur.fetchall()

def main():
    con = psycopg.connect("host=localhost port=5432 dbname=alpha_arena user=laobao password=alpha_pass")
    cur = con.cursor()
    cur.execute(f"SET app.is_admin = '{ADMIN}'")

    print("== S0-4 今日净盈亏（pnl-fee，账户14+156）==")
    rows = q(cur, """SELECT account_id, COUNT(*), ROUND(SUM(COALESCE(pnl,0))::numeric,2),
      ROUND(SUM(COALESCE(fee,0))::numeric,2), ROUND((SUM(COALESCE(pnl,0))-SUM(COALESCE(fee,0)))::numeric,2)
      FROM paper_orders WHERE filled_at >= now()::date AND trade_nature NOT IN ('pair_research','research')
      GROUP BY 1 ORDER BY 1""")
    for r in rows: print("  ", r)

    print("== S0-1 今日开仓数 ==")
    rows = q(cur, """SELECT account_id, trade_nature, COUNT(*) FROM paper_positions
      WHERE opened_at >= now()::date GROUP BY 1,2 ORDER BY 1,2""")
    for r in rows: print("  ", r)

    print("== S0-2 今日 scalp 仓 RR 检查（RR=TP距/SL距 >=1.2）==")
    rows = q(cur, """SELECT id, symbol, side, ROUND(entry_price::numeric,4), ROUND(tp_price::numeric,4),
      ROUND(sl_price::numeric,4),
      CASE WHEN side='long' THEN (tp_price-entry_price)/(entry_price-sl_price)
           ELSE (sl_price-entry_price)/(entry_price-tp_price) END AS rr
      FROM paper_positions WHERE opened_at >= now()::date AND trade_nature='scalp'""")
    for r in rows:
        rr = float(r[6] or 0)
        print("  ", r, "rr=%.2f" % rr, "OK" if rr >= 1.2 else "BELOW_FLOOR")

    print("== S1-1 信号级 pwin>=0.55 桶（近7天已结算）==")
    rows = q(cur, """SELECT COUNT(*), SUM(CASE WHEN COALESCE(win,false) THEN 1 ELSE 0 END),
      ROUND(COALESCE(SUM(net_ret),0)::numeric,4)
      FROM scalp_signal_log WHERE settled AND created_at >= now() - interval '7 days'
      AND (features_json::json->>'meta_p_win')::float >= 0.55""")
    n, w, s = rows[0]
    print("  n=%d wr=%.1f%% net=%s %s" % (n or 0, 100*(w or 0)/max(n,1), s, "OK" if (n or 0)>=100 and (w or 0)/n>=0.55 else "WATCH"))

    print("== S2-1 归因状态（fusion_attribution.json）==")
    path = os.path.join("data", "fusion_attribution.json")
    if os.path.exists(path):
        d = json.load(open(path, encoding="utf-8"))
        print("  tags:", {k: (v or {}).get("source") for k, v in (d.get("tags") or {}).items()})
        print("  stats:", d.get("stats"))
        print("  shadow:", d.get("shadow"))
        print("  breaker_shadow:", d.get("breaker_shadow"))
    else:
        print("  （尚无平仓样本）")

    print("== S3-3 风控事件禁开 ==")
    p2 = os.path.join("data", "symbol_penalty_state.json")
    if os.path.exists(p2):
        d2 = json.load(open(p2, encoding="utf-8"))
        for sym, s in (d2.get("symbols") or {}).items():
            if s.get("risk_ban_until_ts"):
                import time
                if s["risk_ban_until_ts"] > time.time():
                    print("  BANNED:", sym, s.get("risk_events", [])[-1:] if s.get("risk_events") else "")
        else:
            pass
    con.close()
    print("RESULT: 验收核对完成（逐项对照 01_目标设定 文档）")

if __name__ == "__main__":
    main()
