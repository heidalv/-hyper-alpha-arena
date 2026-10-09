"""H60b：修前 −4.1992bp 是不是被极少数大亏笔拉出来的？（均值 vs 中位 vs 尾部）

H60 用 **111 笔 / 18 分钟** 算出修前 −4.1992bp、修后 −2.2759bp。
但 H58 的漂移分布 p5 到 −8.6bp（300s 更到 −26bp）⇒ **paper_pnl 是重尾的**，
111 个样本的均值极可能由 2~3 笔决定。

本项目已犯过两次"用均值代替分布"的错（第 15 条：mean-vs-median），
所以这里必须把窗口内的**全部流水逐笔打印**，并给出中位/p5/尾部占比。

判据：
  · 若修前均值的绝对值 >> 中位 ⇒ 均值不可用，应改用中位或截尾均值
  · 若两窗的**中位**差与均值差方向一致 ⇒ 结论稳
  · 若方向不一致 ⇒ **判定为样本不足，不下结论**
"""
from __future__ import annotations

import argparse
import datetime
import json
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
    ap = argparse.ArgumentParser()
    ap.add_argument("--cutoff", default="2026-09-20 23:59:18")
    ap.add_argument("--window-min", type=float, default=18.0)
    ap.add_argument("--notional", type=float, default=28.3742)
    a = ap.parse_args()

    import numpy as np
    import psycopg2
    import psycopg2.extras

    cn = psycopg2.connect(_dsn("alpha_arena"))
    cn.autocommit = True
    cur = cn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cdt = datetime.datetime.strptime(a.cutoff, "%Y-%m-%d %H:%M:%S")
    d = datetime.timedelta(minutes=a.window_min)

    def fetch(lo, hi):
        cur.execute(
            "SELECT amount_usd::float amt, created_at,"
            "       metadata_json::jsonb->>'symbol' sym,"
            "       metadata_json::jsonb->>'side' side,"
            "       metadata_json::jsonb->>'edge_bp' edge"
            "  FROM arbitrage_paper_ledgers"
            " WHERE account_id=101 AND action='paper_pnl'"
            "   AND created_at >= %s AND created_at < %s ORDER BY created_at",
            (lo, hi))
        return cur.fetchall()

    for label, lo, hi in (("修前", cdt - d, cdt), ("修后", cdt, cdt + d)):
        rows = fetch(lo, hi)
        if not rows:
            print(f"\n【{label}】无数据")
            continue
        bp = np.array([r["amt"] for r in rows]) / a.notional * 1e4
        print("=" * 92)
        print(f"【{label}】{lo} ~ {hi}   n={len(bp):,}")
        print("=" * 92)
        print(f"  均值 {bp.mean():>+9.4f}bp   中位 {np.median(bp):>+9.4f}bp   "
              f"截尾均值(去最大最小各5%) {np.mean(np.sort(bp)[int(.05*len(bp)):len(bp)-int(.05*len(bp)) or None]):>+9.4f}bp")
        print(f"  p5 {np.percentile(bp,5):>+9.4f}   p25 {np.percentile(bp,25):>+9.4f}   "
              f"p75 {np.percentile(bp,75):>+9.4f}   p95 {np.percentile(bp,95):>+9.4f}")
        print(f"  最小 {bp.min():>+9.4f}   最大 {bp.max():>+9.4f}   胜率 {(bp>0).mean():.3f}")
        # 尾部贡献：最亏的 k 笔占总亏损的比例
        s = bp.sum()
        order = np.argsort(bp)
        print(f"  合计 {s:>+10.4f}bp")
        for k in (1, 2, 3, 5):
            if k <= len(bp):
                tail = bp[order[:k]].sum()
                print(f"    最亏 {k} 笔合计 {tail:>+9.4f}bp"
                      f"  = 总和的 {tail/s*100 if s != 0 else float('nan'):>6.1f}%")
        # 逐笔（按亏损排序取最亏 12 笔）
        print(f"\n  最亏的 12 笔：")
        for i in order[:12]:
            r = rows[int(i)]
            print(f"    {str(r['created_at'])[:23]:<24} {str(r['sym']):<8} {str(r['side']):<5} "
                  f"edge={str(r['edge']):<6} {bp[int(i)]:>+9.4f}bp  ({r['amt']:>+9.5f} USD)")
        print(f"\n  最赚的 5 笔：")
        for i in order[-5:]:
            r = rows[int(i)]
            print(f"    {str(r['created_at'])[:23]:<24} {str(r['sym']):<8} {str(r['side']):<5} "
                  f"edge={str(r['edge']):<6} {bp[int(i)]:>+9.4f}bp  ({r['amt']:>+9.5f} USD)")
        print()

    cn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
