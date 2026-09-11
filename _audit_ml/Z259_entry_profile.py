# -*- coding: utf-8 -*-
"""[§89 分析 2026-09-11 / 目标①] "从未走出来"的入场画像：哪些属性与 `peak<2%` 相关？（只读）

背景（§86）：近 30 天 mid/long 的亏损主要集中在 **peak<2%** 的 151 笔（**−$411.58**，占笔数 79%），
而 peak≥2% 的 39 笔合计 **+$194.65**。所以"选谁进"比"怎么出"更值钱。本脚本在**现有数据**里
找可分层的入场属性（不改任何行为）：

  维度：symbol / nature / tier / strategy_id 前缀 / 杠杆桶 / 名义桶 / 入场小时（本地） / 预期持有
  指标：n、peak<2% 占比、净额、平均 peak、胜率、平均持有
  排序：按"最差组与最好组的 peak<2% 占比差"（分离度）排，便于优先看

⚠️ 口径：这是**关联**不是因果；样本 190 笔、单维度分组后更小，只作线索；
   入场时的市场状态（regime/score）不在 `paper_positions`，需另行取数。
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
from sqlalchemy import text  # noqa: E402

NET = ("(case when lower(side) in ('long','buy') then (close_price-entry_price)*size "
       "else -(close_price-entry_price)*size end) "
       "+ coalesce(partial_realized_pnl,0) - coalesce(partial_fee_paid,0)")


def bucket_leverage(x: float) -> str:
    if x <= 0:
        return "?"
    return "≤5x" if x <= 5 else ("5–10x" if x <= 10 else ">10x")


def bucket_notional(x: float) -> str:
    if x <= 0:
        return "?"
    for hi, name in ((150, "<$150"), (300, "$150–300"), (600, "$300–600"), (1e9, ">$600")):
        if x < hi:
            return name
    return "?"


def share_never_worked(xs) -> float:
    """`peak<2%` 占比（组内）。"""
    return (sum(1 for x in xs if x["peak"] < 0.02) / len(xs)) if xs else 0.0


def perm_p(groups, iters: int = 2000, seed: int = 0) -> float:
    """置换检验：观测到的"最差组−最好组 占比差"是否超出随机打标（单尾 p）。

    只在**组内 ≥3 笔**的组间比较（小组噪音不参与）；返回 `(ge+1)/(iters+1)`。
    """
    import random as _random

    rnd = _random.Random(seed)
    usable = [g for g in groups if len(g) >= 3]
    if len(usable) < 2:
        return 1.0
    obs = max(share_never_worked(g) for g in usable) - min(share_never_worked(g) for g in usable)
    pool = [x for g in usable for x in g]
    labels = [x["peak"] < 0.02 for x in pool]
    ge = 0
    for _ in range(int(iters)):
        rnd.shuffle(labels)
        i = 0
        spreads = []
        for g in usable:
            seg = labels[i:i + len(g)]
            i += len(g)
            spreads.append(sum(seg) / len(seg))
        if max(spreads) - min(spreads) >= obs:
            ge += 1
    return (ge + 1) / (int(iters) + 1)


def main() -> int:
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    db = SessionLocal()
    try:
        db.execute(text("set app.is_admin='on'"))
        rows = db.execute(text(f"""
            select upper(symbol), lower(coalesce(timeframe_tier,'?')), coalesce(trade_nature,'?'),
                   coalesce(strategy_id,'-'), coalesce(leverage,0), coalesce(size,0), entry_price,
                   coalesce(peak_pnl_pct,0), {NET} as net,
                   extract(hour from opened_at) as hr,
                   coalesce(expected_hold_hours,0),
                   case when opened_at is null then null
                        else extract(epoch from (closed_at-opened_at))/3600.0 end as hold_h
            from paper_positions
            where status='closed'{ACCT} and closed_at >= now() - interval '{days} day'
              and lower(coalesce(timeframe_tier,'')) in ('mid','long')
              and entry_price > 0 and close_price is not null
        """)).fetchall()
    finally:
        db.close()

    items = []
    for r in rows:
        notional = float(r[5] or 0) * float(r[6] or 0)
        items.append({"sym": r[0], "tier": r[1], "nature": r[2], "strat": r[3],
                      "lev": bucket_leverage(float(r[4] or 0)),
                      "notional": bucket_notional(notional),
                      "hour": f"{int(r[9]):02d}时" if r[9] is not None else "?",
                      "exp_hold": float(r[10] or 0),
                      "peak": float(r[7] or 0), "net": float(r[8] or 0),
                      "hold": float(r[11]) if r[11] is not None else None})
    n = len(items)
    lo = [x for x in items if x["peak"] < 0.02]
    hi = [x for x in items if x["peak"] >= 0.02]
    print(describe_scope())
    print("=" * 100)
    print(f"「从未走出来」入场画像（近 {days} 天 mid/long，n={n}）")
    print("=" * 100)
    print(f"总览：peak<2% {len(lo)} 笔（{100.0*len(lo)/n:.1f}%）净额 {sum(x['net'] for x in lo):.2f}"
          f"｜peak≥2% {len(hi)} 笔 净额 {sum(x['net'] for x in hi):.2f}")

    dims = ["sym", "nature", "tier", "lev", "notional", "hour"]
    for dim in dims:
        groups = defaultdict(list)
        for x in items:
            groups[x[dim]].append(x)
        stats = []
        for k, xs in groups.items():
            if len(xs) < 3:            # 少于 3 笔不单列（避免噪音）
                continue
            lo_n = sum(1 for x in xs if x["peak"] < 0.02)
            stats.append({"k": k, "n": len(xs), "lo_share": lo_n / len(xs),
                          "net": sum(x["net"] for x in xs),
                          "avg_peak": sum(x["peak"] for x in xs) / len(xs) * 100,
                          "wr": sum(1 for x in xs if x["net"] > 0) / len(xs)})
        if len(stats) < 2:
            continue
        stats.sort(key=lambda s: -s["lo_share"])
        sep = stats[0]["lo_share"] - stats[-1]["lo_share"]
        print(f"\n【{dim}】分离度 {sep*100:.0f}pt（最差组 {stats[0]['k']} vs 最好组 {stats[-1]['k']}）")
        print(f"  {'分组':14s} {'n':>4} {'peak<2%占比':>11} {'净额$':>10} {'均peak%':>8} {'胜率':>6}")
        for s in stats[:6]:
            print(f"  {str(s['k'])[:14]:14s} {s['n']:>4} {s['lo_share']*100:>10.1f}% "
                  f"{s['net']:>10.2f} {s['avg_peak']:>8.2f} {s['wr']*100:>5.1f}%")
        if len(stats) > 6:
            print(f"  …… 另有 {len(stats)-6} 组")
    print("\n【稳健性】两大线索的时段拆分 + 置换检验（防事后切片过拟合）")
    for dim in ("sym", "hour"):
        groups = defaultdict(list)
        for x in items:
            groups[x[dim]].append(x)
        p_all = perm_p(list(groups.values()))
        print(f"\n  【{dim}】置换检验 p≈{p_all:.3f}（2000 次；<0.05 才算超出随机）")
        g7 = defaultdict(list)
        for x in items[-60:]:        # 最近 60 笔近似"近 7 天"
            g7[x[dim]].append(x)
        for k in sorted(groups, key=lambda k: -share_never_worked(groups[k]))[:3]:
            print(f"    最差组 {k}: 全窗口 n={len(groups[k])} 占比 {share_never_worked(groups[k])*100:.0f}%"
                  f"｜最近 60 笔 n={len(g7.get(k, []))} 占比 {share_never_worked(g7.get(k, []))*100:.0f}%")
        for k in sorted(groups, key=lambda k: share_never_worked(groups[k]))[:2]:
            print(f"    最好组 {k}: 全窗口 n={len(groups[k])} 占比 {share_never_worked(groups[k])*100:.0f}%"
                  f"｜最近 60 笔 n={len(g7.get(k, []))} 占比 {share_never_worked(g7.get(k, []))*100:.0f}%")

    print("\n【反事实（只做减法）】若排除下述分组，净额变化：")
    for label, pred in (("symbol=BTC", lambda x: x["sym"] == "BTC"),
                        ("hour∈{01,16,22,05,09}", lambda x: x["hour"] in ("01时", "16时", "22时", "05时", "09时")),
                        ("notional>$600", lambda x: x["notional"] == ">$600")):
        sel = [x for x in items if pred(x)]
        print(f"  排除 {label}: 去掉 {len(sel)} 笔（净额 {sum(x['net'] for x in sel):+.2f}）"
              f" ⇒ 剩余 {sum(x['net'] for x in items if not pred(x)):+.2f}")

    print("\n⚠️ 关联≠因果；单组样本小（n≥3 才列），入场时的 regime/score 未纳入"
          "（`paper_positions` 无该字段，需另取 ai_decision_logs）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
