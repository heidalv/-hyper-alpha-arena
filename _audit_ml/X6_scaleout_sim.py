# -*- coding: utf-8 -*-
"""分档止盈（scale-out）反事实模拟（X6）：双窗口。

X5 发现：真正分档止盈只占 5.4%（tp_level 1 档=2%，而中位峰值 1.5%）；
34.2% 的「部分平仓」是主动通道在**亏损时**减仓。本脚本模拟**小幅分档止盈**：
在 +0.8~2% 之间分批锁定部分利润，剩余仓位沿用现行 SL6+trail3/1.5，
看能否在**不引发整体扫损**的前提下改善「浮盈→亏损」子集。

保守假设：同一根 K 线内 SL 与 TP 同时触发时按 SL 先成交。
输出：`data/scaleout_sim.json`
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
OUT = ROOT / "data" / "scaleout_sim.json"

FEE_SIDE = 0.0005
SLIP_SIDE = 0.0005
FUND_8H = 0.0001


def tranche_cost(h):
    """单个 tranche 的往返成本（%）：2×(fee+slip) + funding。"""
    return (FEE_SIDE + SLIP_SIDE) * 2 * 100 + (h / 8.0) * FUND_8H * 100


def load_trades(days):
    eng = create_engine(ARENA_URL)
    with eng.connect() as c:
        c.execute(text("set app.is_admin='on'"))
        return [dict(r._mapping) for r in c.execute(text(f"""
            select id, symbol, side, timeframe_tier, entry_price, close_price,
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


def pick(series, sym):
    for ex in ("asterdex", "binance"):
        v = series.get((ex, sym))
        if v and len(v) > 200:
            return v
    return None


def sim_scaleout(s, i, entry, side, *, tps=(), sl_pct=6.0, trail=(3.0, 1.5), max_h=168):
    """tps: [(weight, tp_pct)]，按触发顺序；剩余权重走 SL+trail。
    返回 (净%加权, 峰值)。"""
    sign = 1.0 if side == "long" else -1.0
    remaining = 1.0
    realized = 0.0
    tps_left = list(tps)
    cur_sl = entry * (1 - sign * sl_pct / 100.0)
    peak = 0.0
    for k in range(i, min(i + int(max_h) + 1, len(s))):
        _ts, _o, h, l, c = s[k]
        hold = k - i
        hi = sign * (h - entry) / entry * 100
        lo = sign * (l - entry) / entry * 100
        peak = max(peak, hi)
        # 1) SL 先判（保守）
        if lo <= sign * (cur_sl - entry) / entry * 100:
            roi = sign * (cur_sl - entry) / entry * 100
            realized += remaining * (roi - tranche_cost(hold))
            return realized, peak
        # 2) 分档止盈
        while tps_left and hi >= tps_left[0][1]:
            w, tp = tps_left.pop(0)
            w = min(w, remaining)
            realized += w * (tp - tranche_cost(hold))
            remaining -= w
            if remaining <= 1e-9:
                return realized, peak
        # 3) 追踪收紧
        if trail and peak >= trail[0]:
            new_sl = entry * (1 + sign * (peak - trail[1]) / 100.0)
            if (side == "long" and new_sl > cur_sl) or (side == "short" and new_sl < cur_sl):
                cur_sl = new_sl
        # 4) 超时
        if k == min(i + int(max_h), len(s) - 1):
            roi = sign * (c - entry) / entry * 100
            realized += remaining * (roi - tranche_cost(hold))
            return realized, peak
    return None, peak


VARIANTS = {
    "S0 现行(无分档, SL6+trail3/1.5)": dict(tps=()),
    "S1 50%@+1.0%": dict(tps=[(0.5, 1.0)]),
    "S2 33%@+0.8%,33%@+1.6%": dict(tps=[(0.33, 0.8), (0.33, 1.6)]),
    "S3 50%@+1.5%": dict(tps=[(0.5, 1.5)]),
    "S4 25%@+0.8%,25%@+1.5%": dict(tps=[(0.25, 0.8), (0.25, 1.5)]),
    "S5 50%@+1.0% + trail1.5/1.0": dict(tps=[(0.5, 1.0)], trail=(1.5, 1.0)),
    "S6 33%@+1.0%,33%@+2.0%": dict(tps=[(0.33, 1.0), (0.33, 2.0)]),
    "S7 25%@+1.0%,25%@+2.0%,25%@+3.0%": dict(tps=[(0.25, 1.0), (0.25, 2.0), (0.25, 3.0)]),
}


def run(days):
    trades = load_trades(days)
    h1 = load_klines({t["symbol"] for t in trades})
    rows = []
    for t in trades:
        s = pick(h1, t["symbol"])
        if not s:
            continue
        entry = float(t["entry_price"] or 0)
        close = float(t["close_price"] or 0)
        if entry <= 0 or close <= 0:
            continue
        ts = int(t["opened_at"].timestamp())
        i = next((k for k, row in enumerate(s) if row[0] >= ts), None)
        if i is None:
            continue
        side = str(t["side"] or "long")
        hold_h = (t["closed_at"] - t["opened_at"]).total_seconds() / 3600 if t["closed_at"] else 0
        raw = (close - entry) / entry * 100 if side == "long" else (entry - close) / entry * 100
        rows.append({"s": s, "i": i, "entry": entry, "side": side,
                     "peak": float(t["peak_pnl_pct"] or 0) * 100,
                     "actual": raw - tranche_cost(hold_h)})
    return rows


def main() -> int:
    out = {}
    for days in (14, 30):
        rows = run(days)
        print(f"\n=== {days} 天窗口：n={len(rows)} ===")
        print(f"{'方案':<40}{'净均值':>9}{'中位':>8}{'胜率':>7}{'合计':>9}{'峰值≥0.5亏损组':>17}")
        res = {}
        lowsub = [r for r in rows if r["peak"] >= 0.5 and r["actual"] < 0]
        for name, cfg in VARIANTS.items():
            nets, sub = [], []
            for r in rows:
                v = sim_scaleout(r["s"], r["i"], r["entry"], r["side"], **cfg)
                if v[0] is None:
                    continue
                nets.append(v[0])
                if r in lowsub:
                    sub.append(v[0])
            m = sum(nets) / len(nets)
            sm = round(sum(sub) / len(sub), 3) if sub else None
            res[name] = {"n": len(nets), "mean": round(m, 3),
                         "median": round(st.median(nets), 3),
                         "win": round(sum(1 for x in nets if x > 0) / len(nets), 3),
                         "sum": round(sum(nets), 1), "loss_peak_subset": sm}
            print(f"{name:<40}{res[name]['mean']:>+9.3f}{res[name]['median']:>+8.3f}"
                  f"{res[name]['win']:>7.3f}{res[name]['sum']:>+9.1f}"
                  f"{(f'{sm:+.3f}(n{len(sub)})' if sm is not None else '-'):>17}")
        out[f"{days}d"] = res
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(), "variants": out,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
