# -*- coding: utf-8 -*-
"""新空头策略候选回测（第十四轮）：用 30 币大样本重挖空头入场条件。

历史空头信号已证无法通过过滤救活（全 117 笔净负，最好组合 -8.4bp/胜率 0.18）。
本脚本从零设计空头入场（不依赖历史信号），在日线 down-regime 上测：

  入场：regime=down + 24h区间分位≥60%（反弹到区间上沿）+ RSI14≥55（超买反弹）
  出场：固定持有 24/48/72h 或 SL/Trail 结构

输出费后净边际（价格口径 bp），判断是否值得做 `learned` 空头。
用法：.venv\\Scripts\\python.exe backend/scripts/backtest_new_short.py
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
OUT = ROOT / "data" / "new_short_backtest.json"

SYMS = ["BTC","ETH","SOL","BNB","XRP","DOGE","ADA","AVAX","LINK","DOT","LTC","TON",
        "TRX","ATOM","BCH","ETC","UNI","AAVE","ARB","OP","SUI","APT","NEAR","INJ",
        "SEI","CRV","ASTER","XPL","VIRTUAL","ZEC"]

FEE_SIDE = 0.0005
SLIP_SIDE = 0.0005
FUND_8H = 0.0001


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


def signals(h1, d1, *, pos_min=60.0, rsi_min=55.0, rsi_max=80.0, sample_step=4):
    out = []
    for sym in SYMS:
        s = pick(h1, sym)
        ds = pick(d1, sym)
        if not s or not ds:
            continue
        closes = np.array([x[4] for x in s])
        for i in range(24, len(s) - 1, sample_step):
            # RSI14
            if i < 15:
                continue
            seg = closes[i - 14: i + 1]
            delta = np.diff(seg)
            gain = np.mean(np.clip(delta, 0, None))
            loss = np.mean(np.clip(-delta, 0, None))
            rsi = 100 - 100 / (1 + gain / loss) if loss > 0 else 100.0
            if rsi < rsi_min or rsi > rsi_max:
                continue
            win = s[max(0, i - 23): i + 1]
            hi = max(x[2] for x in win)
            lo = min(x[3] for x in win)
            pos = (closes[i] - lo) / (hi - lo) * 100 if hi > lo else 50.0
            if pos < pos_min:
                continue
            reg = daily_regime_at(ds, s[i][0])
            if reg != "down":
                continue
            out.append((sym, i, s[i][0], closes[i], reg, pos, rsi))
    return out


def sim(s, i, h):
    if i + h >= len(s):
        return None
    entry = s[i][4]
    ret = (entry - s[i + h][4]) / entry * 100
    return ret - cost_pct(h)


def sim_trail(s, i, sl_pct, trig, gap, max_h):
    entry = s[i][4]
    peak = 0.0
    for k in range(i, min(i + int(max_h) + 1, len(s))):
        _ts, _o, h, l, c = s[k]
        hold = k - i
        mfe = (entry - l) / entry * 100
        peak = max(peak, mfe)
        if h >= entry * (1 + sl_pct / 100):
            return -sl_pct - cost_pct(hold), hold, "sl"
        if peak >= trig and mfe <= peak - gap:
            return max(peak - gap, 0) - cost_pct(hold), hold, "trail"
        if k == min(i + int(max_h), len(s) - 1):
            return (entry - c) / entry * 100 - cost_pct(hold), hold, "timeout"
    return None, 0, "no_data"


def stats(rows):
    if not rows:
        return None
    nets = [r[0] for r in rows]
    return {
        "n": len(rows),
        "net_mean": round(sum(nets) / len(nets), 3),
        "net_median": round(st.median(nets), 3),
        "win": round(sum(1 for x in nets if x > 0) / len(nets), 3),
    }


def main() -> int:
    h1, d1 = load()
    sigs = signals(h1, d1)
    print(f"新空头信号: {len(sigs)}（down-regime + 区间分位≥60 + RSI∈[55,80]）")
    reg_count = defaultdict(int)
    for s in sigs:
        reg_count[s[4]] += 1
    print("regime:", dict(reg_count))

    results = {}
    print(f"\n{'策略':<34}{'n':>5}{'净均值bp':>10}{'中位bp':>10}{'胜率':>7}")
    for h in (24, 48, 72, 168):
        rows = []
        for sym, i, _ts, _px, _reg, _pos, _rsi in sigs:
            r = sim(h1[(next(ex for ex in ("asterdex", "binance") if (ex, sym) in h1), sym)], i, h) \
                if False else None
            # 直接用原始序列
            s = pick(h1, sym)
            if s is None:
                continue
            r = sim(s, i, h)
            if r is not None:
                rows.append((r,))
        stt = stats(rows)
        results[f"hold{h}h"] = stt
        if stt:
            print(f"{'固定持有 ' + str(h) + 'h':<34}{stt['n']:>5}{stt['net_mean']:>10.2f}{stt['net_median']:>10.2f}{stt['win']:>7.3f}")

    for sl, trig, gap, mh in ((6, 3, 1.5, 168), (5, 2, 1, 168), (8, 4, 2, 336), (4, 2, 1, 96)):
        rows = []
        for sym, i, _ts, _px, _reg, _pos, _rsi in sigs:
            s = pick(h1, sym)
            if s is None:
                continue
            r = sim_trail(s, i, sl, trig, gap, mh)
            if r[0] is not None:
                rows.append(r)
        stt = stats(rows)
        name = f"SL{sl}/trail{trig}-{gap}/max{mh}h"
        results[name] = stt
        if stt:
            print(f"{name:<34}{stt['n']:>5}{stt['net_mean']:>10.2f}{stt['net_median']:>10.2f}{stt['win']:>7.3f}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n_signals": len(sigs),
        "results": results,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {OUT}")
    pos = {k: v for k, v in results.items() if v and v["net_mean"] > 0}
    print(f"正期望策略: {len(pos)}")
    for k, v in sorted(pos.items(), key=lambda kv: -kv[1]["net_mean"]):
        print(f"  [OK] {k}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
