# -*- coding: utf-8 -*-
"""Z25：追查「无 entry_source」的 mid/swing 开仓来自哪条链路（§24 #25 关键）。

Z24 发现近 14 天 mid/swing 40 笔的 entry_source 为空（只有 7 笔带 mlto），
且 tpl_mid_range(20)/tpl_mid_reversion(8) 等毒性族仍在成交。
若这些开仓绕过了 brain.can_open_block_reason（learned 门的唯一消费点），
则「门」的实际覆盖面远小于此前假设。
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sqlalchemy import create_engine, text  # noqa: E402

URL = os.getenv("DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")


def main() -> int:
    eng = create_engine(URL)
    with eng.connect() as c:
        c.execute(text("set app.is_admin='on'"))
        rows = [dict(r._mapping) for r in c.execute(text("""
            select id, symbol, timeframe_tier, trade_nature, strategy_id, account_id,
                   entry_price, original_size, size, status, exit_state_json, opened_at,
                   closed_at, unrealized_pnl, partial_realized_pnl, partial_fee_paid, peak_pnl_pct
            from paper_positions
            where trade_nature = 'swing' and opened_at >= now() - interval '14 days'
            order by opened_at
        """)).fetchall()]

    def es(r):
        v = r["exit_state_json"] or {}
        if isinstance(v, str):
            try:
                v = json.loads(v)
            except Exception:
                v = {}
        return v if isinstance(v, dict) else {}

    def src(r):
        return str((es(r).get("open_metadata") or {}).get("entry_source")
                   or es(r).get("entry_source") or "")

    print(f"近 14 天 swing 开仓 = {len(rows)} 笔；account 分布="
          f"{dict(Counter(r['account_id'] for r in rows))}")
    print("\n=== 逐笔：source / strategy_id / exit_state 键 ===")
    print(f"{'id':>6}{'sym':<10}{'tier':<6}{'source':<10}{'strategy_id':<30}"
          f"{'exit_state键':<40}opened")
    for r in rows[:60]:
        keys = ",".join(list(es(r).keys())[:6])
        print(f"{r['id']:>6}{r['symbol']:<10}{str(r['timeframe_tier']):<6}{src(r) or '-':<10}"
              f"{str(r['strategy_id'] or '')[:29]:<30}{keys[:39]:<40}"
              f"{str(r['opened_at'])[:16]}")

    print("\n=== strategy_trades 里同期同币种的 decision_source ===")
    with eng.connect() as c:
        c.execute(text("set app.is_admin='on'"))
        st = [dict(r._mapping) for r in c.execute(text("""
            select symbol, side, strategy_id, decision_source, opened_at, closed_at, pnl,
                   decision_context
            from strategy_trades
            where opened_at >= now() - interval '14 days'
            order by opened_at
        """)).fetchall()]
    print(f"  共 {len(st)} 笔；decision_source 分布="
          f"{dict(Counter(str(x['decision_source'] or '(null)') for x in st))}")
    nat = Counter()
    for x in st:
        dc = x["decision_context"]
        if isinstance(dc, str):
            try:
                dc = json.loads(dc)
            except Exception:
                dc = {}
        nat[str((dc or {}).get("nature") or "(null)")] += 1
    print(f"  nature 分布={dict(nat)}")
    print("\n  样例（前 8 笔 swing/trend_follow）：")
    for x in st:
        dc = x["decision_context"]
        if isinstance(dc, str):
            try:
                dc = json.loads(dc)
            except Exception:
                dc = {}
        if str((dc or {}).get("nature")) not in ("swing", "trend_follow", "position"):
            continue
        print(f"    {str(x['opened_at'])[:16]} {x['symbol']:<9} "
              f"src={str(x['decision_source'] or '-'):<12} "
              f"sid={str(x['strategy_id'] or '')[:26]:<27} pnl={x['pnl']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
