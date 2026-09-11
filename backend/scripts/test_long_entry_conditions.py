# -*- coding: utf-8 -*-
"""多头入场条件大样本验证（第十六轮）：验证 learned 多头闸条件。

多头深度归因（deep_long_attribution.py，140 笔）结论：**出场结构砍掉了入场边际**
（实际 -0.59% vs 72h 持有 +0.24% vs 14d 持有 +11.24%），而非入场无边际。
本脚本在 30 币大样本 up-regime 上验证候选 learned 条件：
  Q1: pos24<60（区间下沿）在 up-regime 是否有正贡献？
  Q2: chg24（前24h涨跌）哪个区间的 72h 前向漂移为正？
  Q3: 条件组合（72h 固定 vs SL6/trail3-1.5/168h）。
  Q4: 时间切分（前后段必须都正——空头侧教训：只有 chg24≤-1% 组合两段皆正）。
  Q5: 胜者深化（trail 时间切分 / 非重叠 / per-symbol / 变体）。

输出：`data/long_entry_conditions.json`
用法：.venv\\Scripts\\python.exe backend/scripts/test_long_entry_conditions.py
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
OUT = ROOT / "data" / "long_entry_conditions.json"

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


def build_signals(h1, d1, sample_step=3, regime=("up",)):
    """up-regime 1h bars with features; keep raw feature values for bucketing."""
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
            if reg not in regime:
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
    return (s[i + h][4] - entry) / entry * 100 - cost_pct(h)


def sim_trail(s, i, sl_pct, trig, gap, max_h):
    entry = s[i][4]
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
    print(f"up-regime 1h 信号总数（2025-07 起）: {len(sigs)}")

    results = {}
    # Q1: pos24 分桶（全 up，不筛 RSI/chg）
    print("\n=== Q1 up-regime 全样本按 pos24 分桶 (72h 固定) ===")
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
        print(f"  pos24 {label:<6} n={stt['n']:>6} 72h净={stt['mean']:>+7.3f}% 中位={stt['median']:>+7.3f}% 胜率={stt['win']:.3f}")
        results[f"q1_pos_{label}"] = {"72h": stt}

    # Q2: chg24 分桶（全 up，不筛 pos/RSI）
    print("\n=== Q2 up-regime 全样本按 chg24 分桶 (72h 固定) ===")
    rows2 = {label: [] for label in ["<-5", "-5~0", "0~3", "3~6", ">=+6"]}
    for sym, i, ts, px, pos, rsi, chg in sigs:
        if chg < -5:
            rows2["<-5"].append((sym, i))
        elif chg < 0:
            rows2["-5~0"].append((sym, i))
        elif chg < 3:
            rows2["0~3"].append((sym, i))
        elif chg < 6:
            rows2["3~6"].append((sym, i))
        else:
            rows2[">=+6"].append((sym, i))
    for label, v in rows2.items():
        r72 = [x for sym, i in v if (x := sim_fixed(pick(h1, sym), i, 72)) is not None]
        stt = stats(r72)
        print(f"  chg24 {label:<6} n={stt['n']:>6} 72h净={stt['mean']:>+7.3f}% 中位={stt['median']:>+7.3f}% 胜率={stt['win']:.3f}")
        results[f"q2_chg_{label}"] = {"72h": stt}

    # Q3: 条件组合（up-regime）
    def cond_rows(fn):
        return [(sym, i) for sym, i, ts, px, pos, rsi, chg in sigs if fn(pos, rsi, chg)]

    combos = {
        "A_pos<60&RSI35-65&chg[-5,6)": lambda p, r, c: p < 60 and 35 <= r <= 65 and -5 <= c < 6,
        "B_A无chg上限(chg>=-5)": lambda p, r, c: p < 60 and 35 <= r <= 65 and c >= -5,
        "C_chg上限3(chg[-5,3))": lambda p, r, c: p < 60 and 35 <= r <= 65 and -5 <= c < 3,
        "D_pos<60&RSI35-65(无chg)": lambda p, r, c: p < 60 and 35 <= r <= 65,
        "E_pos<60&chg[-5,6)(无RSI)": lambda p, r, c: p < 60 and -5 <= c < 6,
        "F_pos<60(仅位置)": lambda p, r, c: p < 60,
        "G_pos<40&RSI35-65&chg[-5,6)": lambda p, r, c: p < 40 and 35 <= r <= 65 and -5 <= c < 6,
        "H_up无条件": lambda p, r, c: True,
        "I_RSI40-60&pos<60&chg[-5,6)": lambda p, r, c: p < 60 and 40 <= r <= 60 and -5 <= c < 6,
    }
    print("\n=== Q3 条件组合（全部 up-regime, 2025-07 起）===")
    print(f"{'组合':<30}{'n':>7}{'72h%':>9}{'336h%':>9}{'trail%':>9}{'trail胜率':>9}")
    for name, fn in combos.items():
        rows = cond_rows(fn)
        eval_set(h1, rows, name, results)
        r = results[name]
        s72, s336, strl = r["72h"], r["336h"], r["trail_SL6_t3_1.5_168h"]
        print(f"{name:<30}{s72['n']:>7}{(s72['mean'] if s72 else float('nan')):>+9.2f}"
              f"{(s336['mean'] if s336 else float('nan')):>+9.2f}"
              f"{(strl['mean'] if strl else float('nan')):>+9.2f}"
              f"{(strl['win'] if strl else float('nan')):>9.3f}")

    # Q4: 时间切分（数据中位时间）——空头侧教训：后段转负的组合不能用
    med_ts = st.median(ts for _s, _i, ts, _p, _pos, _r, _c in sigs)
    print(f"\n=== Q4 时间切分（中位时间 {datetime.fromtimestamp(med_ts, tz=timezone.utc).date()}）===")
    for name, fn in combos.items():
        a = [(sym, i) for sym, i, ts, px, pos, rsi, chg in sigs if ts < med_ts and fn(pos, rsi, chg)]
        b = [(sym, i) for sym, i, ts, px, pos, rsi, chg in sigs if ts >= med_ts and fn(pos, rsi, chg)]
        ra = [x for sym, i in a if (x := sim_fixed(pick(h1, sym), i, 72)) is not None]
        rb = [x for sym, i in b if (x := sim_fixed(pick(h1, sym), i, 72)) is not None]
        sa, sb = stats(ra), stats(rb)
        print(f"  {name:<28} 前段 n={sa['n']:>6} 72h净={sa['mean']:>+7.3f}% | "
              f"后段 n={sb['n']:>6} 72h净={sb['mean']:>+7.3f}%")
        results.setdefault(name, {})["72h_split"] = {"early": sa, "late": sb}

    # chop-regime 同过滤对照（learned 门在 chop 也生效）
    print("\n=== chop-regime 同过滤对照（A 组合）===")
    sigs_c = build_signals(h1, d1, regime=("chop",))
    a_c = [(sym, i) for sym, i, ts, px, pos, rsi, chg in sigs_c if pos < 60 and 35 <= rsi <= 65 and -5 <= chg < 6]
    r72c = [x for sym, i in a_c if (x := sim_fixed(pick(h1, sym), i, 72)) is not None]
    rtrc = [x for sym, i in a_c if (x := sim_trail(pick(h1, sym), i, 6, 3, 1.5, 168)) is not None]
    sc72, sctr = stats(r72c), stats(rtrc)
    print(f"  chop+A组合 n={sc72['n']:>6} 72h净={sc72['mean']:>+7.3f}% 胜率={sc72['win']:.3f} | "
          f"trail净={sctr['mean']:>+7.3f}% 胜率={sctr['win']:.3f}")
    results["chop_A_combo"] = {"72h": sc72, "trail": sctr}
    # chop 时间切分
    med_c = st.median(ts for _s, _i, ts, _p, _pos, _r, _c in sigs_c)
    a_ca = [(s, i) for s, i, ts, px, p, r, c in sigs_c if ts < med_c and p < 60 and 35 <= r <= 65 and -5 <= c < 6]
    a_cb = [(s, i) for s, i, ts, px, p, r, c in sigs_c if ts >= med_c and p < 60 and 35 <= r <= 65 and -5 <= c < 6]
    for label, v in [("前段", a_ca), ("后段", a_cb)]:
        rv = [x for sym, i in v if (x := sim_fixed(pick(h1, sym), i, 72)) is not None]
        sv = stats(rv)
        print(f"  chop+A {label}: n={sv['n']:>6} 72h净={sv['mean']:>+7.3f}% 胜率={sv['win']:.3f}")

    # Q5: 胜者深化（A 组合：trail 时间切分 / 非重叠 / per-symbol / 变体）
    print("\n=== Q5 A组合深化: pos<60&RSI35-65&chg[-5,6) ===")
    ts_of = {(sym, i): ts for sym, i, ts, _px, _pos, _rsi, _chg in sigs}
    a_rows = [(sym, i) for sym, i, ts, px, pos, rsi, chg in sigs
              if pos < 60 and 35 <= rsi <= 65 and -5 <= chg < 6]
    a_early = [(s, i) for s, i in a_rows if ts_of[(s, i)] < med_ts]
    a_late = [(s, i) for s, i in a_rows if ts_of[(s, i)] >= med_ts]
    for label, v in [("前段", a_early), ("后段", a_late)]:
        rtr = [x for s, i in v if (x := sim_trail(pick(h1, s), i, 6, 3, 1.5, 168)) is not None]
        stt = stats(rtr)
        print(f"  trail {label}: n={stt['n']:>5} 净={stt['mean']:>+7.3f}% 中位={stt['median']:>+7.3f}% 胜率={stt['win']:.3f}")
        results.setdefault("q5_A_trail_split", {})[label] = stt
    a_non = [(sym, i) for sym, i in a_rows if (i - 100) % 24 == 0]
    r72n = [x for sym, i in a_non if (x := sim_fixed(pick(h1, sym), i, 72)) is not None]
    stt = stats(r72n)
    print(f"  A非重叠(i%24==0): n={stt['n']:>5} 72h净={stt['mean']:>+7.3f}% 中位={stt['median']:>+7.3f}% 胜率={stt['win']:.3f}")
    results["q5_A_nonoverlap"] = {"72h": stt}
    print("  A per-symbol (trail):")
    bysym = defaultdict(list)
    for sym, i in a_rows:
        v = sim_trail(pick(h1, sym), i, 6, 3, 1.5, 168)
        if v is not None:
            bysym[sym].append(v)
    per = {}
    for sym in sorted(bysym, key=lambda k: -len(bysym[k])):
        v = bysym[sym]
        per[sym] = stats(v)
        print(f"    {sym:<8} n={per[sym]['n']:>5} 净={per[sym]['mean']:>+7.3f}% 胜率={per[sym]['win']:.3f}")
    results["q5_A_per_symbol"] = per
    variants = {
        "A_noRSI(pos<60&chg[-5,6))": lambda p, r, c: p < 60 and -5 <= c < 6,
        "A_chg上限3": lambda p, r, c: p < 60 and 35 <= r <= 65 and -5 <= c < 3,
        "A_RSI40-60": lambda p, r, c: p < 60 and 40 <= r <= 60 and -5 <= c < 6,
        "A_RSI30-70": lambda p, r, c: p < 60 and 30 <= r <= 70 and -5 <= c < 6,
    }
    print("  A 变体 (72h / trail):")
    for name, fn in variants.items():
        rows = [(sym, i) for sym, i, ts, px, pos, rsi, chg in sigs if fn(pos, rsi, chg)]
        r72 = [x for sym, i in rows if (x := sim_fixed(pick(h1, sym), i, 72)) is not None]
        rtr = [x for sym, i in rows if (x := sim_trail(pick(h1, sym), i, 6, 3, 1.5, 168)) is not None]
        s72, strl = stats(r72), stats(rtr)
        print(f"    {name:<28} n={s72['n']:>5} 72h净={s72['mean']:>+7.3f}% | "
              f"trail净={strl['mean']:>+7.3f}% 胜率={strl['win']:.3f}")
        results.setdefault("q5_A_variants", {})[name] = {"72h": s72, "trail": strl}

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
