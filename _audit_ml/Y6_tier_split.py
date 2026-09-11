# -*- coding: utf-8 -*-
"""浮盈锁·按 tier 拆分（Y6）：mid（ExitPolicy 生效）vs long（Chandelier 车道）。

long 车道（nature=trend_follow/position 或 tier=long）跳过 ExitPolicy，
由 long_trend_v2 的「回撤 60/80%」规则管理——实测该规则放行到负收益
（BTC 峰值 +3.15% → 最终 -2.21%）。本脚本对两个 tier 分别跑路径叠加，
并给 long 车道试更宽的锁（1.0/0.3、1.5/0.5）与更紧的锁（0.5/0.15）。
"""
from __future__ import annotations

import os
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, text  # noqa: E402

ARENA_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
MARKET_URL = os.getenv("MARKET_DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_market")

FEE_SIDE = 0.0005
FUND_8H = 0.0001


def cost_pct(h, slip=0.0005):
    return (FEE_SIDE + slip) * 2 * 100 + (h / 8.0) * FUND_8H * 100


def load_trades(days=45):
    eng = create_engine(ARENA_URL)
    with eng.connect() as c:
        c.execute(text("set app.is_admin='on'"))
        return [dict(r._mapping) for r in c.execute(text(f"""
            select id, symbol, side, timeframe_tier, trade_nature, entry_price, close_price, sl_price,
                   peak_pnl_pct, close_reason, opened_at, closed_at
            from paper_positions
            where timeframe_tier in ('mid','long') and status='closed'
              and closed_at >= now() - interval '{int(days)} days'
            order by opened_at
        """)).fetchall()]


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


def pick(series, sym, ts):
    for ex in ("asterdex", "binance"):
        v = series.get((ex, sym))
        if v and len(v) > 200 and v[0][0] <= ts <= v[-1][0] + 86400:
            return v
    return None


def simulate(s, i, entry, side, close_ts, act, gap, sl_pct, strict=True, max_h=336):
    sign = 1.0 if side == "long" else -1.0
    cur_sl = entry * (1 - sign * sl_pct / 100.0)
    peak = 0.0
    for k in range(i, len(s)):
        ts, _o, h, l, c = s[k]
        hold = (ts - s[i][0]) / 3600.0
        if hold > max_h:
            return sign * (c - entry) / entry * 100 - cost_pct(hold)
        hi = sign * (h - entry) / entry * 100
        lo = sign * (l - entry) / entry * 100
        prev = peak
        peak = max(peak, hi)
        if lo <= sign * (cur_sl - entry) / entry * 100:
            return sign * (cur_sl - entry) / entry * 100 - cost_pct(hold)
        if act and gap is not None and peak >= act:
            same_bar = prev < act <= peak
            if not (strict and same_bar):
                new_sl = entry * (1 + sign * (peak - gap) / 100.0)
                if (side == "long" and new_sl > cur_sl) or (side == "short" and new_sl < cur_sl):
                    cur_sl = new_sl
                    if lo <= sign * (cur_sl - entry) / entry * 100:
                        return sign * (cur_sl - entry) / entry * 100 - cost_pct(hold)
        if ts >= close_ts:
            return None
    return None


def main() -> int:
    trades = load_trades(45)
    h1 = load_klines({t["symbol"] for t in trades})
    recs = []
    for t in trades:
        entry = float(t["entry_price"] or 0)
        close = float(t["close_price"] or 0)
        if entry <= 0 or close <= 0:
            continue
        ts = int(t["opened_at"].timestamp())
        s = pick(h1, t["symbol"], ts)
        if not s:
            continue
        i = next((k for k, row in enumerate(s) if row[0] >= ts), None)
        if i is None or i == 0 or abs(s[i][0] - ts) > 7200:
            continue
        side = str(t["side"] or "long")
        sign = 1.0 if side == "long" else -1.0
        hold_h = (t["closed_at"] - t["opened_at"]).total_seconds() / 3600 if t["closed_at"] else 0
        sl = float(t["sl_price"] or 0)
        sl_pct = abs(entry - sl) / entry * 100 if sl > 0 else 6.0
        sl_pct = min(max(sl_pct, 0.5), 12.0)
        tier = str(t["timeframe_tier"] or "mid")
        recs.append({
            "s": s, "i": i, "entry": entry, "side": side, "tier": tier,
            "nature": str(t["trade_nature"] or "?"),
            "close_ts": int(t["closed_at"].timestamp()) if t["closed_at"] else s[-1][0],
            "actual": sign * (close - entry) / entry * 100 - cost_pct(hold_h),
            "peak_db": float(t["peak_pnl_pct"] or 0) * 100,
            "sl_pct": sl_pct, "symbol": t["symbol"],
        })

    configs = [(None, None), (0.5, 0.15), (0.4, 0.15), (0.8, 0.2), (1.0, 0.3), (1.5, 0.5)]
    print(f"样本 n={len(recs)}（近 45 天）")
    for tier in ("mid", "long", "ALL"):
        sub = recs if tier == "ALL" else [r for r in recs if r["tier"] == tier]
        if not sub:
            continue
        print(f"\n=== tier={tier} n={len(sub)} ===")
        print(f"{'锁(act/gap)':<14}{'均值':>9}{'中位':>8}{'胜率':>7}{'合计':>9}{'≤-2%':>7}{'最差':>8}")
        for act, gap in configs:
            nets = []
            for r in sub:
                v = simulate(r["s"], r["i"], r["entry"], r["side"], r["close_ts"], act, gap, r["sl_pct"])
                nets.append(r["actual"] if v is None else v)
            m = sum(nets) / len(nets)
            print(f"{(f'{act}/{gap}' if act else '基线'):<14}{m:>+9.3f}{st.median(nets):>+8.3f}"
                  f"{sum(1 for x in nets if x > 0)/len(nets):>7.3f}{sum(nets):>+9.1f}"
                  f"{sum(1 for x in nets if x <= -2):>7}{min(nets):>+8.2f}")

    print("\n=== long 车道逐笔（tier=long）===")
    for r in [x for x in recs if x["tier"] == "long"]:
        v_lock = simulate(r["s"], r["i"], r["entry"], r["side"], r["close_ts"], 1.0, 0.3, r["sl_pct"])
        v_tight = simulate(r["s"], r["i"], r["entry"], r["side"], r["close_ts"], 0.5, 0.15, r["sl_pct"])
        print(f"  {r['symbol']:<8} {r['side']:<5} peak={r['peak_db']:>+6.2f}% 实际={r['actual']:>+6.2f}% "
              f"锁1.0/0.3={('%.2f' % v_lock) if v_lock is not None else '—':>7} "
              f"锁0.5/0.15={('%.2f' % v_tight) if v_tight is not None else '—':>7}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
