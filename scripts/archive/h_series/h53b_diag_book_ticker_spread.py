"""诊断：asterdex_book_ticker 的价差是不是真的只有 0.012bp？

H53 打印出 BTC 价差中位 **0.012bp**、ETH **0.039bp**。
若为真 ⇒ H52 的 δ=0.05bp「进价差内」报价**越过了对侧最优价**，
`主动卖价 ≤ 我们报价` 这个判据会把几乎所有成交都算给我们
⇒ **H52 的 +0.3902bp 可能是判定伪影，而不是真实边际。**

这必须先查清，否则整个结论是悬空的（第 17/18 条教训：物理假设不核对就报数）。

本脚本做三件事：
  1. 打印 book_ticker 的**原始行**（未经任何加工）
  2. 把 book_ticker 的 b/a 与 `asterdex_depth_snapshots` 的 20 档 bids/asks 最优价**交叉核对**
  3. 打印价差分位数与**一整个 tick 的价格变动分布**，判断涨跌停/离散度是否自洽
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")


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

    cn = psycopg2.connect(_dsn("alpha_market"))
    cn.autocommit = True
    cur = cn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    for vs in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
        print("=" * 88)
        print(f"【{vs}】")
        print("=" * 88)

        # ---- 1) 原始行 ----
        cur.execute(
            "SELECT * FROM asterdex_book_ticker WHERE symbol=%s"
            " ORDER BY event_ts_ms DESC LIMIT 5",
            (vs,),
        )
        rows = cur.fetchall()
        if not rows:
            print("  无数据")
            continue
        print("\n  book_ticker 最近 5 行（原始）：")
        for r in rows:
            print("   ", {k: str(v)[:22] for k, v in r.items()})

        # ---- 2) 价差分位数 ----
        cur.execute(
            "SELECT event_ts_ms, bid_px::float b, ask_px::float a,"
            "       bid_qty::float bq, ask_qty::float aq"
            "  FROM asterdex_book_ticker WHERE symbol=%s"
            "   AND event_ts_ms > (extract(epoch from now())*1000)::bigint - 7200000"
            " ORDER BY event_ts_ms",
            (vs,),
        )
        d = cur.fetchall()
        if len(d) < 100:
            print("  两小时数据不足")
            continue
        bt = np.array([r["event_ts_ms"] for r in d], dtype=np.int64)
        b = np.array([r["b"] for r in d])
        a = np.array([r["a"] for r in d])
        bq = np.array([r["bq"] for r in d])
        aq = np.array([r["aq"] for r in d])
        mid = 0.5 * (b + a)
        sp = (a - b) / mid * 1e4
        ok = (b > 0) & (a > b)
        print(f"\n  样本 {len(d):,} 行（近 2h）  有效 {int(ok.sum()):,}"
              f"   a<=b 的异常行 {int((a <= b).sum()):,}")
        if ok.sum() < 10:
            print("  ⇒ 绝大多数行 a <= b ⇒ 这两列不是买一/卖一")
            continue
        for p in (1, 5, 25, 50, 75, 95, 99):
            print(f"    价差 p{p:<2} = {np.percentile(sp[ok], p):.4f} bp")
        print(f"    (a-b)/mid 的最小值 = {sp[ok].min():.6f} bp，最大值 = {sp[ok].max():.4f} bp")
        print(f"    b 的量 qty: 中位 {np.median(bq[ok]):.6f}   a 的量 qty: 中位 {np.median(aq[ok]):.6f}")
        print(f"    mid 中位 = {np.median(mid[ok]):.4f}   b 中位 = {np.median(b[ok]):.4f}")

        # ---- 3) 与 depth_snapshots 的 20 档最优价交叉核对 ----
        cur.execute(
            "SELECT event_ts_ms, bids, asks FROM asterdex_depth_snapshots"
            " WHERE symbol=%s AND event_ts_ms > (extract(epoch from now())*1000)::bigint - 7200000"
            " ORDER BY event_ts_ms DESC LIMIT 5",
            (vs,),
        )
        ds = cur.fetchall()
        print(f"\n  depth_snapshots 最近 {len(ds)} 行（20 档 jsonb 的档 0）：")
        for r in ds:
            bids = r["bids"]
            asks = r["asks"]
            try:
                b0 = bids[0] if isinstance(bids, list) and bids else None
                a0 = asks[0] if isinstance(asks, list) and asks else None
            except Exception as e:
                b0 = a0 = f"解析失败 {e}"
            print(f"    ts={r['event_ts_ms']}  bids[0]={b0}  asks[0]={a0}")

        # 交叉核对：同一时刻附近 book_ticker 与 depth 的 b/a
        if ds:
            t_d = int(ds[0]["event_ts_ms"])
            j = int(np.searchsorted(bt, t_d, side="right")) - 1
            if j >= 0:
                print(f"\n  交叉核对（depth 最新 ts={t_d}，找 book_ticker 最近一笔 ts={bt[j]}，"
                      f"相差 {abs(t_d - bt[j])} ms）：")
                print(f"    book_ticker : bid={b[j]:.4f}  ask={a[j]:.4f}  "
                      f"价差={(a[j]-b[j])/mid[j]*1e4:.4f}bp")
                try:
                    print(f"    depth snap  : bid={float(ds[0]['bids'][0][0]):.4f}  "
                          f"ask={float(ds[0]['asks'][0][0]):.4f}")
                except Exception as e:
                    print(f"    depth snap 解析失败：{e}")

        # ---- 4) 自洽性：成交价是否落在 [b, a] 内 ----
        cur.execute(
            "SELECT price::float p FROM asterdex_trades WHERE symbol=%s"
            "   AND event_ts_ms > (extract(epoch from now())*1000)::bigint - 7200000"
            " ORDER BY event_ts_ms LIMIT 20000",
            (vs,),
        )
        tr = cur.fetchall()
        if tr:
            tp = np.array([r["p"] for r in tr])
            print(f"\n  成交价区间（近 2h 前 2 万笔）：min {tp.min():.4f}  max {tp.max():.4f}")
            print(f"    买一区间：min {b[ok].min():.4f}  max {b[ok].max():.4f}")
            print(f"    卖一区间：min {a[ok].min():.4f}  max {a[ok].max():.4f}")
            # 成交价落在 [b,a] 内的比例（用最近快照）
            k = np.searchsorted(bt, np.array([0]), side="right")
            cur.execute(
                "SELECT event_ts_ms FROM asterdex_trades WHERE symbol=%s"
                "   AND event_ts_ms > (extract(epoch from now())*1000)::bigint - 7200000"
                " ORDER BY event_ts_ms LIMIT 20000",
                (vs,),
            )
        print()

    cn.close()
    print("=" * 88)
    print("判读要点：")
    print("  · 若 book_ticker 的 a>b 且价差 ~0.01bp ⇒ 该列**不是**真实盘口（可能是标记价/指数价）")
    print("  · 若 depth_snapshots 的档 0 价差 ~1bp 而 book_ticker ~0.01bp ⇒ **两者不同源**")
    print("  · 若成交价大量落在 [b,a] 之外 ⇒ book_ticker 与成交流不一致")
    print("=" * 88)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
