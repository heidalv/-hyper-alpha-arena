"""[2026-09-20] 中线出场通道体检（**只读**）：用引擎自己记录的 MFE/MAE 做"不需要重建价格路径"的分析。

为什么单独做这一步：反事实回放脚本 `scripts/replay_mid_exit_counterfactual.py` 的第一版基线
（+$132）与实际（−$16.91）差了一个量级 ⇒ 回放的规则编码有错，**不能用它下结论**。
但 `peak_pnl_pct / trough_pnl_pct` 是引擎在真实 tick 上记下来的，可以直接回答三件事：

  A) 14 笔止损里，有几笔的 MAE 根本没到 −2.0%？——这些是"cap 1.5% 提前打死"的候选
  B) 42 笔 breakeven_tp 的 MFE 有多高？——锁利线把多少浮盈让出去了
  C) 各出场通道的实际贡献（USD / 单笔价格%）——钱到底漏在哪一根管子

用法：.venv\\Scripts\\python.exe scripts/audit_mid_exit_channels_20260920.py [--days 30]
"""
from __future__ import annotations

import argparse
import io
import statistics as st
import sys

import psycopg

DSN = "postgresql://laobao:alpha_pass@localhost:5432/alpha_arena"
WHERE = ("account_id = 14 and status = 'closed' and timeframe_tier = 'mid' "
         "and closed_at > now() - (%s || ' days')::interval")

MECH = ("sl", "tp", "breakeven_tp", "exit_policy:sl_pct", "staged_tp2_clear",
        "max_hold_timeout", "exit_policy:trailing_callback")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", default="30")
    a = ap.parse_args(argv)
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    with psycopg.connect(DSN, autocommit=True) as c:
        cur = c.cursor()
        cur.execute("SET app.is_admin='on'")

        print("== A) 止损单的 MAE（引擎记录）—— cap 放宽能否救回 ==")
        cur.execute(
            """select id, symbol, side, round(trough_pnl_pct::numeric*100,3), round(peak_pnl_pct::numeric*100,3),
                      round(((close_price-entry_price)/entry_price*100*case when left(side,1)='l' then 1 else -1 end)::numeric,3),
                      round((coalesce(partial_realized_pnl,0)+coalesce(unrealized_pnl,0))::numeric,2),
                      round(coalesce(partial_fee_paid,0)::numeric,2)
               from paper_positions where """ + WHERE + " and close_reason='sl' order by trough_pnl_pct",
            (a.days,),
        )
        rows = cur.fetchall()
        for r in rows:
            print("   id=%-5s %-8s %-6s MAE=%-8s MFE=%-8s 实收%%=%-8s USD=%-8s fee=%s" % r)
        for cap in (2.0, 2.5, 3.0, 4.5):
            n = sum(1 for r in rows if r[3] is not None and float(r[3]) > -cap)
            print("   ⇒ cap=%.1f%% 时不会被止损打到: %d/%d 笔" % (cap, n, len(rows)))
        surv = [r for r in rows if r[3] is not None and float(r[3]) > -2.0]
        if surv:
            print("      这些笔 MFE 中位=%s%% 合计实收=%s USD（若它们活下来，上限≈MFE，下限≈回落到保本）"
                  % (round(st.median([float(r[4]) for r in surv]), 3), sum(float(r[6]) for r in surv)))

        print()
        print("== A2) 止损成交价 vs 触发线（穿透，仅取 sl_price 尚未被追踪改写的样本）==")
        cur.execute(
            """select id, symbol, round(((close_price-sl_price)/entry_price*100*case when left(side,1)='l' then 1 else -1 end)::numeric,4) pen_pp,
                      round((sl_price/entry_price)::numeric,5) sl_over_entry
               from paper_positions where """ + WHERE + """ and close_reason='sl'
                 and sl_price is not null and entry_price > 0""",
            (a.days,),
        )
        pens = [(r[0], r[1], float(r[2])) for r in cur.fetchall()]
        for r in pens:
            print("   id=%-5s %-8s 成交比止损线差=%-9s%% (sl/entry=%s)" % (r[0], r[1], r[2], r[3] if len(r) > 3 else "-"))
        pl = [p[2] for p in pens]
        if pl:
            print("   ⇒ 穿透中位=%s pp, 均值=%s pp, 最差=%s pp（负=成交比线更差）"
                  % (round(st.median(pl), 4), round(st.mean(pl), 4), round(min(pl), 4)))

        print()
        print("== B) breakeven_tp 的 MFE：锁利线让出多少 ==")
        cur.execute(
            """select count(*), round(avg(peak_pnl_pct::numeric*100),3), round(min(peak_pnl_pct::numeric*100),3),
                      round(max(peak_pnl_pct::numeric*100),3),
                      sum(case when peak_pnl_pct*100 >= 3 then 1 else 0 end),
                      sum(case when peak_pnl_pct*100 >= 5 then 1 else 0 end),
                      round(avg((close_price-entry_price)/entry_price*100*case when left(side,1)='l' then 1 else -1 end)::numeric,3),
                      round(sum(coalesce(partial_realized_pnl,0)+coalesce(unrealized_pnl,0))::numeric,2)
               from paper_positions where """ + WHERE + " and close_reason='breakeven_tp'",
            (a.days,),
        )
        n, amfe, mn, mx, ge3, ge5, areal, tot = cur.fetchone()
        print("   n=%s 均MFE=%s%% (min %s / max %s)  ≥3%%: %s 笔  ≥5%%: %s 笔" % (n, amfe, mn, mx, ge3, ge5))
        print("   均落袋=%s%%  合计=%s USD   ⇒ 未兑现浮盈上界 ≈ (均MFE−均落袋)×名义" % (areal, tot))

        print()
        print("== C) 出场通道汇总（实际口径）==")
        cur.execute(
            "select close_reason, count(*), "
            "round(sum(coalesce(partial_realized_pnl,0)+coalesce(unrealized_pnl,0))::numeric,2), "
            "round(avg((close_price-entry_price)/entry_price*100*case when left(side,1)='l' then 1 else -1 end)::numeric,3), "
            "round(avg(coalesce(partial_fee_paid,0))::numeric,3) "
            "from paper_positions where " + WHERE + " group by 1 order by 3", (a.days,),
        )
        groups = {}
        for reason, n2, usd, exp, fee in cur.fetchall():
            key = ("机械:" + reason) if reason in MECH else (
                "策略:" + reason if str(reason).startswith("exit_policy") else (
                    "信号:主动平仓" if str(reason).split(":")[0].split(" ")[0] in
                    ("thesis_should_close", "trend_broken", "trend_weaken", "trend_broken", "midlong",
                     "master_running_close", "reversal_4h", "profit_drawdown_full", "profit_drawdown_hard",
                     "profit_drawdown_stage", "thesis_invalidation", "thesis_long_propagate", "dust_cleanup")
                    else "其他:" + str(reason)[:28]))
            g = groups.setdefault(key, [0, 0.0, []])
            g[0] += n2
            g[1] += float(usd)
            g[2].append((n2, float(exp)))
        for k, (n3, usd3, exps) in sorted(groups.items(), key=lambda kv: kv[1][1]):
            wavg = sum(n4 * e for n4, e in exps) / max(1, sum(n4 for n4, _ in exps))
            print("   %-34s n=%-4d USD=%-9.2f 均=%+.3f%%" % (k, n3, usd3, wavg))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
