# -*- coding: utf-8 -*-
"""浮盈锁稳健性检验（Y4）：同 bar 乐观偏差 / 前后段 / 逐币 / 滑点敏感度。

Y3 用 1h bar 的 low/high 判定锁触发（峰值与触发同 bar 时假设"先高后低"= 乐观）。
本脚本对候选锁配置（act/gap ∈ {0.4,0.5,0.6}×{0.15,0.2}）做：
  1. 同 bar 触发占比（乐观偏差度量）；
  2. 严格模式：同 bar 峰值+触发时**不给锁**（用实际结果）→ 悲观下界；
  3. 时间切分（前后段）；
  4. 逐币；
  5. 滑点敏感度（单边 5bp / 15bp / 30bp）。
"""
from __future__ import annotations

import json
import os
import statistics as st
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, text  # noqa: E402

ARENA_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
MARKET_URL = os.getenv("MARKET_DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_market")
OUT = ROOT / "data" / "lock_robust.json"

FEE_SIDE = 0.0005
FUND_8H = 0.0001


def cost_pct(h, slip_side):
    return (FEE_SIDE + slip_side) * 2 * 100 + (h / 8.0) * FUND_8H * 100


def load_trades(days):
    eng = create_engine(ARENA_URL)
    with eng.connect() as c:
        c.execute(text("set app.is_admin='on'"))
        return [dict(r._mapping) for r in c.execute(text(f"""
            select id, symbol, side, timeframe_tier, entry_price, close_price, sl_price,
                   peak_pnl_pct, opened_at, closed_at
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


def simulate(s, i, entry, side, close_ts, act, gap, sl_pct, slip_side, strict_same_bar, max_h=168):
    """返回 (净%, 原因, 是否同bar触发)。原因: lock/sl/timeout/actual。"""
    sign = 1.0 if side == "long" else -1.0
    cur_sl = entry * (1 - sign * sl_pct / 100.0)
    peak = 0.0
    for k in range(i, len(s)):
        ts, _o, h, l, c = s[k]
        hold = (ts - s[i][0]) / 3600.0
        if hold > max_h:
            return (sign * (c - entry) / entry * 100 - cost_pct(hold, slip_side), "timeout", False)
        hi = sign * (h - entry) / entry * 100
        lo = sign * (l - entry) / entry * 100
        prev_peak = peak
        peak = max(peak, hi)
        if lo <= sign * (cur_sl - entry) / entry * 100:
            return (sign * (cur_sl - entry) / entry * 100 - cost_pct(hold, slip_side), "sl", False)
        if act and gap is not None and peak >= act:
            same_bar = prev_peak < act <= peak  # 峰值首次达标发生在本 bar
            if strict_same_bar and same_bar:
                pass  # 悲观：本 bar 不给锁（假设先低后高）
            else:
                lock = peak - gap
                new_sl = entry * (1 + sign * lock / 100.0)
                if (side == "long" and new_sl > cur_sl) or (side == "short" and new_sl < cur_sl):
                    cur_sl = new_sl
                    if lo <= sign * (cur_sl - entry) / entry * 100:
                        return (sign * (cur_sl - entry) / entry * 100 - cost_pct(hold, slip_side),
                                "lock", same_bar)
        if ts >= close_ts:
            return (None, "actual", False)
    return (None, "no_data", False)


def main() -> int:
    cands = [(0.4, 0.15), (0.5, 0.15), (0.6, 0.15), (0.4, 0.2), (0.5, 0.2), (None, None)]
    out = {}
    for days in (30,):
        trades = load_trades(days)
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
            actual = sign * (close - entry) / entry * 100 - cost_pct(hold_h, 0.0005)
            sl = float(t["sl_price"] or 0)
            sl_pct = abs(entry - sl) / entry * 100 if sl > 0 else 6.0
            sl_pct = min(max(sl_pct, 0.5), 12.0)
            recs.append({"s": s, "i": i, "entry": entry, "side": side,
                         "close_ts": int(t["closed_at"].timestamp()) if t["closed_at"] else s[-1][0],
                         "actual": actual, "peak_db": float(t["peak_pnl_pct"] or 0) * 100,
                         "sl_pct": sl_pct, "symbol": t["symbol"],
                         "opened": str(t["opened_at"])[:19]})

        med = sorted(r["opened"] for r in recs)[len(recs) // 2]
        print(f"\n===== 30 天窗口 n={len(recs)} 中位时间={med} =====")
        for act, gap in cands:
            label = f"{act}/{gap}" if act else "基线(无锁)"
            for mode, slip in (("乐观", 0.0005), ("严格同bar", 0.0005),
                               ("严格+滑点15bp", 0.0015), ("严格+滑点30bp", 0.003)):
                nets, same_bar_n, lock_n = [], 0, 0
                for r in recs:
                    v, why, same = simulate(r["s"], r["i"], r["entry"], r["side"], r["close_ts"],
                                            act, gap, r["sl_pct"], slip, mode.startswith("严格"))
                    if v is None:
                        v = r["actual"]
                    else:
                        if same:
                            same_bar_n += 1
                        if why == "lock":
                            lock_n += 1
                    nets.append(v)
                mean = sum(nets) / len(nets)
                bad = sum(1 for x in nets if x <= -2.0)
                res_key = f"{label}|{mode}"
                out[res_key] = {"n": len(nets), "mean": round(mean, 3),
                                "median": round(st.median(nets), 3),
                                "win": round(sum(1 for x in nets if x > 0) / len(nets), 3),
                                "sum": round(sum(nets), 1), "bad_le_-2": bad,
                                "lock_n": lock_n, "same_bar_n": same_bar_n}
                if mode in ("乐观", "严格同bar"):
                    print(f"  {label:<10}{mode:<12} 均值={mean:>+7.3f} 中位={st.median(nets):>+7.3f} "
                          f"胜率={sum(1 for x in nets if x>0)/len(nets):.3f} 合计={sum(nets):>+7.1f} "
                          f"≤-2%={bad:>2} 锁={lock_n:>3} 同bar={same_bar_n:>3}")

            # 时间切分 + 逐币（仅严格同 bar 模式）
            a = [r for r in recs if r["opened"] < med]
            b = [r for r in recs if r["opened"] >= med]
            for seg_name, seg in (("前段", a), ("后段", b)):
                nets = []
                for r in seg:
                    v, _why, _same = simulate(r["s"], r["i"], r["entry"], r["side"], r["close_ts"],
                                              act, gap, r["sl_pct"], 0.0005, True)
                    nets.append(r["actual"] if v is None else v)
                if nets:
                    print(f"      {seg_name} n={len(nets)} 均值={sum(nets)/len(nets):>+7.3f}")

        # 逐币（选最优候选 0.5/0.15 严格模式）
        print("\n  === 逐币（0.5/0.15，严格同bar）===")
        bysym = defaultdict(list)
        for r in recs:
            v, _w, _s = simulate(r["s"], r["i"], r["entry"], r["side"], r["close_ts"],
                                 0.5, 0.15, r["sl_pct"], 0.0005, True)
            bysym[r["symbol"]].append((r["actual"], r["actual"] if v is None else v))
        for sym in sorted(bysym, key=lambda k: -len(bysym[k])):
            v = bysym[sym]
            if len(v) < 4:
                continue
            a_mean = sum(x[0] for x in v) / len(v)
            l_mean = sum(x[1] for x in v) / len(v)
            print(f"    {sym:<10} n={len(v):>3} 实际={a_mean:>+7.3f}% 加锁={l_mean:>+7.3f}% "
                  f"Δ={l_mean - a_mean:>+7.3f}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(), "res": out,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
