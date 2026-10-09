"""H107：按币种找"亏损集中点" —— 决定该砍掉哪些币。

# H105/H106 的整夜结论

```
整夜累计（完整口径 pnl+fee）：**-52.20 USD**  （10 个小时全是负的）
  paper_pnl  fill     5023 行  **-0.1274**  = **-0.002 bp/行**  ← **入场腿已归零**
  paper_pnl  flatten   393 行  -32.8139
  paper_fee  flatten   395 行  -19.2571
```

**按币种（今日）**
```
ONDO   -10.41     SOL    -7.51     UNI     -3.26
XRP     -9.32     DOGE   -6.33     VIRTUAL -1.01
ARB     -8.95     ASTER  -4.50     PENDLE  -0.78
```

**强平率演化**
```
① 60s 上限（03:20 前）  周期 634   强平率 **23.2%**
② F291 120s（03:20-34） 周期  58   强平率 10.3%
③ F292 0.30（03:34 后） 周期 1411  强平率 **13.0%**  ← F292 没改善
```

# 本脚本做什么（给出可执行的调整）

对每个币算：周期数、强平率、入场腿贡献、强平腿成本、**净贡献**
⇒ 找出"净贡献显著为负"的币 ⇒ 建议移出宇宙

判据（事先定死）：
  · 若某币净贡献 < −$5 且周期数 ≥50 ⇒ **建议移出**（不是碰运气的小样本）
  · 若亏损均匀分布在各币 ⇒ 说明是**策略性**问题，砍币无用

用法：
    .venv\\Scripts\\python.exe scripts\\h107_symbol_attribution.py
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env", override=False)

NOTIONAL = 135.0
DAY = "2026-09-21 00:00:00"


def _dsn(db: str) -> str:
    url = os.getenv("DATABASE_URL") or ""
    for p in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(p, "")
    head, _, _ = url.rpartition("/")
    return head + "/" + db


def main():
    import numpy as np
    import psycopg2
    import psycopg2.extras

    from h84_derive_episodes import derive, load

    print("=" * 104)
    print("H107  按币种归因（整夜 00:00 ~ 现在）")
    print("=" * 104)

    eps = derive(load())
    bysym = defaultdict(list)
    for e in eps:
        bysym[e["sym"]].append(e)

    cn = psycopg2.connect(_dsn("alpha_arena"))
    cn.autocommit = True
    cur = cn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        "SELECT metadata_json::jsonb->>'symbol' sym, action,"
        "       COALESCE(metadata_json::jsonb->>'phase','?') ph,"
        "       count(*) n, COALESCE(SUM(amount_usd),0) s"
        "  FROM arbitrage_paper_ledgers"
        " WHERE account_id=101 AND action<>'create_account'"
        "   AND metadata_json::jsonb->>'lane_id'='mm_asterdex'"
        "   AND created_at >= %s GROUP BY 1,2,3", (DAY,))
    led = defaultdict(lambda: defaultdict(float))
    ledn = defaultdict(lambda: defaultdict(int))
    for r in cur.fetchall():
        led[r["sym"]][(r["action"], r["ph"])] += float(r["s"])
        ledn[r["sym"]][(r["action"], r["ph"])] += int(r["n"])
    cn.close()

    print(f"\n  {'币':<10} {'周期':>6} {'强平率':>8} {'入场腿USD':>11} "
          f"{'强平pnl':>11} {'强平fee':>10} {'净贡献':>11} {'判定':<10}")
    print("  " + "-" * 96)
    rows = []
    for s in sorted(set(list(bysym.keys()) + list(led.keys()))):
        es = bysym.get(s, [])
        n_ep = len(es); n_fl = sum(1 for e in es if e["flat"])
        fr = n_fl / n_ep if n_ep else float("nan")
        fill = led[s].get(("paper_pnl", "fill"), 0.0)
        flp = led[s].get(("paper_pnl", "flatten"), 0.0)
        flf = led[s].get(("paper_fee", "flatten"), 0.0)
        net = fill + flp + flf
        rows.append((s, n_ep, fr, fill, flp, flf, net))

    for s, n_ep, fr, fill, flp, flf, net in sorted(rows, key=lambda x: x[6]):
        verdict = ""
        if net < -5 and n_ep >= 50:
            verdict = "**建议移出**"
        elif net < -1:
            verdict = "偏亏"
        elif net > 0:
            verdict = "正贡献"
        fstr = f"{fr*100:.1f}%" if np.isfinite(fr) else "-"
        print(f"  {s:<10} {n_ep:>6} {fstr:>8} {fill:>+11.4f} {flp:>+11.4f} "
              f"{flf:>+10.4f} {net:>+11.4f} {verdict:<10}")

    print("\n" + "=" * 104)
    print("汇总")
    print("=" * 104)
    tot = sum(r[6] for r in rows)
    bad = [r for r in rows if r[6] < -5 and r[1] >= 50]
    print(f"\n  全部币净贡献合计 = **{tot:+.4f} USD**")
    print(f"  净贡献 <−$5 且周期 ≥50 的币：**{[r[0] for r in bad]}**")
    if bad:
        cut = sum(r[6] for r in bad)
        print(f"  这些币合计贡献 **{cut:+.4f} USD**（占全部的 {cut/tot*100:.1f}%）")
        print(f"  ⇒ 若把它们移出宇宙，理论上能把整夜从 {tot:+.2f} 改善到 "
              f"**{tot-cut:+.2f} USD**")
    print(f"\n  各币强平率：")
    for s, n_ep, fr, fill, flp, flf, net in sorted(rows, key=lambda x: -(x[2] if np.isfinite(x[2]) else 0)):
        if n_ep >= 20:
            print(f"    {s:<10} {fr*100:>5.1f}%  (n={n_ep})")

    print("\n" + "=" * 104)
    print("判读")
    print("=" * 104)
    print("  ⚠️ 砍币前必须确认：这些币的亏损是**结构性**的，不是被少数几笔巨亏带偏的。")
    print("     建议下一步：对候选砍掉的币做**逐周期净额分布**（中位/p10/最亏），")
    print("     若中位也为负 ⇒ 结构性；若中位为正、被几笔拉偏 ⇒ 不能砍。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
