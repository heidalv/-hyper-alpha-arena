# -*- coding: utf-8 -*-
"""learned 门对「先盈利后大亏」模式的拦截效果（X7，决定性验证）。

近 30 天 184 笔中，44 笔（24%）「峰值≥0.5% 后亏损离场」，机械出场反事实
也是负的（-1.36%）——说明是**入场质量问题**。本脚本把 round-17 修正后的
learned 门套到这 184 笔上：
  1. 门会拦掉多少笔、拦掉的那些的实际净与峰值；
  2. 44 笔「浮盈→亏损」模式交易中，门拦住几笔；
  3. 门放行集 vs 拦截集的实际净/峰值/胜率对比；
  4. 逐条列出被拦的「浮盈→亏损」交易（符号/时间/regime/chg/模板）。
"""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend" / "scripts"))

from deep_long_freshness import (  # noqa: E402
    load_klines, pick, build_bar_features, learned_ok, learned_ok_prod, cost_pct,
)

from sqlalchemy import create_engine, text  # noqa: E402

ARENA_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
OUT = ROOT / "data" / "gate_vs_giveback.json"


def load_recent(days=30):
    eng = create_engine(ARENA_URL)
    with eng.connect() as c:
        c.execute(text("set app.is_admin='on'"))
        return [dict(r._mapping) for r in c.execute(text(f"""
            select id, symbol, side, timeframe_tier, trade_nature, strategy_id,
                   entry_price, close_price, peak_pnl_pct, close_reason, opened_at, closed_at
            from paper_positions
            where timeframe_tier in ('mid','long') and status='closed'
              and closed_at >= now() - interval '{int(days)} days'
            order by opened_at
        """)).fetchall()]


def main() -> int:
    rows = load_recent(30)
    h1, d1 = load_klines({r["symbol"] for r in rows})
    feats = {}
    for sym in {r["symbol"] for r in rows}:
        s = pick(h1, sym)
        ds = pick(d1, sym)
        if s is None or ds is None or len(s) < 300 or len(ds) < 70:
            continue
        feats[sym] = (s, build_bar_features(s, ds))

    recs = []
    for r in rows:
        sym = r["symbol"]
        if sym not in feats:
            continue
        s, (reg_arr, pos_arr, chg_arr) = feats[sym]
        entry = float(r["entry_price"] or 0)
        close = float(r["close_price"] or 0)
        if entry <= 0 or close <= 0:
            continue
        ts = int(r["opened_at"].timestamp())
        i = next((k for k, row in enumerate(s) if row[0] >= ts), None)
        if i is None or i < 25:
            continue
        side = str(r["side"] or "long")
        hold_h = (r["closed_at"] - r["opened_at"]).total_seconds() / 3600 if r["closed_at"] else 0
        raw = (close - entry) / entry * 100 if side == "long" else (entry - close) / entry * 100
        reg = reg_arr[i]
        chg = chg_arr[i]
        pos = pos_arr[i]
        allow = learned_ok_prod(reg, pos, chg)
        allow16 = learned_ok(reg, pos, chg)
        peak = float(r["peak_pnl_pct"] or 0) * 100
        recs.append({
            "symbol": sym, "side": side, "tier": r["timeframe_tier"],
            "nature": str(r.get("trade_nature") or "?"),
            "sid": str(r.get("strategy_id") or "?")[:18],
            "opened": str(r["opened_at"])[:16],
            "regime": reg, "chg": round(chg, 2), "pos": round(pos, 1),
            "allow": allow, "allow16": allow16, "peak": round(peak, 3),
            "actual": round(raw - cost_pct(hold_h), 3),
            "reason": str(r["close_reason"] or "?")[:26],
        })

    print(f"近 30 天 mid/long 可评估: {len(recs)} 笔（多头+空头）")
    longs = [x for x in recs if x["side"] == "long"]
    print(f"其中多头: {len(longs)} 笔")

    def agg(rows_, label):
        if not rows_:
            return
        n = len(rows_)
        print(f"  {label:<28} n={n:>3} 实际净均值={sum(x['actual'] for x in rows_)/n:>+7.2f}% "
              f"峰值均值={sum(x['peak'] for x in rows_)/n:>+6.2f}% "
              f"胜率={sum(1 for x in rows_ if x['actual']>0)/n:.3f} "
              f"合计={sum(x['actual'] for x in rows_):>+8.1f}%")

    print("\n=== 全部（含空头）===")
    agg(recs, "全部")
    agg([x for x in recs if x["allow"]], "门放行")
    agg([x for x in recs if not x["allow"]], "门拦截")

    print("\n=== 只多头 ===")
    agg(longs, "全部多头")
    agg([x for x in longs if x["allow"]], "门放行")
    agg([x for x in longs if not x["allow"]], "门拦截")

    print("\n=== 「浮盈→亏损」模式（峰值≥0.5% 且实际亏损）===")
    pat = [x for x in recs if x["peak"] >= 0.5 and x["actual"] < 0]
    pat_l = [x for x in longs if x["peak"] >= 0.5 and x["actual"] < 0]
    agg(pat, "模式交易(全部)")
    agg([x for x in pat if x["allow"]], "  └ 门放行(round17)")
    agg([x for x in pat if not x["allow"]], "  └ 门拦截(round17)")
    agg([x for x in pat if x["allow16"]], "  └ 门放行(round16对照)")
    agg([x for x in pat if not x["allow16"]], "  └ 门拦截(round16对照)")
    agg(pat_l, "模式交易(多头)")
    agg([x for x in pat_l if x["allow"]], "  └ 门放行(round17)")
    agg([x for x in pat_l if not x["allow"]], "  └ 门拦截(round17)")

    print("\n=== 门放行的「浮盈→亏损」交易明细（多头，round17 口径的漏网）===")
    for x in [y for y in pat_l if y["allow"]][:25]:
        print(f"  {x['opened']} {x['symbol']:<8} {x['regime']:<5} chg={x['chg']:>+6.2f}% "
              f"pos={x['pos']:>5.1f}% 峰值={x['peak']:>+5.2f}% 实际={x['actual']:>+6.2f}% "
              f"sid={x['sid']:<18} {x['reason']}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n": len(recs), "recs": recs,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
