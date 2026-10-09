"""诊断：实盘 tick 能不能拿到「两侧队列规模」—— H59 择时闸的前置条件。

## 为什么先查这个

报告里第 1 项（失衡择时闸）的依据是 Albers et al. arXiv:2502.18625v2：
  · §5 Table 1：成交后漂移由**队列位置**决定，队首↔队尾落差 0.72~1.16bp
  · §4：成交概率由**近侧队列规模（p=0.000）与失衡（p=0.001）**决定，4 参数 OLS R²=0.946

H53 已在**我们自己的场地**上测出象限跨度 0.61bp：
    大 near / 小 opp  +0.0510bp
    大 near / 大 opp  −0.1154bp
    小 near / 小 opp  −0.2456bp
    小 near / 大 opp  −0.5621bp

但要把它做成**实盘闸门**，前提是实盘 tick 必须能读到 `bid_qty` / `ask_qty`。
`runner.py` 的实盘路径读的是 `market_orderbook_snapshots.best_bid/best_ask` ——
**那两列只有价格，没有量**。若确实没有量，闸门就无法在实盘实现，
必须先把量接进 tick（这是**设计约束**，不是实现细节）。

本脚本查：
  1. `market_orderbook_snapshots` 有没有量的列
  2. `asterdex_book_ticker` 有没有量（有 —— bid_qty/ask_qty）
  3. 两者是否可对齐（若要新增一列，代价多大）
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

    for t in ("market_orderbook_snapshots", "asterdex_book_ticker"):
        print("=" * 84)
        print(f"【{t}】")
        print("=" * 84)
        cur.execute(
            "SELECT column_name, data_type FROM information_schema.columns"
            " WHERE table_name=%s ORDER BY ordinal_position", (t,))
        cols = cur.fetchall()
        for r in cols:
            print(f"  {r['column_name']:<24} {r['data_type']}")
        names = [r["column_name"] for r in cols]
        has_qty = [c for c in names
                   if any(k in c.lower() for k in ("qty", "size", "amount", "volume"))]
        print(f"\n  ⇒ 含量/规模的列：{has_qty if has_qty else '（无）'}")

    # 实盘 tick 需要的最小列是否齐
    print("\n" + "=" * 84)
    print("实盘择时闸所需字段的可得性")
    print("=" * 84)
    need = ["best_bid", "best_ask", "bid_qty", "ask_qty", "event_ts_ms"]
    cur.execute(
        "SELECT table_name, column_name FROM information_schema.columns"
        " WHERE table_name IN ('market_orderbook_snapshots','asterdex_book_ticker',"
        "'asterdex_depth_snapshots')")
    have = {}
    for r in cur.fetchall():
        have.setdefault(r["table_name"], set()).add(r["column_name"])
    for t, cs in have.items():
        print(f"  {t:<32} {[c for c in need if c in cs]}")

    # 若有量，算一下量与失衡的分布（H59 需要分位切点）
    print("\n" + "=" * 84)
    print("队列规模与失衡的实际分布（用于定闸门阈值）")
    print("=" * 84)
    import numpy as np
    for s in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT"):
        cur.execute(
            "SELECT bid_qty::float bq, ask_qty::float aq,"
            "       bid_px::float b, ask_px::float a"
            "  FROM asterdex_book_ticker WHERE symbol=%s"
            "   AND event_ts_ms > (extract(epoch from now())*1000)::bigint - 7200000"
            "   AND bid_qty>0 AND ask_qty>0 AND ask_px>bid_px",
            (s,))
        d = cur.fetchall()
        if len(d) < 500:
            print(f"  {s}: 数据不足")
            continue
        bq = np.array([r["bq"] for r in d])
        aq = np.array([r["aq"] for r in d])
        # **名义**失衡（用价格加权，避免"币量大但金额小"的伪失衡）
        b = np.array([r["b"] for r in d]); a = np.array([r["a"] for r in d])
        nb, na = bq * b, aq * a
        imb_n = (nb - na) / (nb + na)          # 名义失衡 ∈ [-1,1]
        imb_q = (bq - aq) / (bq + aq)          # 张数失衡
        print(f"\n  {s}  n={len(d):,}")
        print(f"    bid_qty  中位 {np.median(bq):>14,.4f}   ask_qty 中位 {np.median(aq):>14,.4f}")
        print(f"    名义失衡   p5 {np.percentile(imb_n,5):+.3f}  p25 {np.percentile(imb_n,25):+.3f}"
              f"  中位 {np.median(imb_n):+.3f}  p75 {np.percentile(imb_n,75):+.3f}"
              f"  p95 {np.percentile(imb_n,95):+.3f}")
        print(f"    张数失衡   p5 {np.percentile(imb_q,5):+.3f}  p25 {np.percentile(imb_q,25):+.3f}"
              f"  中位 {np.median(imb_q):+.3f}  p75 {np.percentile(imb_q,75):+.3f}"
              f"  p95 {np.percentile(imb_q,95):+.3f}")
        # 两种失衡的相关性（若高度相关可任选其一）
        print(f"    两种失衡的相关系数 {np.corrcoef(imb_n, imb_q)[0,1]:.4f}")

    cn.close()
    print("\n" + "=" * 84)
    print("判读：")
    print("  · 若 market_orderbook_snapshots 无量的列 ⇒ 实盘 tick **拿不到队列规模**，")
    print("    择时闸必须先补数据链路（新增列或改读 asterdex_book_ticker）")
    print("  · asterdex_book_ticker 有 bid_qty/ask_qty 且 36ms 网格 ⇒ 可直接作为数据源")
    print("=" * 84)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
