"""H62：实盘账本聚合 —— 每笔净额与左尾占比（用全部能对齐的数据，不只看 18 分钟窗）。

H60b 只看了一个 18 分钟窗（111/150 笔），样本太小。
这里用**engine 内存口径的 fills 计数**（status）与**账本逐笔**对齐，
算全会话的每笔净额；并把左尾占比算出来与 H61 的模拟对照。

⚠️ 口径注意：`fills` 计数与 `paper_pnl` 行数**不一定相等**
（一笔成交可能记 1 行，平仓腿另记）。两者的差本身就是需要暴露的信息。
"""
from __future__ import annotations

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
    import numpy as np
    import psycopg2
    import psycopg2.extras

    cn = psycopg2.connect(_dsn("alpha_arena"))
    cn.autocommit = True
    cur = cn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    sf = ROOT / "logs" / "mm_lane_status.json"
    st = json.loads(sf.read_text(encoding="utf-8")) if sf.exists() else {}
    notional = float(st.get("fill_notional") or 28.5)

    print("=" * 96)
    print("H62  实盘账本聚合")
    print("=" * 96)
    print(f"  status: ticks={st.get('ticks')} fills={st.get('fills')} "
          f"flattens={st.get('flattens')} equity={st.get('equity')}")
    print(f"  fill_notional = ${notional:.4f}")

    # 今天（09-20 起）的 mm_asterdex 流水
    cur.execute(
        "SELECT count(*) n, COALESCE(SUM(amount_usd),0) s,"
        "       min(created_at) mn, max(created_at) mx"
        "  FROM arbitrage_paper_ledgers"
        " WHERE account_id=101 AND action='paper_pnl'"
        "   AND metadata_json::jsonb->>'lane_id'='mm_asterdex'"
        "   AND created_at >= '2026-09-20 00:00:00'")
    r = cur.fetchone()
    n_rows, s_rows = int(r["n"]), float(r["s"])
    print(f"\n  09-20 起 mm_asterdex paper_pnl：{n_rows:,} 行  合计 {s_rows:>+10.4f} USD")
    print(f"    时间跨度 {r['mn']} ~ {r['mx']}")

    # 与 status.fills 对照
    engine_fills = int(st.get("fills") or 0)
    print(f"\n  口径对照：")
    print(f"    账本行数        {n_rows:,}")
    print(f"    status.fills    {engine_fills:,}")
    print(f"    差              {n_rows - engine_fills:+,}")
    if engine_fills and abs(n_rows - engine_fills) / engine_fills > 0.10:
        print("    ⚠️ 差 >10% ⇒ **两个口径不等价**（平仓腿/强平腿的记账方式），")
        print("       每笔净额必须声明用哪个分母 —— 本项目第 12 条教训就是这类口径混淆")
    denom = engine_fills if engine_fills else n_rows
    print(f"\n  ⇒ 用 status.fills 作分母：每笔净额 "
          f"{s_rows / denom / notional * 1e4:>+8.4f} bp")
    print(f"  ⇒ 用账本行数作分母：    每笔净额 "
          f"{s_rows / n_rows / notional * 1e4:>+8.4f} bp")

    # 左尾
    cur.execute(
        "SELECT amount_usd::float a FROM arbitrage_paper_ledgers"
        " WHERE account_id=101 AND action='paper_pnl'"
        "   AND metadata_json::jsonb->>'lane_id'='mm_asterdex'"
        "   AND created_at >= '2026-09-20 00:00:00'")
    bp = np.array([x["a"] for x in cur.fetchall()]) / notional * 1e4
    tot = bp.sum()
    print(f"\n  分布（n={len(bp):,}）：")
    print(f"    均值 {bp.mean():>+9.4f}bp  中位 {np.median(bp):>+9.4f}bp  "
          f"胜率 {(bp>0).mean():.4f}")
    print(f"    p1 {np.percentile(bp,1):>+9.4f}  p5 {np.percentile(bp,5):>+9.4f}  "
          f"p95 {np.percentile(bp,95):>+9.4f}  最小 {bp.min():>+10.4f}  最大 {bp.max():>+9.4f}")
    o = np.argsort(bp)
    print(f"\n    最亏 k% 贡献：")
    for pct in (0.5, 1, 2, 5, 10):
        k = max(1, int(len(bp) * pct / 100))
        sel = bp[o[:k]]
        print(f"      {pct:>4.1f}%  ({k:>5,} 笔)  合计 {sel.sum():>+11.2f}bp"
              f"  占总计 {sel.sum()/tot*100 if tot else float('nan'):>6.1f}%"
              f"   该组均值 {sel.mean():>+9.4f}bp")
    print(f"\n    若把最亏 5% 的笔全部剔除：合计 {bp[o[int(len(bp)*0.05):]].sum():>+11.2f}bp"
          f"  ⇒ 每笔 {bp[o[int(len(bp)*0.05):]].mean():>+8.4f}bp")

    cn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
