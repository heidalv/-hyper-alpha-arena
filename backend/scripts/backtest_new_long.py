# -*- coding: utf-8 -*-
"""新多头策略候选回测（第十五轮）：用 30 币大样本重挖多头入场条件。

历史多头信号的特征学习（learn_long_policy.py，234 笔）给出方向：
  regime=up、24h 区间分位<60、RSI≥40、chg24 温和（避免追涨>6% 与接刀<-5%）。
本脚本从零设计多头入场（不依赖历史信号），在 30 币 × 1h K 线上验证：

  入场：regime ∈ {up, up+chop} + 位置分位 + RSI + chg24 条件
  出场：新出场结构 SL6/追踪3-1.5/168h（生产同款）+ 固定持有对照

**诚实性（吸取 §14/§15 教训）**：
  1. 网格发现用 sample_step=4（与空头侧同口径）；
  2. 胜者必须过 **sample_step=24 非重叠验证**（|t| 口径），否则弃用。
输出：`data/new_long_backtest.json` + 控制台。
用法：.venv\\Scripts\\python.exe backend/scripts/backtest_new_long.py
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
OUT = ROOT / "data" / "new_long_backtest.json"

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


def signals(h1, d1, *, regs=("up",), pos_max=100.0, pos_min=None,
            rsi_lo=0.0, rsi_hi=None, chg_lo=None, chg_hi=None, sample_step=4):
    out = []
    for sym in SYMS:
        s = pick(h1, sym)
        ds = pick(d1, sym)
        if not s or not ds:
            continue
        closes = np.array([x[4] for x in s])
        for i in range(24, len(s) - 1, sample_step):
            if i < 15:
                continue
            # RSI14
            seg = closes[i - 14: i + 1]
            delta = np.diff(seg)
            gain = np.mean(np.clip(delta, 0, None))
            loss = np.mean(np.clip(-delta, 0, None))
            rsi = 100 - 100 / (1 + gain / loss) if loss > 0 else 100.0
            if rsi < rsi_lo or (rsi_hi is not None and rsi > rsi_hi):
                continue
            win = s[max(0, i - 23): i + 1]
            hi = max(x[2] for x in win)
            lo = min(x[3] for x in win)
            pos = (closes[i] - lo) / (hi - lo) * 100 if hi > lo else 50.0
            if pos >= pos_max or (pos_min is not None and pos < pos_min):
                continue
            chg24 = (closes[i] / closes[i - 24] - 1.0) * 100 if closes[i - 24] > 0 else 0.0
            if chg_lo is not None and chg24 < chg_lo:
                continue
            if chg_hi is not None and chg24 >= chg_hi:
                continue
            reg = daily_regime_at(ds, s[i][0])
            if reg not in regs:
                continue
            out.append((sym, i, s[i][0], closes[i], reg, pos, rsi, chg24))
    return out


def sim(s, i, h):
    if i + h >= len(s):
        return None
    entry = s[i][4]
    ret = (s[i + h][4] - entry) / entry * 100
    return ret - cost_pct(h)


def sim_trail(s, i, sl_pct, trig, gap, max_h):
    entry = s[i][4]
    peak = 0.0
    for k in range(i, min(i + int(max_h) + 1, len(s))):
        _ts, _o, h, l, c = s[k]
        hold = k - i
        mfe = (h - entry) / entry * 100
        peak = max(peak, mfe)
        if l <= entry * (1 - sl_pct / 100):
            return -sl_pct - cost_pct(hold), hold, "sl"
        if peak >= trig and mfe <= peak - gap:
            return max(peak - gap, 0) - cost_pct(hold), hold, "trail"
        if k == min(i + int(max_h), len(s) - 1):
            return (c - entry) / entry * 100 - cost_pct(hold), hold, "timeout"
    return None, 0, "no_data"


def stats(rows):
    if not rows:
        return None
    nets = [r[0] for r in rows]
    a = np.array(nets)
    t = float(a.mean() / (a.std(ddof=1) / np.sqrt(len(a)))) if len(a) > 1 else 0.0
    return {
        "n": len(rows),
        "net_mean": round(float(a.mean()), 3),
        "net_median": round(float(np.median(a)), 3),
        "win": round(float((a > 0).mean()), 3),
        "t": round(t, 2),
    }


def run_cfg(h1, sigs, exits):
    res = {}
    for name, fn in exits.items():
        rows = []
        for sym, i, _ts, _px, _reg, _pos, _rsi, _chg in sigs:
            s = pick(h1, sym)
            if s is None:
                continue
            r = fn(s, i)
            if r[0] is not None:
                rows.append(r)
        res[name] = stats(rows)
    return res


def fmt_line(name, stt):
    if stt:
        return (f"{name:<38}{stt['n']:>6}{stt['net_mean']:>+10.2f}{stt['net_median']:>+10.2f}"
                f"{stt['win']:>8.3f}{stt['t']:>+7.2f}")
    return f"{name:<38}  (无样本)"


def main() -> int:
    h1, d1 = load()

    # 出场族：生产同款优先
    exits = {
        "SL6/trail3-1.5/max168h": lambda s, i: sim_trail(s, i, 6, 3, 1.5, 168),
        "SL5/trail2-1/max168h": lambda s, i: sim_trail(s, i, 5, 2, 1, 168),
        "SL6/trail3-1.5/max336h": lambda s, i: sim_trail(s, i, 6, 3, 1.5, 336),
        "hold72h": lambda s, i: (r, 72, "hold") if (r := sim(s, i, 72)) is not None else (None, 0, ""),
        "hold168h": lambda s, i: (r, 168, "hold") if (r := sim(s, i, 168)) is not None else (None, 0, ""),
    }

    print("=" * 100)
    print("网格发现（sample_step=4，与空头侧同口径）")
    print("=" * 100)
    results = {}
    grid = [
        ("up,pos<60,rsi>=40,chg∈[-5,6)", dict(regs=("up",), pos_max=60.0, rsi_lo=40.0, chg_lo=-5.0, chg_hi=6.0)),
        ("up,pos<60,rsi>=40", dict(regs=("up",), pos_max=60.0, rsi_lo=40.0)),
        ("up,pos<60,rsi∈[35,65],chg∈[-5,6)", dict(regs=("up",), pos_max=60.0, rsi_lo=35.0, rsi_hi=65.0, chg_lo=-5.0, chg_hi=6.0)),
        ("up,pos<40,rsi>=40,chg∈[-5,6)", dict(regs=("up",), pos_max=40.0, rsi_lo=40.0, chg_lo=-5.0, chg_hi=6.0)),
        ("up,pos∈[20,60),rsi>=40", dict(regs=("up",), pos_max=60.0, pos_min=20.0, rsi_lo=40.0)),
        ("up+chop,pos<60,rsi>=40,chg∈[-5,6)", dict(regs=("up", "chop"), pos_max=60.0, rsi_lo=40.0, chg_lo=-5.0, chg_hi=6.0)),
        ("up+chop,pos<60,rsi∈[40,60],chg∈[-5,6)", dict(regs=("up", "chop"), pos_max=60.0, rsi_lo=40.0, rsi_hi=60.0, chg_lo=-5.0, chg_hi=6.0)),
        ("up,pos<60,rsi∈[40,60]", dict(regs=("up",), pos_max=60.0, rsi_lo=40.0, rsi_hi=60.0)),
        ("up,pos<60 (仅位置)", dict(regs=("up",), pos_max=60.0, rsi_lo=0.0)),
        ("up,无条件", dict(regs=("up",), pos_max=100.0, rsi_lo=0.0)),
        ("chop,pos<60,rsi>=40,chg∈[-5,6)", dict(regs=("chop",), pos_max=60.0, rsi_lo=40.0, chg_lo=-5.0, chg_hi=6.0)),
        ("down,pos<60,rsi>=40 (对照应负)", dict(regs=("down",), pos_max=60.0, rsi_lo=40.0)),
    ]
    header = f"{'条件':<38}{'n':>6}{'净均值bp':>10}{'中位bp':>10}{'胜率':>8}{'t':>7}"
    for name, kw in grid:
        sigs = signals(h1, d1, **kw)
        res = run_cfg(h1, sigs, exits)
        key = f"SL6/trail3-1.5/max168h"
        stt = res[key]
        results[name] = {"n_signals": len(sigs), "exit": stt}
        print(f"\n[{name}] 信号数={len(sigs)}")
        print(header)
        for en, estt in res.items():
            print(fmt_line(en, estt))

    # 非重叠验证：胜者必须过 sample_step=24
    print("\n" + "=" * 100)
    print("非重叠验证（sample_step=24，观察点不重叠；胜者必须在此口径下成立）")
    print("=" * 100)
    verify = {}
    for name, kw in grid:
        sigs = signals(h1, d1, sample_step=24, **kw)
        res = run_cfg(h1, sigs, exits)
        stt = res["SL6/trail3-1.5/max168h"]
        verify[name] = {"n_signals": len(sigs), "exit": stt}
        print(f"\n[{name}] 非重叠信号数={len(sigs)}")
        print(header)
        for en, estt in res.items():
            print(fmt_line(en, estt))

    # 第三层：前向窗口完全不重叠（sample_step=168 = max 持有期）+
    # 按币聚类稳健 t —— 吸取 §15 教训：t 必须在窗口不重叠口径下复核
    print("\n" + "=" * 100)
    print("第三层验证（sample_step=168，前向窗口完全不重叠 + 按币聚类稳健 t）")
    print("=" * 100)
    finalists = [
        ("up,pos<60,rsi∈[35,65],chg∈[-5,6)", dict(regs=("up",), pos_max=60.0, rsi_lo=35.0, rsi_hi=65.0, chg_lo=-5.0, chg_hi=6.0)),
        ("up,pos<60,rsi>=40,chg∈[-5,6)", dict(regs=("up",), pos_max=60.0, rsi_lo=40.0, chg_lo=-5.0, chg_hi=6.0)),
        ("up,pos<60,rsi∈[40,60]", dict(regs=("up",), pos_max=60.0, rsi_lo=40.0, rsi_hi=60.0)),
        ("up,pos<40,rsi>=40,chg∈[-5,6)", dict(regs=("up",), pos_max=40.0, rsi_lo=40.0, chg_lo=-5.0, chg_hi=6.0)),
        ("up+chop,pos<60,rsi>=40,chg∈[-5,6)", dict(regs=("up", "chop"), pos_max=60.0, rsi_lo=40.0, chg_lo=-5.0, chg_hi=6.0)),
    ]
    tier3 = {}
    for name, kw in finalists:
        sigs = signals(h1, d1, sample_step=168, **kw)
        rows = []
        by_sym = defaultdict(list)
        for sym, i, _ts, _px, _reg, _pos, _rsi, _chg in sigs:
            s = pick(h1, sym)
            if s is None:
                continue
            r = sim_trail(s, i, 6, 3, 1.5, 168)
            if r[0] is not None:
                rows.append(r)
                by_sym[sym].append(r[0])
        stt = stats(rows)
        # 按币聚类稳健标准误：聚类数=币数，SE = sqrt(Σ_g (n_g/n)^2 * var_g)
        a_all = np.array([r[0] for r in rows])
        g_var = 0.0
        n_tot = len(rows)
        for sym, v in by_sym.items():
            vv = np.array(v)
            g_var += (len(vv) / n_tot) ** 2 * vv.var(ddof=1) if len(vv) > 1 else 0.0
        se_cl = float(np.sqrt(g_var / 1.0)) if g_var > 0 else 0.0
        t_cl = float(a_all.mean() / se_cl) if se_cl > 0 else 0.0
        n_pos = sum(1 for sym, v in by_sym.items() if sum(v) > 0)
        per_sym = {sym: {"n": len(v), "net_mean_bp": round(sum(v) / len(v), 2)}
                   for sym, v in sorted(by_sym.items())}
        tier3[name] = {"n": len(rows), "exit": stt, "cluster_t": round(t_cl, 2),
                       "symbols_positive": f"{n_pos}/{len(by_sym)}", "by_symbol": per_sym}
        print(f"\n[{name}] 前向不重叠信号数={len(sigs)} 可模拟={len(rows)}")
        print(f"  均值 t（iid）: {stt['t'] if stt else 0:+.2f}  |  "
              f"按币聚类稳健 t: {t_cl:+.2f}  |  正期望币数: {n_pos}/{len(by_sym)}")
        print(f"  净均值={stt['net_mean'] if stt else 0:+.2f}bp 中位={stt['net_median'] if stt else 0:+.2f}bp "
              f"胜率={stt['win'] if stt else 0:.3f}")
        bad = {s: v for s, v in per_sym.items() if v["net_mean_bp"] < 0}
        print(f"  负期望币: {bad if bad else '无'}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "symbols": SYMS,
        "cost_roundtrip_bp": (FEE_SIDE + SLIP_SIDE) * 2 * 100,
        "grid": results,
        "verify_nonoverlap_step24": verify,
        "verify_nonoverlap_step168_cluster": tier3,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
