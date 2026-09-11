# -*- coding: utf-8 -*-
"""Z24：mid/long 实际流量来源拆解（§24 #25）。

问题：周报显示 14 天内 mid/long 开仓 70 笔（swing 47 / trend_follow 18 / position 5），
但历史回算「门放行」只有 0.39 笔/天 —— 两者矛盾。
本脚本查清：这些开仓走的是哪条链路（`entry_source`）、是否经过 learned 门。
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
            select id, symbol, timeframe_tier, trade_nature, strategy_id, entry_price,
                   original_size, size, status, exit_state_json, opened_at, closed_at,
                   unrealized_pnl, partial_realized_pnl, partial_fee_paid, peak_pnl_pct
            from paper_positions
            where trade_nature in ('swing','trend_follow','position')
              and opened_at >= now() - interval '14 days'
            order by opened_at
        """)).fetchall()]
    print(f"近 14 天 mid/long 开仓 = {len(rows)} 笔")

    def src(r):
        es = r["exit_state_json"] or {}
        if isinstance(es, str):
            try:
                es = json.loads(es)
            except Exception:
                es = {}
        om = (es or {}).get("open_metadata") or {}
        return str(om.get("entry_source") or (es or {}).get("entry_source") or "(none)")

    def usd(r):
        return (float(r["unrealized_pnl"] or 0) + float(r["partial_realized_pnl"] or 0)
                - float(r["partial_fee_paid"] or 0))

    print("\n=== 按 (tier, nature, entry_source) ===")
    g = defaultdict(list)
    for r in rows:
        g[(str(r["timeframe_tier"]), str(r["trade_nature"]), src(r))].append(r)
    print(f"  {'tier':<6}{'nature':<14}{'entry_source':<18}{'n':>5}{'已平':>6}{'总USD':>10}")
    for k, sub in sorted(g.items(), key=lambda x: -len(x[1])):
        closed = [x for x in sub if x["status"] == "closed"]
        print(f"  {k[0]:<6}{k[1]:<14}{k[2]:<18}{len(sub):>5}{len(closed):>6}"
              f"{sum(usd(x) for x in closed):>+10.2f}")

    print("\n=== 按 strategy_id 家族 ===")
    fam = Counter()
    for r in rows:
        s = str(r["strategy_id"] or "")
        for pref in ("tpl_mid_reversion", "tpl_mid_range", "tpl_long_swing", "tpl_long_mean_reversion",
                     "tpl_pro", "gen_", "auto_", "trend_e1", "scalp"):
            if s.lower().startswith(pref):
                fam[pref] += 1
                break
        else:
            fam[s[:20] or "(empty)"] += 1
    for k, v in fam.most_common(12):
        print(f"  {k:<28}{v}")

    print("\n=== 逐日开仓数（tier × nature）===")
    byday = defaultdict(lambda: defaultdict(int))
    for r in rows:
        byday[str(r["opened_at"])[:10]][f"{r['timeframe_tier']}/{r['trade_nature']}"] += 1
    for d in sorted(byday)[-14:]:
        items = " ".join(f"{k}={v}" for k, v in sorted(byday[d].items()))
        print(f"  {d}  {sum(byday[d].values()):>3}  {items}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
