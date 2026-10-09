"""诊断：market_orderbook_snapshots.raw_levels 里有没有量？

实盘 tick（runner.py:1880）只读 `best_bid/best_ask`，该表**没有量列**
⇒ 实盘拿不到队列规模 ⇒ H59 的失衡择时闸**无法在实盘实现**（这是设计约束）。

但该表有一列 `raw_levels text`。若它已经是档位（价+量）的 JSON，
则**不需要改采集链路**就能拿到量 —— 这是最低成本的落地路径。

本脚本：打印几行 raw_levels 原文，判断结构。
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
    import psycopg2
    import psycopg2.extras

    cn = psycopg2.connect(_dsn("alpha_market"))
    cn.autocommit = True
    cur = cn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    print("=" * 92)
    print("raw_levels 结构与覆盖率")
    print("=" * 92)

    cur.execute(
        "SELECT count(*) n,"
        "       count(raw_levels) nn,"
        "       count(*) FILTER (WHERE raw_levels IS NOT NULL AND raw_levels <> '') nne,"
        "       count(bid_depth_5) nd5"
        "  FROM market_orderbook_snapshots WHERE exchange='asterdex'"
        "   AND timestamp > (extract(epoch from now())*1000)::bigint - 7200000")
    r = cur.fetchone()
    print(f"\n  近 2h asterdex：总行 {int(r['n']):,}   raw_levels 非 NULL {int(r['nn']):,}"
          f"   非空串 {int(r['nne']):,}   bid_depth_5 非 NULL {int(r['nd5']):,}")

    cur.execute(
        "SELECT symbol, timestamp, best_bid, best_ask, spread,"
        "       bid_depth_5, ask_depth_5, bid_depth_10, ask_depth_10,"
        "       bid_orders_count, ask_orders_count, raw_levels"
        "  FROM market_orderbook_snapshots"
        " WHERE exchange='asterdex' AND raw_levels IS NOT NULL AND raw_levels <> ''"
        " ORDER BY timestamp DESC LIMIT 3")
    rows = cur.fetchall()
    if not rows:
        print("\n  ⚠️ raw_levels 全为空 ⇒ 该列**不能**作为量的来源")
    for i, r in enumerate(rows):
        print(f"\n  ── 行 {i+1}  symbol={r['symbol']} ts={r['timestamp']}")
        print(f"     best_bid={r['best_bid']}  best_ask={r['best_ask']}  spread={r['spread']}")
        print(f"     bid_depth_5={r['bid_depth_5']}  ask_depth_5={r['ask_depth_5']}")
        print(f"     bid_depth_10={r['bid_depth_10']}  ask_depth_10={r['ask_depth_10']}")
        print(f"     bid_orders_count={r['bid_orders_count']}  ask_orders_count={r['ask_orders_count']}")
        rl = r["raw_levels"]
        print(f"     raw_levels（前 400 字符）: {str(rl)[:400]}")

    # depth_5 是不是量（若是，它就是一个可用的失衡来源！）
    print("\n" + "=" * 92)
    print("bid_depth_5 / ask_depth_5 的分布（若它们是量，可直接用来算失衡）")
    print("=" * 92)
    import numpy as np
    for s in ("BTC", "ETH", "SOL", "XRP", "DOGE"):
        cur.execute(
            "SELECT bid_depth_5::float bd, ask_depth_5::float ad,"
            "       bid_depth_10::float bd10, ask_depth_10::float ad10,"
            "       best_bid::float b, best_ask::float a, spread::float sp,"
            "       bid_orders_count bc, ask_orders_count ac"
            "  FROM market_orderbook_snapshots"
            " WHERE exchange='asterdex' AND symbol=%s AND bid_depth_5>0 AND ask_depth_5>0"
            "   AND timestamp > (extract(epoch from now())*1000)::bigint - 7200000",
            (s,))
        d = cur.fetchall()
        if len(d) < 50:
            print(f"\n  {s}: 数据不足（{len(d)} 行）")
            continue
        bd = np.array([x["bd"] for x in d]); ad = np.array([x["ad"] for x in d])
        b = np.array([x["b"] for x in d]); a = np.array([x["a"] for x in d])
        sp = np.array([x["sp"] for x in d])
        bc = np.array([x["bc"] for x in d]); ac = np.array([x["ac"] for x in d])
        print(f"\n  {s}  n={len(d):,}")
        print(f"    bid_depth_5 中位 {np.median(bd):>16,.3f}   ask_depth_5 中位 {np.median(ad):>16,.3f}")
        print(f"    中位价 {np.median(0.5*(b+a)):>14,.4f}   spread 中位 {np.median(sp):>12,.4f}")
        # 判断 depth 是"量"还是"名义"：若是名义，depth/price 才是个数
        print(f"    bid_depth_5 / bid_px 中位 = {np.median(bd/np.where(b>0,b,1)):,.3f}"
              f"   （若 depth 是名义，这个比值≈张数）")
        print(f"    bid_orders_count 中位 {np.median(bc):,.0f}   ask_orders_count 中位 {np.median(ac):,.0f}")
        imb5 = (bd - ad) / (bd + ad)
        print(f"    top5 深度失衡：p5 {np.percentile(imb5,5):+.3f}  中位 {np.median(imb5):+.3f}"
              f"  p95 {np.percentile(imb5,95):+.3f}  标准差 {imb5.std():.3f}")

    cn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
