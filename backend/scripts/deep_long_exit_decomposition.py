# -*- coding: utf-8 -*-
"""多头出场损失逐笔分解（第十七轮·深度一）。

第十六轮归因发现：历史多头「入场有边际（14d 持有 +11.24%）、出场砍掉边际
（实际 -0.59%，出场差 -11.82%）」。本脚本把出场损失**逐笔分解**：

  1. 每个 close_reason 的历史多头：实际净 / 72h / 14d / 新出场结构逐笔反事实；
  2. 「新结构反事实 - 实际」= 新出场能救回多少（按 close_reason 分桶）；
  3. 新结构下的出场分布（trail/SL/timeout）与翻转统计（亏损→盈利的笔数）；
  4. peak 回吐：峰值 PnL 到最终的实际回吐 vs 新结构（trail 3%-1.5%）回吐。

输出：`data/long_exit_decomposition.json`
用法：.venv\\Scripts\\python.exe backend/scripts/deep_long_exit_decomposition.py
"""
from __future__ import annotations

import json
import os
import statistics as st
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, text  # noqa: E402

ARENA_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
MARKET_URL = os.getenv("MARKET_DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_market")
OUT = ROOT / "data" / "long_exit_decomposition.json"

FEE_SIDE = 0.0005
SLIP_SIDE = 0.0005
FUND_8H = 0.0001


def cost_pct(h):
    return (FEE_SIDE + SLIP_SIDE) * 2 * 100 + (h / 8.0) * FUND_8H * 100


def load_longs():
    eng = create_engine(ARENA_URL)
    with eng.connect() as c:
        c.execute(text("set app.is_admin='on'"))
        rows = [dict(r._mapping) for r in c.execute(text("""
            select id, symbol, side, entry_price, close_price, sl_price, original_size, size,
                   partial_realized_pnl, timeframe_tier, trade_nature, close_reason,
                   opened_at, closed_at
            from paper_positions
            where timeframe_tier in ('mid','long') and side='long' and status='closed'
            order by opened_at
        """)).fetchall()]
    with eng.connect() as c:
        c.execute(text("set app.is_admin='on'"))
        st_rows = [dict(r._mapping) for r in c.execute(text("""
            select id, symbol, side, entry_price, exit_price as close_price, null as sl_price,
                   position_size as original_size, position_size as size,
                   null as partial_realized_pnl, null as timeframe_tier, null as trade_nature,
                   null as close_reason, opened_at, closed_at, decision_context, strategy_id
            from strategy_trades
            where side='long' and status='closed' and entry_price is not null
            order by opened_at desc limit 3000
        """)).fetchall()]
    have = {(r["symbol"], r["opened_at"]) for r in rows}
    for r in st_rows:
        if str(r["strategy_id"] or "").startswith("e2e_"):
            continue
        dc = r.get("decision_context")
        if isinstance(dc, str):
            try:
                dc = json.loads(dc)
            except Exception:
                try:
                    dc = eval(dc)  # noqa: S307
                except Exception:
                    dc = {}
        nature = (dc or {}).get("nature")
        if nature not in ("swing", "trend_follow", "position"):
            continue
        r["timeframe_tier"] = "mid" if nature == "swing" else "long"
        if (r["symbol"], r["opened_at"]) in have:
            continue
        have.add((r["symbol"], r["opened_at"]))
        rows.append(r)
    return rows


def load_klines(symbols):
    h1 = defaultdict(list)
    eng = create_engine(MARKET_URL)
    with eng.connect() as c:
        c.execute(text("set statement_timeout='900000'"))
        for exch in ("asterdex", "binance"):
            for s, ts, o, h, l, cl in c.execute(text("""
                select symbol, timestamp, open_price, high_price, low_price, close_price
                from crypto_klines where period='1h' and exchange=:ex and symbol = any(:syms)
                order by symbol, timestamp
            """), {"ex": exch, "syms": list(symbols)}).fetchall():
                h1[(exch, s)].append((int(ts), float(o), float(h), float(l), float(cl)))
    return h1


def pick(series, sym):
    for ex in ("asterdex", "binance"):
        v = series.get((ex, sym))
        if v and len(v) > 300:
            return v
    return None


def sim_new_exit(s, i, entry, sl_pct=6.0, trig=3.0, gap=1.5, max_h=168):
    """新出场结构（生产同款）：SL6 / 追踪3-1.5 / 168h。返回 (net, hold_h, reason, peak)。"""
    peak = 0.0
    for k in range(i, min(i + int(max_h) + 1, len(s))):
        _ts, _o, h, l, c = s[k]
        hold = k - i
        mfe = (h - entry) / entry * 100
        peak = max(peak, mfe)
        if l <= entry * (1 - sl_pct / 100):
            return -sl_pct - cost_pct(hold), hold, "sl", peak
        if peak >= trig and mfe <= peak - gap:
            return max(peak - gap, 0) - cost_pct(hold), hold, "trail", peak
        if k == min(i + int(max_h), len(s) - 1):
            return (c - entry) / entry * 100 - cost_pct(hold), hold, "timeout", peak
    return None, 0, "no_data", peak


def main() -> int:
    rows = load_longs()
    h1 = load_klines({r["symbol"] for r in rows})
    recs = []
    for r in rows:
        s = pick(h1, r["symbol"])
        if not s:
            continue
        ts = int(r["opened_at"].timestamp())
        i = next((k for k, row in enumerate(s) if row[0] >= ts), None)
        if i is None or i < 24:
            continue
        entry = float(r["entry_price"] or 0)
        close = float(r["close_price"] or 0)
        if entry <= 0 or close <= 0:
            continue
        hold_h = (r["closed_at"] - r["opened_at"]).total_seconds() / 3600 if r["closed_at"] else 0
        actual = (close - entry) / entry * 100 - cost_pct(hold_h)
        k72 = i + 72
        net72 = (s[min(k72, len(s) - 1)][4] - entry) / entry * 100 - cost_pct(72)
        j = i + 336
        if j >= len(s):
            continue
        net14 = (s[j][4] - entry) / entry * 100 - cost_pct(336)
        new_net, new_hold, new_reason, new_peak = sim_new_exit(s, i, entry)
        if new_net is None:
            continue
        reason = str(r.get("close_reason") or "?")
        # 归类 close_reason（前 32 字符内的关键词族）
        if reason.startswith("long_trend_v2"):
            family = "long_trend_v2"
        elif "no_progress" in reason:
            family = "no_progress"
        elif reason.startswith("trend_broken"):
            family = "trend_broken"
        elif reason.startswith("profit_drawdown"):
            family = "profit_drawdown"
        elif reason.startswith("master_running"):
            family = "master_running"
        elif reason.startswith("breakeven"):
            family = "breakeven_tp"
        elif reason.startswith("sl") or reason.startswith("emergency"):
            family = reason[:24]
        elif reason == "?":
            family = "strategy_trades"
        else:
            family = reason[:24]
        recs.append({
            "id": r["id"], "symbol": r["symbol"], "tier": str(r.get("timeframe_tier") or "?"),
            "family": family, "close_reason": reason[:60],
            "opened": str(r["opened_at"])[:19], "hold_h": round(hold_h, 2),
            "actual": round(actual, 3), "net72": round(net72, 3), "net14": round(net14, 3),
            "new_net": round(new_net, 3), "new_hold": new_hold, "new_reason": new_reason,
            "new_peak": round(new_peak, 3),
            "recovery": round(new_net - actual, 3),
        })

    print(f"可解析样本: {len(recs)}")

    def agg(rows_, label):
        if not rows_:
            return
        n = len(rows_)
        a = sum(x["actual"] for x in rows_) / n
        n72 = sum(x["net72"] for x in rows_) / n
        n14 = sum(x["net14"] for x in rows_) / n
        nn = sum(x["new_net"] for x in rows_) / n
        rec = sum(x["recovery"] for x in rows_) / n
        flip = sum(1 for x in rows_ if x["actual"] <= 0 < x["new_net"])
        print(f"  {label:<26} n={n:>3} 实际={a:>+8.2f}% 新出场={nn:>+8.2f}% "
              f"72h={n72:>+8.2f}% 14d={n14:>+8.2f}% 救回={rec:>+8.2f}% 翻转={flip:>3}")

    print("\n=== 总体 ===")
    agg(recs, "全部")
    print("\n=== 按 close_reason 族（n≥3）===")
    by_fam = defaultdict(list)
    for x in recs:
        by_fam[x["family"]].append(x)
    for fam in sorted(by_fam, key=lambda k: -len(by_fam[k])):
        agg(by_fam[fam], fam)
    print("\n=== 按 tier ===")
    for tier in ("mid", "long"):
        agg([x for x in recs if x["tier"] == tier], f"tier={tier}")

    print("\n=== 新出场结构的出场分布 ===")
    for reason in ("trail", "sl", "timeout"):
        v = [x for x in recs if x["new_reason"] == reason]
        if v:
            agg(v, f"新出场={reason}")

    print("\n=== 新出场 vs 实际：笔数汇总 ===")
    n_all = len(recs)
    print(f"  实际盈利: {sum(1 for x in recs if x['actual'] > 0)}/{n_all} "
          f"→ 新出场盈利: {sum(1 for x in recs if x['new_net'] > 0)}/{n_all}")
    print(f"  实际总净: {sum(x['actual'] for x in recs):+.1f}% → "
          f"新出场总净: {sum(x['new_net'] for x in recs):+.1f}% → "
          f"14d 总净: {sum(x['net14'] for x in recs):+.1f}%")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n": n_all, "recs": recs,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
