"""诊断：实盘车道 −$14.50 的精确分解 —— 是手续费、已实现，还是浮亏？

`mm_lane_status.json` 只给一个 `equity`（285.50），无法区分：
  · 已实现（点差捕获 + 价格损益 + 手续费）
  · 未实现（持仓浮亏）
  · 或统计口径本身有问题（本项目已有 19 次口径错误，其中 F252 就是净额虚报 71 倍）

⇒ 直接从账本与状态表把它拆开，再谈"根因"。

数据源：
  · `arbitrage_paper_accounts` id=101（MM 做市专用 $300）
  · `arbitrage_paper_ledgers`（列：action / amount_usd / metadata_json / created_at）
  · `lane_ledger`（meta_json 带 qty/px/mid/flatten）
  · `logs/mm_lane_status.json`（唯一可靠的跨进程运行态）
"""
from __future__ import annotations

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
    import psycopg2
    import psycopg2.extras

    print("=" * 96)
    print("实盘车道盈亏分解")
    print("=" * 96)

    # ── 1) 模拟账户 ──
    cn = psycopg2.connect(_dsn("alpha_arena"))
    cn.autocommit = True
    cur = cn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM arbitrage_paper_accounts WHERE id=101")
    acc = cur.fetchone()
    if acc:
        print("\n  arbitrage_paper_accounts id=101:")
        for k, v in acc.items():
            print(f"    {k:<24} {v}")

    # ── 2) 账本流水汇总 ──
    cur.execute(
        "SELECT action, count(*) n, COALESCE(SUM(amount_usd),0) s"
        "  FROM arbitrage_paper_ledgers WHERE account_id=101"
        " GROUP BY action ORDER BY s"
    )
    rows = cur.fetchall()
    print("\n  arbitrage_paper_ledgers（按 action 汇总）:")
    tot = 0.0
    for r in rows:
        tot += float(r["s"])
        print(f"    {str(r['action']):<28} {r['n']:>8,} 笔   合计 {float(r['s']):>+12.4f} USD")
    print(f"    {'—— 合计':<28} {'':>8}       {tot:>+12.4f} USD")

    # ── 3) lane_ledger（成交腿 / 平仓腿分开）──
    cur.execute(
        "SELECT count(*) n,"
        "       COALESCE(SUM((meta_json->>'qty')::float"
        "         * (meta_json->>'px')::float),0) notional,"
        "       COALESCE(SUM(CASE WHEN (meta_json->>'flatten')::boolean"
        "         THEN 1 ELSE 0 END),0) flattens"
        "  FROM lane_ledger WHERE lane_id='mm_asterdex'"
    )
    r = cur.fetchone()
    if r:
        print(f"\n  lane_ledger（lane_id='mm_asterdex'）:")
        print(f"    行数 {int(r['n']):,}   名义合计 {float(r['notional']):,.2f} USD"
              f"   平仓腿 {int(r['flattens'] or 0):,}")

    cur.execute(
        "SELECT symbol, count(*) n FROM lane_ledger"
        " WHERE lane_id='mm_asterdex' GROUP BY symbol ORDER BY n DESC"
    )
    print("\n    按币分布:")
    for r in cur.fetchall():
        print(f"      {str(r['symbol']):<16} {int(r['n']):>8,}")

    # ── 4) status（运行态）──
    sf = ROOT / "logs" / "mm_lane_status.json"
    if sf.exists():
        j = json.loads(sf.read_text(encoding="utf-8"))
        print(f"\n  logs/mm_lane_status.json:")
        for k in ("ts", "ok", "reason", "ticks", "fills", "flattens", "equity",
                  "fill_notional", "quoted_decisions", "fills_per_hour",
                  "avg_width_bp", "avg_base_bp", "frozen_share", "quote_modes"):
            if k in j:
                print(f"    {k:<20} {j[k]}")
        if j.get("skip_counts"):
            print(f"    {'skip_counts':<20} {j['skip_counts']}")
        if j.get("side_counts"):
            print(f"    {'side_counts':<20} {j['side_counts']}")
        if j.get("states"):
            print(f"\n    逐币状态（前 12）:")
            st = j["states"]
            items = list(st.items())[:12] if isinstance(st, dict) else []
            for s, v in items:
                if isinstance(v, dict):
                    print(f"      {s:<16} qty={v.get('qty')} avg_px={v.get('avg_px')} "
                          f"opened={v.get('opened_ts')}")

    cn.close()
    print("\n" + "=" * 96)
    print("判读：")
    print("  · 若「已实现」≈ −$14.5 ⇒ 是**成交本身**在亏（点差/逆选择），修挂宽有用")
    print("  · 若「未实现」占大头 ⇒ 是**持仓浮亏**没被平掉（超时/平仓腿失效），修持有窗口")
    print("  · 若 ledger 合计与 equity 差额对不上 ⇒ **又一处口径错误**，先修统计再谈策略")
    print("=" * 96)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
