# -*- coding: utf-8 -*-
"""多头信号特征学习（第十五轮，2026-09-09）。

为空头侧 learn_short_policy.py 的多头镜像：把全部历史 mid/long 多头成交特征化：
  regime(日线 up/chop/down) / 24h区间位置分位 / 14h RSI / 前24h涨跌 /
  20h 已实现波动 / 持仓时长 / SL距离 / tier / close_reason / 净收益(bp 价格口径)

数据源：paper_positions（165 笔 mid/long 多头已平仓）+ strategy_trades
（decision_context.nature ∈ swing/trend_follow/position 且 side=long，剔除 e2e）。

然后做：
  1. 单特征分桶（net bp / 胜率 / n）
  2. 组合条件扫描（找费后正期望子集）
输出：`data/long_learning.json` + 控制台。
用法：.venv\\Scripts\\python.exe backend/scripts/learn_long_policy.py
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

ARENA_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
MARKET_URL = os.getenv("MARKET_DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_market")
OUT = ROOT / "data" / "long_learning.json"

FEE_SIDE = 0.0005
SLIP_SIDE = 0.0005
FUND_8H = 0.0001


def load_longs():
    eng = create_engine(ARENA_URL)
    with eng.connect() as c:
        c.execute(text("set app.is_admin='on'"))
        rows = [dict(r._mapping) for r in c.execute(text("""
            select id, symbol, side, entry_price, close_price, sl_price, tp_price,
                   original_size, size, partial_realized_pnl, partial_fee_paid,
                   timeframe_tier, trade_nature, close_reason, opened_at, closed_at
            from paper_positions
            where timeframe_tier in ('mid','long') and side='long' and status='closed'
              and trade_nature is not distinct from trade_nature
            order by opened_at
        """)).fetchall()]
    # 补充 strategy_trades 中未被 paper_positions 覆盖的真实信号
    with eng.connect() as c:
        c.execute(text("set app.is_admin='on'"))
        st_rows = [dict(r._mapping) for r in c.execute(text("""
            select id, symbol, side, entry_price, exit_price as close_price, null as sl_price,
                   null as tp_price, position_size as original_size, position_size as size,
                   null as partial_realized_pnl, null as partial_fee_paid,
                   null as timeframe_tier, null as trade_nature, null as close_reason,
                   opened_at, closed_at, decision_context, strategy_id
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
                    dc = eval(dc)  # noqa: S307 —— 与既有审计脚本同口径
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
    d1 = defaultdict(list)
    eng = create_engine(MARKET_URL)
    with eng.connect() as c:
        c.execute(text("set statement_timeout='900000'"))
        for exch in ("asterdex", "binance"):
            for s, ts, o, h, l, cl, vol in c.execute(text("""
                select symbol, timestamp, open_price, high_price, low_price, close_price, volume
                from crypto_klines where period='1h' and exchange=:ex and symbol = any(:syms)
                order by symbol, timestamp
            """), {"ex": exch, "syms": list(symbols)}).fetchall():
                h1[(exch, s)].append((int(ts), float(o), float(h), float(l), float(cl), float(vol or 0)))
        for exch in ("asterdex", "binance"):
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


def regime_at(series, ts):
    if not series:
        return "unknown"
    i = None
    for k, row in enumerate(series):
        if row[0] >= ts:
            i = k
            break
    if i is None:
        i = len(series) - 1
    elif i > 0:
        i -= 1
    if i < 60:
        return "unknown"
    closes = [row[4] for row in series[max(0, i - 200): i + 1]]
    ema = sum(closes) / len(closes)
    px = closes[-1]
    base = series[i - 60][4] if i >= 60 and series[i - 60][4] > 0 else closes[0]
    mom60 = (px / base - 1.0) if base > 0 else 0.0
    if px > ema and mom60 > 0.05:
        return "up"
    if px < ema and mom60 < -0.05:
        return "down"
    return "chop"


def featurize(rows, h1, d1):
    out = []
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
        sz = float(r["original_size"] or r["size"] or 0)
        if entry <= 0 or close <= 0 or sz <= 0:
            continue
        price_ret = (close - entry) / entry * 100
        hold_h = (r["closed_at"] - r["opened_at"]).total_seconds() / 3600 if r["closed_at"] else 0
        cost = (FEE_SIDE + SLIP_SIDE) * 2 * 100 + (hold_h / 8.0) * FUND_8H * 100
        net = price_ret - cost
        usd = (close - entry) * sz + float(r["partial_realized_pnl"] or 0)
        win = s[max(0, i - 23): i + 1]
        hi = max(x[2] for x in win)
        lo = min(x[3] for x in win)
        pos24 = (entry - lo) / (hi - lo) * 100 if hi > lo else 50.0
        chg24 = (s[i][4] / s[i - 24][4] - 1) * 100 if s[i - 24][4] > 0 else 0.0
        closes = [x[4] for x in s[max(0, i - 14): i + 1]]
        gains, losses = [], []
        for a, b in zip(closes[:-1], closes[1:]):
            d = b - a
            gains.append(max(d, 0))
            losses.append(max(-d, 0))
        avg_g = sum(gains) / len(gains) if gains else 0
        avg_l = sum(losses) / len(losses) if losses else 0
        rsi = 100 - 100 / (1 + avg_g / avg_l) if avg_l > 0 else 100.0
        rets = [(s[k][4] / s[k - 1][4] - 1) * 100 for k in range(max(1, i - 19), i + 1) if s[k - 1][4] > 0]
        vol20 = float(np.std(rets)) if rets else 0.0
        sl_px = float(r["sl_price"] or 0)
        sl_dist = (entry - sl_px) / entry * 100 if sl_px > 0 else None
        regime = regime_at(ds, ts)
        out.append({
            "id": r["id"], "symbol": r["symbol"],
            "tier": str(r.get("timeframe_tier") or "?"),
            "nature": str(r.get("trade_nature") or "?"),
            "close_reason": str(r.get("close_reason") or "")[:40],
            "opened": str(r["opened_at"])[:19], "hold_h": round(hold_h, 2),
            "price_ret_bp": round(price_ret * 100, 3), "net_bp": round(net * 100, 3),
            "usd_pnl": round(usd, 3),
            "regime": regime, "pos24": round(pos24, 1), "chg24": round(chg24, 2),
            "rsi": round(rsi, 1), "vol20": round(vol20, 4),
            "sl_dist": round(sl_dist, 2) if sl_dist is not None else None,
        })
    return out


def bucket_stats(rows, keyf, bins=None):
    g = defaultdict(list)
    for r in rows:
        k = keyf(r)
        if bins is not None:
            k = _bucket(k, bins)
        g[k].append(r["net_bp"])
    out = {}
    for k in sorted(g, key=lambda x: str(x)):
        v = g[k]
        out[str(k)] = {
            "n": len(v), "net_mean": round(sum(v) / len(v), 2),
            "win": round(sum(1 for x in v if x > 0) / len(v), 3),
        }
    return out


def _bucket(x, bins):
    for b in bins:
        if x < b:
            return f"<{b}"
    return f">={bins[-1]}"


def main() -> int:
    rows = load_longs()
    h1, d1 = load_klines({r["symbol"] for r in rows})
    feats = featurize(rows, h1, d1)
    print(f"特征化样本: {len(feats)} / {len(rows)}")

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n": len(feats),
        "overall": bucket_stats(feats, lambda r: "ALL"),
        "by_regime": bucket_stats(feats, lambda r: r["regime"]),
        "by_pos24": bucket_stats(feats, lambda r: r["pos24"], [20, 40, 60, 80]),
        "by_rsi": bucket_stats(feats, lambda r: r["rsi"], [40, 55, 70]),
        "by_chg24": bucket_stats(feats, lambda r: r["chg24"], [-5, 0, 3, 6]),
        "by_vol20": bucket_stats(feats, lambda r: r["vol20"], [0.2, 0.4, 0.6]),
        "by_hold": bucket_stats(feats, lambda r: r["hold_h"], [2, 8, 24, 48]),
    }

    def show(title, d):
        print(f"\n=== {title} ===")
        for k, v in d.items():
            print(f"  {k:<12} n={v['n']:>4} 净均值={v['net_mean']:>+8.2f}bp 胜率={v['win']:.3f}")

    show("总体", report["overall"])
    show("按 regime", report["by_regime"])
    show("按 24h 区间分位", report["by_pos24"])
    show("按 RSI", report["by_rsi"])
    show("按 前24h 涨跌%", report["by_chg24"])
    show("按 20h 已实现波动", report["by_vol20"])
    show("按 持仓时长h", report["by_hold"])

    print("\n=== 组合条件扫描（n>=8 的子集，净均值排序）===")
    combos = []
    for reg in ("up", "chop", "down", None):
        for pos_lo, pos_hi in ((None, None), (None, 60), (None, 40), (20, 60)):
            for rsi_lo, rsi_hi in ((None, None), (None, 60), (40, None), (40, 60)):
                for chg_lo, chg_hi in ((None, None), (-5, None), (None, 6), (0, 6)):
                    sub = [r for r in feats
                           if (reg is None or r["regime"] == reg)
                           and (pos_lo is None or r["pos24"] >= pos_lo)
                           and (pos_hi is None or r["pos24"] < pos_hi)
                           and (rsi_lo is None or r["rsi"] >= rsi_lo)
                           and (rsi_hi is None or r["rsi"] < rsi_hi)
                           and (chg_lo is None or r["chg24"] >= chg_lo)
                           and (chg_hi is None or r["chg24"] < chg_hi)]
                    if len(sub) >= 8:
                        m = sum(x["net_bp"] for x in sub) / len(sub)
                        usd_m = sum(x["usd_pnl"] for x in sub)
                        w = sum(1 for x in sub if x["net_bp"] > 0) / len(sub)
                        combos.append((float(m), usd_m, w, len(sub), reg, pos_lo, pos_hi, rsi_lo, rsi_hi, chg_lo, chg_hi))
    combos.sort(key=lambda x: -x[0])
    report["top_combos"] = []
    for m, usd_m, w, n, reg, pl, ph, rl, rh, cl, ch in combos[:15]:
        desc = (f"regime={reg or '*'}, pos24∈[{pl or '-'},{ph or '-'}), "
                f"rsi∈[{rl or '-'},{rh or '-'}), chg24∈[{cl or '-'},{ch or '-'})")
        report["top_combos"].append({"net_mean": m, "usd": round(usd_m, 1), "win": w, "n": n, "desc": desc})
        print(f"  {m:>+8.2f}bp USD={usd_m:>+8.1f} 胜率={w:.2f} n={n:>3} | {desc}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
