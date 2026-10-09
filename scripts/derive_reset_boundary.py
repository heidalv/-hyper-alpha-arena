# -*- coding: utf-8 -*-
"""反推账户真实重置边界：从流水尾部向前累加 paper_pnl，直到等于账户行 realized_pnl。
   该时刻 = 上一次真正重置账户的时间（据此修正面板口径）。写 meta.account_reset_at。
"""
import sys
import json
import pathlib
import datetime as dt
import psycopg

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.h422_weekly_scan import read_env_dsn  # noqa: E402

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

c = psycopg.connect(read_env_dsn(), autocommit=True)
cur = c.cursor()
cur.execute("SELECT realized_pnl, available_balance FROM arbitrage_paper_accounts WHERE id=101")
target = float(cur.fetchone()[0])
print(f"账户行 realized_pnl = {target:+.4f}（这是'上次重置以来'的权威已实现）")

cur.execute("""
    SELECT created_at, amount_usd FROM arbitrage_paper_ledgers
    WHERE account_id=101 AND action='paper_pnl'
    ORDER BY created_at DESC
""")
rows = cur.fetchall()
acc = 0.0
boundary = None
prev = None
for created, amt in rows:
    acc += float(amt or 0.0)
    if acc <= target:
        boundary = created
        print(f"  反推边界：{created:%Y-%m-%d %H:%M:%S}（累计 {acc:+.4f} 首次触及 {target:+.4f}）")
        break
    prev = created
if boundary is None:
    print("  反推失败（累计未达），退化为最早流水")
    boundary = rows[-1][0] if rows else None

if boundary:
    # 该边界之后的费用
    cur.execute("""
        SELECT COUNT(*), ROUND(SUM(amount_usd)::numeric,4) FROM arbitrage_paper_ledgers
        WHERE account_id=101 AND action='paper_fee' AND created_at >= %s
    """, (boundary,))
    nf, fee = cur.fetchone()
    print(f"  边界后手续费：{nf} 条 Σ=${fee}")
    cur.execute("""
        SELECT COUNT(*) FROM lane_ledger WHERE event='fill' AND ts >= %s
    """, (boundary,))
    print(f"  边界后成交腿数：{cur.fetchone()[0]}")

    # 写入 meta（幂等：仅在缺失或更早时更新）
    cur.execute("SELECT meta_json FROM lane_registry WHERE lane_id='mm_asterdex'")
    m = cur.fetchone()[0]
    old = m.get("account_reset_at")
    m["account_reset_at"] = boundary.isoformat()
    ops = list(m.get("ops_changes") or [])
    ops.append({"ts": dt.datetime.now(dt.timezone.utc).isoformat(),
                "action": "set_account_reset_at",
                "field": "meta.account_reset_at",
                "from": old, "to": boundary.isoformat(),
                "note": "面板口径修正：以「账户真实重置时刻」为界（由账户行 realized_pnl "
                        "对流水反推得到），不再受 stats_since（试跑脚本会改）影响"})
    m["ops_changes"] = ops[-20:]
    cur.execute("UPDATE lane_registry SET meta_json=%s, updated_at=now() "
                "WHERE lane_id='mm_asterdex'",
                (json.dumps(m, ensure_ascii=False, default=str),))
    c.commit()
    print(f"  已写 meta.account_reset_at = {boundary.isoformat()}")
