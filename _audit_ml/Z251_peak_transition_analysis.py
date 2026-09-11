# -*- coding: utf-8 -*-
"""[§86 度量 2026-09-11 / 目标①] 把"先盈利后大亏离场"这句直觉**逐层量化**（只读）。

用户原始痛点："还是先盈利后大亏离场"。本脚本用 30 天 mid/long 已平仓数据回答三个问题：

  Q1 **资金到底漏在哪一段？** 按 `peak_pnl_pct`（持仓期最大浮盈）分桶，看每桶的
     笔数 / 净额 / 胜率 / 平均持有 / 平均回吐（peak − 最终收益率）。
  Q2 **"先盈利后大亏"占多少？** 即"曾浮盈 ≥X% 却以亏损收场"的笔数与金额占比。
  Q3 **逐层差异**：mid 与 long 分开看（目标① 要求"逐通道/逐层"）。

反事实（只做**减法**，不做因果宣称）：
  * `净额(peak<K)` = "如果这些从未开过仓"可避免的金额（下界估计，忽略机会成本）；
  * `净额(peak≥K)` = 其余部分，用来判断问题在**选股/入场**还是在**出场**。

口径：净额 = `(close-entry)*size*dir + partial_realized_pnl - partial_fee_paid`；
`peak_pnl_pct` 为持仓期最大浮盈百分比；`ret_pct` 为最终收益率（同方向口径）。
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
from dotenv import load_dotenv  # noqa: E402

load_dotenv(str(ROOT / ".env"), override=False)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _scope import account_clause, describe_scope  # noqa: E402

ACCT = account_clause()   # [§90/#73] 账户隔离（默认 account_id=14，AUDIT_ACCOUNT_ID=0 关闭）

from backend.database.connection import SessionLocal  # noqa: E402
from backend.services.exit.channel_breaker_gate import channel_of, is_protected  # noqa: E402
from sqlalchemy import text  # noqa: E402

NET = ("(case when lower(side) in ('long','buy') then (close_price-entry_price)*size "
       "else -(close_price-entry_price)*size end) "
       "+ coalesce(partial_realized_pnl,0) - coalesce(partial_fee_paid,0)")
RET = ("(case when lower(side) in ('long','buy') then (close_price-entry_price)/entry_price "
       "else (entry_price-close_price)/entry_price end)")

BUCKETS = [("<0.5%", -9, 0.005), ("0.5–1%", 0.005, 0.01), ("1–2%", 0.01, 0.02),
           ("2–5%", 0.02, 0.05), ("≥5%", 0.05, 9)]


def _bucket(p):
    for name, lo, hi in BUCKETS:
        if lo <= p < hi:
            return name
    return "?"


def main() -> int:
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    db = SessionLocal()
    try:
        db.execute(text("set app.is_admin='on'"))
        rows = db.execute(text(f"""
            select upper(symbol), lower(coalesce(timeframe_tier,'?')), coalesce(trade_nature,''),
                   coalesce(close_reason,'?'), {NET} as net, coalesce(peak_pnl_pct,0) as peak,
                   {RET} as ret, coalesce(expected_hold_hours,0),
                   case when opened_at is null then null
                        else extract(epoch from (closed_at-opened_at))/3600.0 end as hold_h
            from paper_positions
            where status='closed'{ACCT} and closed_at >= now() - interval '{days} day'
              and lower(coalesce(timeframe_tier,'')) in ('mid','long')
              and entry_price is not null and entry_price > 0
        """)).fetchall()
    finally:
        db.close()

    items = [{"sym": r[0], "tier": r[1], "nature": r[2], "channel": channel_of(r[3]),
              "net": float(r[4] or 0), "peak": float(r[5] or 0), "ret": float(r[6] or 0),
              "exp_hold": float(r[7] or 0),
              "hold": float(r[8]) if r[8] is not None else None} for r in rows]
    print(describe_scope())
    print("=" * 100)
    print(f"「先盈利后大亏」逐层量化（近 {days} 天 mid/long，n={len(items)}，净口径）")
    print("=" * 100)

    def agg(xs):
        n = len(xs)
        if not n:
            return dict(n=0, net=0.0, avg=0.0, wr=0.0, give=0.0, hold=0.0, ret=0.0)
        holds = [x["hold"] for x in xs if x["hold"] is not None]
        return dict(n=n, net=sum(x["net"] for x in xs), avg=sum(x["net"] for x in xs) / n,
                    wr=sum(1 for x in xs if x["net"] > 0) / n,
                    give=sum(x["peak"] - x["ret"] for x in xs) / n * 100,
                    hold=(sum(holds) / len(holds)) if holds else 0.0,
                    ret=sum(x["ret"] for x in xs) / n * 100)

    print("\nQ1. 按**持仓期最大浮盈**分桶（全部 mid+long）")
    print(f"  {'peak 桶':10s} {'笔数':>5} {'占比':>6} {'净额$':>10} {'单笔$':>7} {'胜率':>6} "
          f"{'最终收益':>8} {'回吐pt':>7} {'平均持有h':>9}")
    tot = len(items)
    for name, lo, hi in BUCKETS:
        xs = [x for x in items if lo <= x["peak"] < hi]
        a = agg(xs)
        print(f"  {name:10s} {a['n']:>5} {100.0*a['n']/max(1,tot):>5.1f}% {a['net']:>10.2f} "
              f"{a['avg']:>7.2f} {a['wr']*100:>5.1f}% {a['ret']:>7.2f}% {a['give']:>7.2f} {a['hold']:>9.1f}")

    lo_net = sum(x["net"] for x in items if x["peak"] < 0.02)
    hi_net = sum(x["net"] for x in items if x["peak"] >= 0.02)
    print(f"\n  反事实（只做减法）：从未开过 `peak<2%` 的仓 ⇒ 去掉 {lo_net:+.2f}；"
          f"其余部分 = {hi_net:+.2f}")
    print(f"  ⇒ 结论方向：{'资金主要漏在「从未走出来的仓」' if lo_net < hi_net else '资金主要漏在「曾盈利的仓」'}")

    print("\nQ2. 「先盈利后大亏」到底占多少？（曾浮盈 ≥1%/≥2%/≥5% 却以亏损收场）")
    for th in (0.01, 0.02, 0.05):
        bad = [x for x in items if x["peak"] >= th and x["net"] < 0]
        healed = [x for x in items if x["peak"] >= th and x["net"] >= 0]
        loss = sum(x["net"] for x in bad)
        gain = sum(x["net"] for x in healed)
        tot_loss = abs(sum(x["net"] for x in items if x["net"] < 0))
        print(f"  peak≥{th*100:>4.0f}%：{len(bad):>3} 笔转亏（合计 {abs(loss):>8.2f}）｜"
              f"{len(healed):>3} 笔保住（合计 {gain:>8.2f}）｜占全部亏损额 "
              f"{100.0*abs(loss)/max(1e-9,tot_loss):>5.1f}%")
        if bad:
            chans = defaultdict(lambda: [0, 0.0])
            for x in bad:
                chans[x["channel"]][0] += 1
                chans[x["channel"]][1] += x["net"]
            top = sorted(chans.items(), key=lambda kv: kv[1][1])[:3]
            print(f"       转亏最多的通道：" + "；".join(
                f"{c}({n} 笔 {v:.2f})" for c, (n, v) in top))

    print("\nQ3. 逐层对比（mid vs long）")
    for tier in ("mid", "long"):
        xs = [x for x in items if x["tier"] == tier]
        a = agg(xs)
        if not a["n"]:
            continue
        lo = [x for x in xs if x["peak"] < 0.02]
        print(f"  {tier:5s} n={a['n']:>3} 净额 {a['net']:>9.2f} 单笔 {a['avg']:>6.2f} "
              f"胜率 {a['wr']*100:>5.1f}% 回吐 {a['give']:>5.2f}pt 持有 {a['hold']:>5.1f}h"
              f"｜peak<2% 占 {100.0*len(lo)/max(1,a['n']):>5.1f}%（净额 {sum(x['net'] for x in lo):>8.2f}）")

    print("\nQ5. 逐层**盈亏赔率**（为什么 long 胜率更高却更亏）")
    print(f"  {'层':5s} {'胜率':>6} {'平均盈利$':>10} {'平均亏损$':>10} {'赔率':>6} "
          f"{'最大单笔亏$':>11} {'亏损额90分位$':>13}")
    for tier in ("mid", "long"):
        xs = [x for x in items if x["tier"] == tier]
        wins = [x["net"] for x in xs if x["net"] > 0]
        losses = [x["net"] for x in xs if x["net"] < 0]
        if not xs:
            continue
        aw = sum(wins) / len(wins) if wins else 0.0
        al = sum(losses) / len(losses) if losses else 0.0
        srt = sorted(losses)
        p90 = srt[int(0.1 * len(srt))] if srt else 0.0     # 最差 10% 的分位（负值）
        print(f"  {tier:5s} {100.0*len(wins)/len(xs):>5.1f}% {aw:>10.2f} {al:>10.2f} "
              f"{(aw/abs(al) if al else 0):>6.2f} {min(losses or [0]):>11.2f} {p90:>13.2f}")
    print("  （赔率 = 平均盈利 / |平均亏损|；<1 说明赢得少亏得多 ⇒ 出场/止损结构问题）")

    print("\nQ6. long 层的亏损**由哪些通道造成**（按金额，含 peak 桶）")
    lg = [x for x in items if x["tier"] == "long" and x["net"] < 0]
    chans = defaultdict(list)
    for x in lg:
        chans[x["channel"]].append(x)
    for ch, xs in sorted(chans.items(), key=lambda kv: sum(x["net"] for x in kv[1])):
        tot = sum(x["net"] for x in xs)
        peaks = [f"{x['peak']*100:.2f}%" for x in xs]
        holds = [x["hold"] for x in xs if x["hold"] is not None]
        print(f"  {ch:22s} {len(xs):>2} 笔 {tot:>8.2f}（单笔 {tot/len(xs):>6.2f}）"
              f"｜peak={','.join(peaks)}｜持有均值 {(sum(holds)/len(holds) if holds else 0):>5.1f}h")
    mid_lg = [x for x in items if x["tier"] == "mid" and x["net"] < 0]
    print(f"\n  对照：mid 层亏损 {len(mid_lg)} 笔，平均单笔 "
          f"{sum(x['net'] for x in mid_lg)/max(1,len(mid_lg)):.2f}"
          f"；long 层亏损 {len(lg)} 笔，平均单笔 "
          f"{sum(x['net'] for x in lg)/max(1,len(lg)):.2f}"
          f" ⇒ 倍率 {abs((sum(x['net'] for x in lg)/max(1,len(lg))) / (sum(x['net'] for x in mid_lg)/max(1,len(mid_lg)))):.1f}×")

    print("\nQ4. 入场侧线索（哪些属性更常落到 peak<2% 桶）")
    by_nature = defaultdict(lambda: [0, 0, 0.0])
    for x in items:
        k = x["nature"] or "?"
        by_nature[k][0] += 1
        if x["peak"] < 0.02:
            by_nature[k][1] += 1
        by_nature[k][2] += x["net"]
    for k, (n, lo_n, net) in sorted(by_nature.items(), key=lambda kv: -kv[1][0]):
        print(f"  nature={k:14s} n={n:>3} 其中 peak<2% {lo_n:>3}（{100.0*lo_n/max(1,n):>5.1f}%）"
              f"｜净额 {net:>9.2f}")
    print("\n⚠️ 口径限制：`peak_pnl_pct` 是持仓期最大浮盈（未记录到达时间），因此本表不能回答"
          "\n   “峰值出现在持有早期还是末期”；反事实只做减法，不含机会成本/因果宣称。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
