# -*- coding: utf-8 -*-
"""多头信号来源分解（第十七轮·深度三）。

把历史多头按「来源」切分，回答哪些来源有边际：
  1. nature（swing / trend_follow / position）；
  2. tier（mid / long）；
  3. 来源表（paper_positions vs strategy_trades 补充样本）；
  4. strategy_id 家族（brain/hub 主路径 vs 其它）；
  5. 时段（旧出场时代 2026-09-09 前 vs 新出场时代之后，样本少仅参考）。
报：n / 实际净 / 72h 前向 / 新出场反事实 / learned(现行门) 通过率。
"""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, text  # noqa: E402

ARENA_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
MARKET_URL = os.getenv("MARKET_DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_market")
OUT = ROOT / "data" / "long_sources.json"

FEE_SIDE = 0.0005
SLIP_SIDE = 0.0005
FUND_8H = 0.0001


def cost_pct(h):
    return (FEE_SIDE + SLIP_SIDE) * 2 * 100 + (h / 8.0) * FUND_8H * 100


def load_longs():
    eng = create_engine(ARENA_URL)
    with eng.connect() as c:
        c.execute(text("set app.is_admin='on'"))
        pp = [dict(r._mapping) for r in c.execute(text("""
            select id, symbol, side, entry_price, close_price, sl_price, original_size, size,
                   partial_realized_pnl, timeframe_tier, trade_nature, close_reason,
                   strategy_id, opened_at, closed_at
            from paper_positions
            where timeframe_tier in ('mid','long') and side='long' and status='closed'
            order by opened_at
        """)).fetchall()]
        for r in pp:
            r["_src"] = "paper_positions"
        st_rows = [dict(r._mapping) for r in c.execute(text("""
            select id, symbol, side, entry_price, exit_price as close_price, null as sl_price,
                   position_size as original_size, position_size as size,
                   null as partial_realized_pnl, null as timeframe_tier, null as trade_nature,
                   null as close_reason, opened_at, closed_at, decision_context, strategy_id,
                   decision_source, thesis_id
            from strategy_trades
            where side='long' and status='closed' and entry_price is not null
            order by opened_at desc limit 3000
        """)).fetchall()]
    have = {(r["symbol"], r["opened_at"]) for r in pp}
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
        r["trade_nature"] = nature
        r["_src"] = "strategy_trades"
        if (r["symbol"], r["opened_at"]) in have:
            continue
        have.add((r["symbol"], r["opened_at"]))
        pp.append(r)
    return pp


def load_klines(symbols):
    h1 = defaultdict(list)
    d1 = defaultdict(list)
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
            for s, ts, o, h, l, cl in c.execute(text("""
                select symbol, timestamp, open_price, high_price, low_price, close_price
                from crypto_klines where period='1d' and exchange=:ex and symbol = any(:syms)
                order by symbol, timestamp
            """), {"ex": exch, "syms": list(symbols)}).fetchall():
                d1[(exch, s)].append((int(ts), float(o), float(h), float(l), float(cl)))
    return h1, d1


def pick(series, sym):
    for ex in ("asterdex", "binance"):
        v = series.get((ex, sym))
        if v and len(v) > 300:
            return v
    return None


def regime_at(dseries, ts):
    if not dseries:
        return "unknown"
    i = next((k for k, row in enumerate(dseries) if row[0] >= ts), len(dseries) - 1)
    if i > 0:
        i -= 1
    if i < 60:
        return "unknown"
    closes = [row[4] for row in dseries[max(0, i - 200): i + 1]]
    ema = sum(closes) / len(closes)
    px = closes[-1]
    base = dseries[i - 60][4] if i >= 60 else closes[0]
    mom60 = (px / base - 1.0) if base > 0 else 0.0
    if px > ema and mom60 > 0.05:
        return "up"
    if px < ema and mom60 < -0.05:
        return "down"
    return "chop"


def sim_new_exit(s, i, entry, sl_pct=6.0, trig=3.0, gap=1.5, max_h=168):
    peak = 0.0
    for k in range(i, min(i + int(max_h) + 1, len(s))):
        _ts, _o, h, l, c = s[k]
        hold = k - i
        mfe = (h - entry) / entry * 100
        peak = max(peak, mfe)
        if l <= entry * (1 - sl_pct / 100):
            return -sl_pct - cost_pct(hold)
        if peak >= trig and mfe <= peak - gap:
            return max(peak - gap, 0) - cost_pct(hold)
        if k == min(i + int(max_h), len(s) - 1):
            return (c - entry) / entry * 100 - cost_pct(hold)
    return None


def main() -> int:
    rows = load_longs()
    h1, d1 = load_klines({r["symbol"] for r in rows})
    recs = []
    for r in rows:
        s = pick(h1, r["symbol"])
        ds = pick(d1, r["symbol"])
        if not s or not ds:
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
        k72 = min(i + 72, len(s) - 1)
        fwd72 = (s[k72][4] - entry) / entry * 100 - cost_pct(72)
        new_r = sim_new_exit(s, i, entry)
        win = s[max(0, i - 23): i + 1]
        hi = max(x[2] for x in win)
        lo = min(x[3] for x in win)
        pos24 = (entry - lo) / (hi - lo) * 100 if hi > lo else 50.0
        chg24 = (s[i][4] / s[i - 24][4] - 1) * 100 if s[i - 24][4] > 0 else 0.0
        reg = regime_at(ds, ts)
        learned_pass = (reg == "up" and chg24 >= 3.0) or (reg == "chop" and pos24 >= 60.0 and chg24 >= 2.0)
        sid = str(r.get("strategy_id") or "?")
        if sid.startswith("brain") or "hub" in sid.lower() or sid.startswith("mlto"):
            fam = "brain/hub"
        elif sid in ("?", "None"):
            fam = "none"
        else:
            fam = sid[:16]
        recs.append({
            "nature": str(r.get("trade_nature") or "?"),
            "tier": str(r.get("timeframe_tier") or "?"),
            "src": r.get("_src", "?"),
            "sid_fam": fam,
            "dec_source": str(r.get("decision_source") or "?"),
            "opened": str(r["opened_at"])[:10],
            "regime": reg, "learned_pass": learned_pass,
            "actual": round(actual, 3), "fwd72": round(fwd72, 3),
            "new_net": round(new_r, 3) if new_r is not None else None,
        })

    print(f"可解析样本: {len(recs)}")

    def agg(rows_, label):
        if not rows_:
            return
        n = len(rows_)
        a = sum(x["actual"] for x in rows_) / n
        f72 = sum(x["fwd72"] for x in rows_) / n
        nn = [x["new_net"] for x in rows_ if x["new_net"] is not None]
        nn_s = f"{sum(nn)/len(nn):+.2f}" if nn else "-"
        lp = sum(1 for x in rows_ if x["learned_pass"]) / n
        print(f"  {label:<28} n={n:>4} 实际={a:>+8.2f}% 72h前向={f72:>+8.2f}% "
              f"新出场={nn_s}% 现行门通过率={lp:.2f}")

    print("\n=== 按 nature ===")
    for k in ("swing", "trend_follow", "position", "?"):
        agg([x for x in recs if x["nature"] == k], f"nature={k}")
    print("\n=== 按 tier ===")
    for k in ("mid", "long"):
        agg([x for x in recs if x["tier"] == k], f"tier={k}")
    print("\n=== 按来源表 ===")
    for k in ("paper_positions", "strategy_trades"):
        agg([x for x in recs if x["src"] == k], f"src={k}")
    print("\n=== 按 strategy_id 家族（n≥5）===")
    byfam = defaultdict(list)
    for x in recs:
        byfam[x["sid_fam"]].append(x)
    for fam in sorted(byfam, key=lambda k: -len(byfam[k])):
        if len(byfam[fam]) >= 5:
            agg(byfam[fam], f"sid={fam}")
    print("\n=== 按决策来源（strategy_trades 才有 decision_source）===")
    bydec = defaultdict(list)
    for x in recs:
        if x["src"] == "strategy_trades":
            bydec[x["dec_source"]].append(x)
    for k in sorted(bydec, key=lambda kk: -len(bydec[kk])):
        if len(bydec[k]) >= 5:
            agg(bydec[k], f"dec={k}")
    print("\n=== 按 regime × 现行门通过 ===")
    for reg in ("up", "chop", "down"):
        agg([x for x in recs if x["regime"] == reg], f"regime={reg}")
        agg([x for x in recs if x["regime"] == reg and x["learned_pass"]], f"  └ 门通过")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n": len(recs), "recs": recs,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
