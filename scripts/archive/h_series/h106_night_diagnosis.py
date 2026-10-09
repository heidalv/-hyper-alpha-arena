"""H106：整夜的关键诊断 —— 入场腿已归零，全部亏损来自强平腿。

# H105 的整夜全景（2026-09-21 00:00 ~ 09:09）

**按小时（完整口径 pnl+fee）**
```
00:00  -4.75   03:00  -3.50   06:00  -3.40   09:00  -0.22
01:00  -2.84   04:00  -7.86   07:00  -2.27
02:00 -14.67   05:00  -3.75   08:00  -8.94
⇒ 累计 **-52.20 USD**（每一小时都是负的，没有一小时为正）
```

**按 phase**
```
paper_pnl  fill     5023 行   **-0.1274**   = **-0.002 bp/行**  ← **入场腿已归零**
paper_pnl  flatten   393 行   -32.8139      = -6.185 bp/行
paper_fee  flatten   395 行   **-19.2571**   = -3.611 bp/行
────────────────────────────────────────────────
合计                          **-52.1984 USD**
```

**⇒ 整夜的亏损 100% 来自强平腿（−52.07），入场腿贡献 −0.13（等于没有）。**

**按币种**：ONDO −10.41 / XRP −9.32 / ARB −8.95 / SOL −7.51 / DOGE −6.33 / ASTER −4.50 …

# 本脚本要回答的三个问题

  1. **F292（出库 0.30）有没有降低强平率？** 对比 F291期间 vs F292之后
  2. **入场腿为什么归零？** H102 测的是 +0.142 bp/行，现在 −0.002。
     是 F292 影响了入场，还是行情变了？
  3. **闸门挡掉了多少机会？** `vol_pause` 2978 次、`net_exposure` 1411 次

用法：
    .venv\\Scripts\\python.exe scripts\\h106_night_diagnosis.py
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
F291 = "2026-09-21 03:20:00"
F292 = "2026-09-21 03:34:00"


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

    print("=" * 100)
    print("H106  整夜关键诊断")
    print("=" * 100)

    # ── 1) 各配置期的强平率（用 H84 周期口径）──
    import datetime as dt
    eps = derive(load())
    t291 = dt.datetime(2026, 9, 21, 3, 20).timestamp()
    t292 = dt.datetime(2026, 9, 21, 3, 34).timestamp()
    now = dt.datetime.now().timestamp()
    print("\n  ── 1) 强平率演化（H84 周期口径）──")
    print(f"  {'配置期':<28} {'周期':>6} {'含强平':>7} {'强平率':>9} {'时长中位':>9}")
    print("  " + "-" * 66)
    segs = [("① 最初 60s 上限（03:20 前）", 0, t291),
            ("② F291 120s（03:20-03:34）", t291, t292),
            ("③ F292 出库0.30（03:34 后）", t292, now)]
    for lab, lo, hi in segs:
        sub = [e for e in eps if lo <= (e.get("ts0") or 0) < hi]
        if not sub:
            print(f"  {lab:<28} 无周期")
            continue
        f = sum(1 for e in sub if e["flat"])
        d = np.array([e["dur_s"] for e in sub])
        print(f"  {lab:<28} {len(sub):>6} {f:>7} {f/len(sub)*100:>8.1f}% "
              f"{np.median(d):>8.0f}s")

    # ── 2) 入场腿 vs 强平腿分期 ──
    cn = psycopg2.connect(_dsn("alpha_arena"))
    cn.autocommit = True
    cur = cn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    print("\n  ── 2) 各配置期：入场腿 / 强平腿（完整口径）──")
    print(f"  {'配置期':<28} {'入场腿USD':>11} {'入场bp/行':>10} "
          f"{'强平腿USD':>11} {'feeUSD':>10}")
    print("  " + "-" * 76)
    for lab, lo in (("① 03:20 前", "2026-09-21 00:00:00"),
                    ("② F291 期间", F291),
                    ("③ F292 之后", F292)):
        cur.execute(
            "SELECT COALESCE(metadata_json::jsonb->>'phase','?') ph, action,"
            "       count(*) n, COALESCE(SUM(amount_usd),0) s"
            "  FROM arbitrage_paper_ledgers"
            " WHERE account_id=101 AND action<>'create_account'"
            "   AND metadata_json::jsonb->>'lane_id'='mm_asterdex'"
            "   AND created_at >= %s GROUP BY 1,2", (lo,))
        d = defaultdict(float); n = defaultdict(int)
        for r in cur.fetchall():
            d[(r["action"], r["ph"])] += float(r["s"]); n[(r["action"], r["ph"])] += int(r["n"])
        fill = d[("paper_pnl", "fill")]; filln = max(n[("paper_pnl", "fill")], 1)
        fl_p = d[("paper_pnl", "flatten")]; fl_f = d[("paper_fee", "flatten")]
        print(f"  {lab:<28} {fill:>+11.4f} {fill/filln/NOTIONAL*1e4:>+10.3f} "
              f"{fl_p:>+11.4f} {fl_f:>+10.4f}")

    # ── 3) 强平率与单次成本 ──
    print("\n  ── 3) 每次强平的成本（各期）──")
    print(f"  {'配置期':<28} {'强平次数':>9} {'pnl/次':>10} {'fee/次':>10} {'合计/次':>10}")
    print("  " + "-" * 70)
    for lab, lo in (("① 03:20 前", "2026-09-21 00:00:00"),
                    ("② F291 期间", F291),
                    ("③ F292 之后", F292)):
        cur.execute(
            "SELECT COALESCE(SUM(amount_usd),0) s, count(*) n"
            "  FROM arbitrage_paper_ledgers"
            " WHERE account_id=101 AND action='paper_pnl'"
            "   AND metadata_json::jsonb->>'lane_id'='mm_asterdex'"
            "   AND metadata_json::jsonb->>'phase'='flatten'"
            "   AND created_at >= %s", (lo,))
        r1 = cur.fetchone()
        cur.execute(
            "SELECT COALESCE(SUM(amount_usd),0) s, count(*) n"
            "  FROM arbitrage_paper_ledgers"
            " WHERE account_id=101 AND action='paper_fee'"
            "   AND metadata_json::jsonb->>'lane_id'='mm_asterdex'"
            "   AND metadata_json::jsonb->>'phase'='flatten'"
            "   AND created_at >= %s", (lo,))
        r2 = cur.fetchone()
        nn = max(int(r1["n"]), 1)
        pp = float(r1["s"]) / nn; ff = float(r2["s"]) / max(int(r2["n"]), 1)
        print(f"  {lab:<28} {int(r1['n']):>9} {pp:>+10.5f} {ff:>+10.5f} "
              f"{pp+ff:>+10.5f}")

    cn.close()
    print("\n" + "=" * 100)
    print("判读")
    print("=" * 100)
    print("  · 若③的强平率低于② ⇒ F292（出库挂回 mid）有效")
    print("  · 若③的强平率**高于**② ⇒ F292 反而有害 ⇒ 应回滚到 0.95 或走另一方向")
    print("  · 入场腿 bp 已归零 ⇒ **不存在「入场赚钱」这回事**，全部盈亏由出库决定")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
