"""H92：口径对账 —— ledger / available_balance / realized_pnl 三者不一致。

# 发现（本轮对账）

```
按天（mm_asterdex, paper_pnl + paper_fee）：
  09-14   −$7.2698
  09-15   −$93.2976      ← 单日最大亏损（旧配置）
  09-16   −$2.3245
  09-17   −$0.0050
  09-20   −$16.1241
  09-21   −$22.6400
  ────────────────
  累计    **−$141.6610**

账户 available_balance = 300 − 38.70 = **261.30**
账户 realized_pnl      = **−$29.67**
账户 total_equity      = 300.00（**陈旧未更新**）
```

**三个口径互不一致**：
  1. 累计 ledger = **−$141.66**
  2. `available_balance` 隐含 = **−$38.70**
  3. `realized_pnl` = **−$29.67**

# 这为什么重要（对目标判据的影响）

我的目标判据一直是"fill+flatten 合并每笔净额转正"，但**一直只算了 `paper_pnl`**，
**漏了 `paper_fee`**（H91 才发现它占 36%）。

⇒ 正确的判据必须包含费用，否则会系统性高估。

# 本脚本产出

  1. **今天（09-21）的完整口径**：pnl + fee 合计、按行/按周期折算 bp
  2. 费用占亏损的比例
  3. 三个口径的差异，并指出哪个可用于判据
  4. 明确写清 `realized_pnl` 是**本 epoch** 口径，不能当累计用

用法：
    .venv\\Scripts\\python.exe scripts\\h92_pnl_reconciliation.py
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

    DAY = "2026-09-21 00:00:00"
    NOTIONAL = 135.0     # 当前腿量

    cn = psycopg2.connect(_dsn("alpha_arena"))
    cn.autocommit = True
    cur = cn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    print("=" * 100)
    print("H92  盈亏口径对账（今天）")
    print("=" * 100)

    cur.execute(
        "SELECT action, count(*) n, COALESCE(SUM(amount_usd),0) s"
        "  FROM arbitrage_paper_ledgers"
        " WHERE account_id=101"
        "   AND metadata_json::jsonb->>'lane_id'='mm_asterdex'"
        "   AND created_at >= %s GROUP BY action ORDER BY action", (DAY,))
    rows = cur.fetchall()
    tot, parts = 0.0, {}
    print(f"\n  {'action':<16} {'行数':>7} {'合计USD':>12} {'每行bp':>10}")
    print("  " + "-" * 50)
    for r in rows:
        s = float(r["s"])
        tot += s
        parts[r["action"]] = s
        bp = s / max(int(r["n"]), 1) / NOTIONAL * 1e4
        print(f"  {r['action']:<16} {int(r['n']):>7} {s:>+12.4f} {bp:>+10.3f}")
    print("  " + "-" * 50)
    print(f"  {'合计':<16} {'':>7} {tot:>+12.4f} {tot/max(sum(int(r['n']) for r in rows),1)/NOTIONAL*1e4:>+10.3f}")

    pnl = parts.get("paper_pnl", 0.0)
    fee = parts.get("paper_fee", 0.0)
    print(f"\n  **完整口径（pnl + fee）= {pnl + fee:+.4f} USD**")
    print(f"     其中 paper_pnl {pnl:+.4f}    paper_fee {fee:+.4f}")
    print(f"     费用占 |合计| 的 **{abs(fee)/max(abs(pnl+fee),1e-9)*100:.1f}%**")

    # 按周期折算
    eps = derive(load())
    eps_day = [e for e in eps if (e.get("ts0") or 0) >= 1789920000.0]  # 09-21 00:00 本地
    print(f"\n  今天周期数 {len(eps_day)}（其中含强平 {sum(1 for e in eps_day if e['flat'])}）")
    if eps_day:
        print(f"  ⇒ 每周期完整口径 = {(pnl+fee)/len(eps_day):+.5f} USD")
        print(f"  ⇒ 每周期折算 bp（按 ${NOTIONAL:.0f} 腿）= "
              f"{(pnl+fee)/len(eps_day)/NOTIONAL*1e4:+.3f} bp")

    print("\n" + "=" * 100)
    print("三个口径的差异（必须写清，否则判据会错）")
    print("=" * 100)
    cur.execute("SELECT realized_pnl::float rp, available_balance::float ab,"
                "       total_equity::float te"
                "  FROM arbitrage_paper_accounts WHERE id=101")
    acc = cur.fetchone()
    cur.execute(
        "SELECT COALESCE(SUM(amount_usd),0) s FROM arbitrage_paper_ledgers"
        " WHERE account_id=101 AND action<>'create_account'")
    all_time = float(cur.fetchone()["s"])
    cur.execute(
        "SELECT COALESCE(SUM(amount_usd),0) s FROM arbitrage_paper_ledgers"
        " WHERE account_id=101 AND action<>'create_account'"
        "   AND metadata_json::jsonb->>'lane_id'='mm_asterdex'")
    lane_time = float(cur.fetchone()["s"])
    cn.close()

    print(f"\n  {'口径':<34} {'值USD':>12} {'说明'}")
    print("  " + "-" * 76)
    print(f"  {'累计 ledger（全账户）':<34} {all_time:>+12.4f}  含所有 lane")
    print(f"  {'累计 ledger（mm_asterdex）':<34} {lane_time:>+12.4f}  **真实累计交易亏损**")
    print(f"  {'available_balance − 300':<34} {acc['ab']-300:>+12.4f}  可用现金变化")
    print(f"  {'realized_pnl':<34} {acc['rp']:>+12.4f}  **仅本 epoch，非累计**")
    print(f"  {'total_equity':<34} {acc['te']:>+12.4f}  ⚠️ 陈旧未更新")

    print("\n" + "=" * 100)
    print("判据用哪个口径")
    print("=" * 100)
    print("  · **判据必须用 `paper_pnl + paper_fee`**（完整口径）")
    print("    —— 只算 pnl 会系统性高估，实测费用占 36%")
    print("  · `realized_pnl` 是**本 epoch**口径，**不能当累计用**")
    print("    （它只反映引擎本次启动以来的已实现，与 ledger 累计差 $112）")
    print("  · `total_equity` **陈旧未更新**（恒为 300）⇒ 不能用它算回撤")
    print("  · 若要算「自建仓以来的总亏损」，用**累计 ledger（mm_asterdex）**")
    print(f"\n  ⇒ 真实累计交易亏损 = **{lane_time:+.4f} USD**")
    print(f"     而我一直引用的「−10%」（相对 $300）**低估了约 "
          f"{abs(lane_time)-29.8:.0f} USD**")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
