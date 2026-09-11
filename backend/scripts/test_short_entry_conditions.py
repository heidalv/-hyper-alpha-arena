# -*- coding: utf-8 -*-
"""空头入场条件大样本验证（第十五轮补充）：验证 learned 空头闸的两个条件。

历史空头归因结论：入场毒性（追涨/区间上沿入场）而非出场造成负期望。
本脚本在 30 币大样本 down-regime 上验证：
  Q1: pos24>=60（区间上沿）到底有没有正贡献？
  Q2: chg24（前24h涨幅）能否过滤掉毒性入场（不追涨）？
  Q3: 时间切分 / 条件组合的稳健性（72h 固定 vs SL6/trail3-1.5/168h）。

输出：`data/short_entry_conditions.json`
"""
from __future__ import annotations

import json
import os
import statistics as st
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, text  # noqa: E402

MARKET_URL = os.getenv("MARKET_DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_market")
OUT = ROOT / "data" / "short_entry_conditions.json"

SYMS = ["BTC","ETH","SOL","BNB","XRP","DOGE","ADA","AVAX","LINK","DOT","LTC","TON",
        "TRX","ATOM","BCH","ETC","UNI","AAVE","ARB","OP","SUI","APT","NEAR","INJ",
        "SEI","CRV","ASTER","XPL","VIRTUAL","ZEC"]

FEE_SIDE = 0.0005
SLIP_SIDE = 0.0005
FUND_8H = 0.0001
START_TS = int(datetime(2025, 7, 1, tzinfo=timezone.utc).timestamp())


def cost_pct(h):
    return (FEE_SIDE + SLIP_SIDE) * 2 * 100 + (h / 8.0) * FUND_8H * 100


def load():
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
            """), {"ex": exch, "syms": SYMS}).fetchall():
                h1[(exch, s)].append((int(ts), float(o), float(h), float(l), float(cl)))
            for s, ts, o, h, l, cl in c.execute(text("""
                select symbol, timestamp, open_price, high_price, low_price, close_price
                from crypto_klines where period='1d' and exchange=:ex and symbol = any(:syms)
                order by symbol, timestamp
            """), {"ex": exch, "syms": SYMS}).fetchall():
                d1[(exch, s)].append((int(ts), float(o), float(h), float(l), float(cl)))
    return h1, d1


def pick(series, sym):
    for ex in ("asterdex", "binance"):
        v = series.get((ex, sym))
        if v and len(v) > 500:
            return v
    return None


def daily_regime_at(dseries, ts):
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


def build_signals(h1, d1, sample_step=3):
    """down-regime 1h bars with features; keep raw feature values for bucketing."""
    out = []
    for sym in SYMS:
        s = pick(h1, sym)
        ds = pick(d1, sym)
        if not s or not ds:
            continue
        for i in range(100, len(s) - 1, sample_step):
            ts = s[i][0]
            if ts < START_TS:
                continue
            reg = daily_regime_at(ds, ts)
            if reg != "down":
                continue
            # RSI14
            gains, losses = [], []
            for k in range(i - 14, i + 1):
                d = s[k][4] - s[k - 1][4]
                gains.append(max(d, 0.0))
                losses.append(max(-d, 0.0))
            ag = sum(gains) / 14.0
            al = sum(losses) / 14.0
            rsi = 100.0 - 100.0 / (1.0 + ag / al) if al > 0 else 100.0
            # pos24 / chg24
            win = s[i - 23: i + 1]
            hi = max(x[2] for x in win)
            lo = min(x[3] for x in win)
            close = s[i][4]
            pos = (close - lo) / (hi - lo) * 100 if hi > lo else 50.0
            chg = (close / s[i - 24][4] - 1) * 100 if s[i - 24][4] > 0 else 0.0
            out.append((sym, i, ts, close, pos, rsi, chg))
    return out


def sim_fixed(s, i, h):
    if i + h >= len(s):
        return None
    entry = s[i][4]
    return (entry - s[i + h][4]) / entry * 100 - cost_pct(h)


def sim_trail(s, i, sl_pct, trig, gap, max_h):
    entry = s[i][4]
    peak = 0.0
    for k in range(i, min(i + int(max_h) + 1, len(s))):
        _ts, _o, h, l, c = s[k]
        hold = k - i
        mfe = (entry - l) / entry * 100
        peak = max(peak, mfe)
        if h >= entry * (1 + sl_pct / 100):
            return -sl_pct - cost_pct(hold)
        if peak >= trig and mfe <= peak - gap:
            return max(peak - gap, 0) - cost_pct(hold)
        if k == min(i + int(max_h), len(s) - 1):
            return (entry - c) / entry * 100 - cost_pct(hold)
    return None


def stats(rows):
    if not rows:
        return None
    nets = [r for r in rows]
    return {"n": len(nets), "mean": round(sum(nets) / len(nets), 3),
            "median": round(st.median(nets), 3),
            "win": round(sum(1 for x in nets if x > 0) / len(nets), 3)}


def eval_set(h1, rows, name, results):
    """rows: (sym, i) pairs. Evaluate 72h fixed / 336h fixed / trail on each."""
    r72, r336, rtr = [], [], []
    for sym, i in rows:
        s = pick(h1, sym)
        if s is None:
            continue
        v = sim_fixed(s, i, 72)
        if v is not None:
            r72.append(v)
        v = sim_fixed(s, i, 336)
        if v is not None:
            r336.append(v)
        v = sim_trail(s, i, 6, 3, 1.5, 168)
        if v is not None:
            rtr.append(v)
    results[name] = {"72h": stats(r72), "336h": stats(r336),
                     "trail_SL6_t3_1.5_168h": stats(rtr)}


def main() -> int:
    h1, d1 = load()
    sigs = build_signals(h1, d1)
    print(f"down-regime 1h 信号总数（2025-07 起）: {len(sigs)}")

    results = {}
    # Q1: pos24 分桶（全 down，不筛 RSI）
    print("\n=== Q1 down-regime 全样本按 pos24 分桶 (72h 固定) ===")
    rows = {label: [] for label in ["<40", "40-60", "60-80", ">=80"]}
    for sym, i, ts, px, pos, rsi, chg in sigs:
        if pos < 40:
            rows["<40"].append((sym, i))
        elif pos < 60:
            rows["40-60"].append((sym, i))
        elif pos < 80:
            rows["60-80"].append((sym, i))
        else:
            rows[">=80"].append((sym, i))
    for label, v in rows.items():
        r72 = [x for sym, i in v if (x := sim_fixed(pick(h1, sym), i, 72)) is not None]
        stt = stats(r72)
        print(f"  pos24 {label:<6} n={stt['n']:>5} 72h净={stt['mean']:>+7.3f}% 中位={stt['median']:>+7.3f}% 胜率={stt['win']:.3f}")
        results[f"q1_pos_{label}"] = {"72h": stt}

    # Q2: chg24 分桶（全 down，不筛 RSI）
    print("\n=== Q2 down-regime 全样本按 chg24 分桶 (72h 固定) ===")
    rows2 = {label: [] for label in ["<-5", "-5~-1", "-1~+1", "+1~5", ">=+5"]}
    for sym, i, ts, px, pos, rsi, chg in sigs:
        if chg < -5:
            rows2["<-5"].append((sym, i))
        elif chg < -1:
            rows2["-5~-1"].append((sym, i))
        elif chg < 1:
            rows2["-1~+1"].append((sym, i))
        elif chg < 5:
            rows2["+1~5"].append((sym, i))
        else:
            rows2[">=+5"].append((sym, i))
    for label, v in rows2.items():
        r72 = [x for sym, i in v if (x := sim_fixed(pick(h1, sym), i, 72)) is not None]
        stt = stats(r72)
        print(f"  chg24 {label:<6} n={stt['n']:>5} 72h净={stt['mean']:>+7.3f}% 中位={stt['median']:>+7.3f}% 胜率={stt['win']:.3f}")
        results[f"q2_chg_{label}"] = {"72h": stt}

    # Q3: 条件组合
    def cond_rows(fn):
        return [(sym, i) for sym, i, ts, px, pos, rsi, chg in sigs if fn(pos, rsi, chg)]

    combos = {
        "A_learned(pos>=60&RSI55-80)": lambda p, r, c: p >= 60 and 55 <= r <= 80,
        "B_A+chg<=+2": lambda p, r, c: p >= 60 and 55 <= r <= 80 and c <= 2,
        "C_A+chg<=0": lambda p, r, c: p >= 60 and 55 <= r <= 80 and c <= 0,
        "D_A+chg<=-1": lambda p, r, c: p >= 60 and 55 <= r <= 80 and c <= -1,
        "E_pos40-60&RSI55-80": lambda p, r, c: 40 <= p < 60 and 55 <= r <= 80,
        "F_RSI55-80&chg<=0(无pos)": lambda p, r, c: 55 <= r <= 80 and c <= 0,
        "G_RSI55-80&chg<=-1(无pos)": lambda p, r, c: 55 <= r <= 80 and c <= -1,
    }
    print("\n=== Q3 条件组合（全部 down-regime, 2025-07 起）===")
    print(f"{'组合':<28}{'n':>6}{'72h%':>9}{'336h%':>9}{'trail%':>9}{'trail胜率':>9}")
    for name, fn in combos.items():
        rows = cond_rows(fn)
        eval_set(h1, rows, name, results)
        r = results[name]
        s72, s336, strl = r["72h"], r["336h"], r["trail_SL6_t3_1.5_168h"]
        print(f"{name:<28}{s72['n']:>6}{(s72['mean'] if s72 else float('nan')):>+9.2f}"
              f"{(s336['mean'] if s336 else float('nan')):>+9.2f}"
              f"{(strl['mean'] if strl else float('nan')):>+9.2f}"
              f"{(strl['win'] if strl else float('nan')):>9.3f}")

    # Q4: 时间切分（数据中位时间）
    med_ts = st.median(ts for _s, _i, ts, _p, _pos, _r, _c in sigs)
    print(f"\n=== Q4 时间切分（中位时间 {datetime.fromtimestamp(med_ts, tz=timezone.utc).date()}）===")
    for name, fn in combos.items():
        a = [(sym, i) for sym, i, ts, px, pos, rsi, chg in sigs if ts < med_ts and fn(pos, rsi, chg)]
        b = [(sym, i) for sym, i, ts, px, pos, rsi, chg in sigs if ts >= med_ts and fn(pos, rsi, chg)]
        ra = [x for sym, i in a if (x := sim_fixed(pick(h1, sym), i, 72)) is not None]
        rb = [x for sym, i in b if (x := sim_fixed(pick(h1, sym), i, 72)) is not None]
        sa, sb = stats(ra), stats(rb)
        print(f"  {name:<26} 前段 n={sa['n']:>5} 72h净={sa['mean']:>+7.3f}% | "
              f"后段 n={sb['n']:>5} 72h净={sb['mean']:>+7.3f}%")
        results.setdefault(name, {})["72h_split"] = {"early": sa, "late": sb}

    # Q5: D 稳健性深化（per-symbol / trail 时间切分 / 非重叠样本 / 无RSI变体）
    print("\n=== Q5 D组合深化: pos>=60&RSI55-80&chg<=-1 ===")
    ts_of = {(sym, i): ts for sym, i, ts, _px, _pos, _rsi, _chg in sigs}
    d_rows = [(sym, i) for sym, i, ts, px, pos, rsi, chg in sigs if pos >= 60 and 55 <= rsi <= 80 and chg <= -1]
    # trail 时间切分
    d_early = [(s, i) for s, i in d_rows if ts_of[(s, i)] < med_ts]
    d_late = [(s, i) for s, i in d_rows if ts_of[(s, i)] >= med_ts]
    for label, v in [("前段", d_early), ("后段", d_late)]:
        rtr = [x for s, i in v if (x := sim_trail(pick(h1, s), i, 6, 3, 1.5, 168)) is not None]
        stt = stats(rtr)
        print(f"  trail {label}: n={stt['n']:>4} 净={stt['mean']:>+7.3f}% 中位={stt['median']:>+7.3f}% 胜率={stt['win']:.3f}")
        results.setdefault("q5_D_trail_split", {})[label] = stt
    # 非重叠样本（(i-100)%24==0 → 每 8 个采样点一根 ≈ 每日一根；72h 窗仍 3 重叠加，但大幅降相关）
    d_non = [(sym, i) for sym, i in d_rows if (i - 100) % 24 == 0]
    r72n = [x for sym, i in d_non if (x := sim_fixed(pick(h1, sym), i, 72)) is not None]
    stt = stats(r72n)
    print(f"  D非重叠(i%24==0): n={stt['n']:>4} 72h净={stt['mean']:>+7.3f}% 中位={stt['median']:>+7.3f}% 胜率={stt['win']:.3f}")
    results["q5_D_nonoverlap"] = {"72h": stt}
    # per-symbol（D，trail 口径）
    print("  D per-symbol (trail):")
    bysym = defaultdict(list)
    for sym, i in d_rows:
        v = sim_trail(pick(h1, sym), i, 6, 3, 1.5, 168)
        if v is not None:
            bysym[sym].append(v)
    per = {}
    for sym in sorted(bysym, key=lambda k: -len(bysym[k])):
        v = bysym[sym]
        per[sym] = stats(v)
        print(f"    {sym:<8} n={per[sym]['n']:>4} 净={per[sym]['mean']:>+7.3f}% 胜率={per[sym]['win']:.3f}")
    results["q5_D_per_symbol"] = per
    # 变体：去掉 RSI / 去掉 pos / 收紧 chg
    variants = {
        "D_noRSI(pos>=60&chg<=-1)": lambda p, r, c: p >= 60 and c <= -1,
        "D_chg<=-2": lambda p, r, c: p >= 60 and 55 <= r <= 80 and c <= -2,
        "D_chg<=-3": lambda p, r, c: p >= 60 and 55 <= r <= 80 and c <= -3,
    }
    print("  D 变体 (72h / trail):")
    for name, fn in variants.items():
        rows = [(sym, i) for sym, i, ts, px, pos, rsi, chg in sigs if fn(pos, rsi, chg)]
        r72 = [x for sym, i in rows if (x := sim_fixed(pick(h1, sym), i, 72)) is not None]
        rtr = [x for sym, i in rows if (x := sim_trail(pick(h1, sym), i, 6, 3, 1.5, 168)) is not None]
        s72, strl = stats(r72), stats(rtr)
        print(f"    {name:<26} n={s72['n']:>4} 72h净={s72['mean']:>+7.3f}% | "
              f"trail净={strl['mean']:>+7.3f}% 胜率={strl['win']:.3f}")
        results.setdefault("q5_D_variants", {})[name] = {"72h": s72, "trail": strl}

    # Q6: D 逐月稳健性（72h + trail），对照 A（原 learned）
    print("\n=== Q6 逐月切分（D: pos>=60&RSI55-80&chg<=-1；对照 A）===")
    ts_of_full = {(sym, i): ts for sym, i, ts, _px, _pos, _rsi, _chg in sigs}
    a_rows = [(sym, i) for sym, i, ts, px, pos, rsi, chg in sigs if pos >= 60 and 55 <= rsi <= 80]
    monthly = defaultdict(lambda: {"D72": [], "Dtr": [], "A72": []})
    for sym, i in d_rows:
        ym = datetime.fromtimestamp(ts_of_full[(sym, i)], tz=timezone.utc).strftime("%Y-%m")
        v = sim_fixed(pick(h1, sym), i, 72)
        if v is not None:
            monthly[ym]["D72"].append(v)
        v = sim_trail(pick(h1, sym), i, 6, 3, 1.5, 168)
        if v is not None:
            monthly[ym]["Dtr"].append(v)
    for sym, i in a_rows:
        ym = datetime.fromtimestamp(ts_of_full[(sym, i)], tz=timezone.utc).strftime("%Y-%m")
        v = sim_fixed(pick(h1, sym), i, 72)
        if v is not None:
            monthly[ym]["A72"].append(v)
    print(f"{'月份':<9}{'D n':>5}{'D 72h%':>9}{'D trail%':>10}{'A n':>6}{'A 72h%':>9}")
    q6 = {}
    for ym in sorted(monthly):
        m = monthly[ym]
        sD72, sDtr, sA72 = stats(m["D72"]), stats(m["Dtr"]), stats(m["A72"])
        if sD72["n"] == 0:
            continue
        q6[ym] = {"D_72h": sD72, "D_trail": sDtr, "A_72h": sA72}
        print(f"{ym:<9}{sD72['n']:>5}{sD72['mean']:>+9.2f}{sDtr['mean']:>+10.2f}"
              f"{sA72['n']:>6}{sA72['mean']:>+9.2f}")
    results["q6_monthly"] = q6

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n_signals": len(sigs),
        "results": results,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
