# -*- coding: utf-8 -*-
"""Z35：同向并发上限的反事实验证（针对昨晚 0.94x 全多敞口）。

规则：开仓时若**已持有 N 笔 mid/long 仓位**则跳过该笔（时间顺序贪婪回放，
被跳过的仓位不占用名额）。逐 N 评估：总 USD / 均值 / 胜率 / 模式率 / ≤-2% 笔数 /
最差单笔 / 逐月，并做 walk-forward 与 bootstrap；最后单独看昨晚窗口。
"""
from __future__ import annotations

import os
import random
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sqlalchemy import create_engine, text  # noqa: E402

URL = os.getenv("DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
SEED = 20260910
LASTNIGHT = ("2026-09-09 17:00:00+08", "2026-09-10 10:00:00+08")


def load(days=75):
    eng = create_engine(URL)
    with eng.connect() as c:
        c.execute(text("set app.is_admin='on'"))
        rows = [dict(r._mapping) for r in c.execute(text(f"""
            select id, symbol, timeframe_tier, trade_nature, entry_price, original_size, size,
                   peak_pnl_pct, unrealized_pnl, partial_realized_pnl, partial_fee_paid,
                   opened_at, closed_at, status
            from paper_positions
            where timeframe_tier in ('mid','long')
              and (status='open' or (status='closed'
                   and closed_at >= now() - interval '{int(days)} days'))
            order by opened_at
        """)).fetchall()]
    out = []
    for p in rows:
        sz0 = float(p["original_size"] or p["size"] or 0)
        entry = float(p["entry_price"] or 0)
        if sz0 <= 0 or entry <= 0 or not p["opened_at"]:
            continue
        is_open = str(p["status"]) == "open"
        out.append({
            "id": p["id"], "symbol": p["symbol"], "tier": str(p["timeframe_tier"]),
            "notional0": sz0 * entry,
            "usd": (float(p["unrealized_pnl"] or 0) + float(p["partial_realized_pnl"] or 0)
                    - float(p["partial_fee_paid"] or 0)),
            "peak": float(p["peak_pnl_pct"] or 0) * 100,
            "open_ts": int(p["opened_at"].timestamp()),
            "close_ts": (10 ** 12 if is_open
                         else int(p["closed_at"].timestamp())),
            "mon": str(p["opened_at"])[:7],
            "opened": str(p["opened_at"])[:19],
            "is_open": is_open,
        })
    out.sort(key=lambda r: r["open_ts"])
    return out


def replay(recs, cap):
    """时间顺序贪婪回放：已持有 ≥cap 笔时跳过该笔。

    仍持仓（status=open）的仓位**永远占用名额且不会被跳过**（它们已经真实发生了）。
    被跳过的仓位不占用名额。
    """
    if cap is None:
        return [r for r in recs if not r["is_open"]], [r for r in recs if r["is_open"]]
    open_list, keep, skip = [], [], []
    for r in recs:
        open_list = [x for x in open_list if x["close_ts"] > r["open_ts"]]
        if r["is_open"]:
            open_list.append(r)          # 真实持仓：占名额、不参与取舍
            continue
        if len(open_list) >= cap:
            skip.append(r)
        else:
            keep.append(r)
            open_list.append(r)
    return keep, skip


def agg(sub):
    if not sub:
        return None
    pcts = [r["usd"] / r["notional0"] * 100 for r in sub]
    pat = sum(1 for r in sub if r["peak"] >= 0.5 and r["usd"] < 0)
    return {"n": len(sub), "usd": sum(r["usd"] for r in sub),
            "mean": sum(pcts) / len(pcts),
            "win": sum(1 for x in pcts if x > 0) / len(pcts),
            "pat": pat / len(sub),
            "le2": sum(1 for x in pcts if x <= -2),
            "worst": min(r["usd"] for r in sub)}


def main() -> int:
    recs_all = load(75)
    recs = [r for r in recs_all if not r["is_open"]]
    n_open = len(recs_all) - len(recs)
    print(f"已平仓样本 n={len(recs)}（另含仍持仓 {n_open} 笔，占名额）")
    base = agg(recs)
    print(f"样本 n={len(recs)}  基线 USD={base['usd']:+.2f} 均值={base['mean']:+.3f}% "
          f"模式率={base['pat']:.3f} ≤-2%={base['le2']} 最差单笔={base['worst']:+.2f}")

    print(f"\n=== 逐档：同向并发上限 N ===")
    print(f"{'N':<8}{'保留n':>6}{'总USD':>10}{'均值%':>9}{'胜率':>7}{'模式率':>8}"
          f"{'≤-2%':>7}{'最差单笔':>10}  逐月USD")
    for cap in (2, 3, 4, 5, 6, None):
        keep, skip = replay(recs_all, cap)
        a = agg(keep)
        bym = defaultdict(float)
        for r in keep:
            bym[r["mon"]] += r["usd"]
        bym_s = " ".join(f"{m[5:]}:{v:+.0f}" for m, v in sorted(bym.items()))
        print(f"{str(cap):<8}{a['n']:>6}{a['usd']:>+10.2f}{a['mean']:>+9.3f}{a['win']:>7.3f}"
              f"{a['pat']:>8.3f}{a['le2']:>7}{a['worst']:>+10.2f}  {bym_s}")

    print("\n=== 被跳过的笔（cap=4 与 cap=3）的特征 ===")
    for cap in (4, 3, 2):
        _keep, skip = replay(recs_all, cap)
        if not skip:
            continue
        a = agg(skip)
        print(f"  cap={cap}: 跳过 {a['n']} 笔 合计 USD={a['usd']:+.2f} 均值={a['mean']:+.3f}% "
              f"模式率={a['pat']:.3f} ≤-2%={a['le2']} 最差={a['worst']:+.2f}")

    print("\n=== 昨晚窗口（9/9 17:00 起）在各档下的取舍 ===")
    for cap in (None, 6, 5, 4, 3, 2):
        keep, skip = replay(recs_all, cap)
        kid = {r["id"] for r in keep}
        ln = [r for r in recs if r["opened"] >= "2026-09-09 17:00:00"]
        kept = [r for r in ln if r["id"] in kid]
        print(f"  cap={str(cap):<5} 昨晚 {len(ln)} 笔 → 保留 {len(kept)} 笔 "
              f"保留部分 USD={sum(r['usd'] for r in kept):+.2f}（实际 "
              f"{sum(r['usd'] for r in ln):+.2f}）")

    print("\n=== walk-forward：前 2/3 选 N → 后 1/3 验证 ===")
    cut = int(len(recs_all) * 2 / 3)
    tr_all, te_all = recs_all[:cut], recs_all[cut:]
    tr = [r for r in tr_all if not r["is_open"]]
    te = [r for r in te_all if not r["is_open"]]
    best = None
    for cap in (2, 3, 4, 5, 6, None):
        keep, _ = replay(tr_all, cap)
        u = sum(r["usd"] for r in keep)
        if best is None or u > best[1]:
            best = (cap, u)
    print(f"  训练最优 cap={best[0]}（训练 ${best[1]:+.2f} vs 实际 "
          f"${sum(r['usd'] for r in tr):+.2f}）")
    for cap in (None, best[0]):
        keep, _ = replay(te_all, cap)
        print(f"  验证 cap={str(cap):<5} n={len(keep)} USD={sum(r['usd'] for r in keep):+.2f}")
    # bootstrap：被跳过的笔 pct 均值
    _k, skip_te = replay(te_all, best[0])
    if len(skip_te) >= 5:
        d = [r["usd"] / r["notional0"] * 100 for r in skip_te]
        rnd = random.Random(SEED)
        n = len(d)
        boots = sorted(sum(d[rnd.randrange(n)] for _ in range(n)) / n for _ in range(4000))
        lo, hi = boots[int(0.025 * 4000)], boots[int(0.975 * 4000)]
        print(f"  验证集被跳过 {n} 笔均值={sum(d)/n:+.4f}% CI[{lo:+.4f},{hi:+.4f}] "
              f"{'显著为负（限并发有效）' if hi < 0 else '不显著(跨0)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
