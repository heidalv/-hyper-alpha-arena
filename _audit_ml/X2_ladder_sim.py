# -*- coding: utf-8 -*-
"""浮盈保护阶梯反事实模拟（X2）。

现象：65% 的 mid/long 仓位峰值浮盈 <3%，而追踪止盈激活阈值=3% → 全程无保护，
最终由主动出场通道（thesis_invalidation/trend_broken/master_running 等）
在 -2%~-3.9% 处平掉。本脚本用 1h K 线对**同一批真实成交**模拟保护阶梯：

  基线C0：SL6 + trail 3.0/1.5（现行 EXIT_POLICY_MID_*）
  L1：SL6 + 保本@峰值1.0% + trail 3.0/1.5
  L2：SL6 + 保本@1.0% + trail 1.5/1.0
  L3：SL6 + 保本@0.8%(+0.15%缓冲) + trail 2.0/1.0 + trail 3.0/1.5（双段）
  L4：SL6 + trail 1.5/1.0（无保本）
  L5：SL6 + 保本@0.5%(+0.15%) + trail 1.5/1.0
报每方案的：净均值 / 中位 / 胜率 / 峰值→最终回吐 / 相对 C0 的改善。
样本：近 14 天 mid/long 已平仓（真实入场价 + 1h K 线逐根走）。
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
OUT = ROOT / "data" / "long_ladder_sim.json"

FEE_SIDE = 0.0005
SLIP_SIDE = 0.0005
FUND_8H = 0.0001


def cost_pct(h):
    return (FEE_SIDE + SLIP_SIDE) * 2 * 100 + (h / 8.0) * FUND_8H * 100


def load_trades(days=14):
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


def sim_ladder(s, i, entry, side, *, sl_pct=6.0, stages=(), max_h=168):
    """stages: [(activation_pct, callback_pct)]，按激活阈值升序。
    保本阶段用 callback=0 表示（SL 收到 entry+0.15% 成本缓冲）。
    返回 (net%, hold_h, reason, peak)。"""
    sign = 1.0 if side == "long" else -1.0
    cur_sl = entry * (1 - sign * sl_pct / 100.0)
    peak = 0.0
    for k in range(i, min(i + int(max_h) + 1, len(s))):
        _ts, _o, h, l, c = s[k]
        hold = k - i
        hi_roi = sign * (h - entry) / entry * 100
        lo_roi = sign * (l - entry) / entry * 100
        peak = max(peak, hi_roi)
        # 1) 先判 SL（用本根 low/high）
        if lo_roi <= sign * (cur_sl - entry) / entry * 100:
            roi = sign * (cur_sl - entry) / entry * 100
            return roi - cost_pct(hold), hold, "sl", peak
        # 2) 阶梯收紧（用本根达到的峰值）
        for act, cb in stages:
            if peak >= act:
                lock = peak - cb if cb > 0 else 0.15  # cb=0 → 保本+0.15% 成本缓冲
                new_sl = entry * (1 + sign * lock / 100.0)
                if (side == "long" and new_sl > cur_sl) or (side == "short" and new_sl < cur_sl):
                    cur_sl = new_sl
        if k == min(i + int(max_h), len(s) - 1):
            roi = sign * (c - entry) / entry * 100
            return roi - cost_pct(hold), hold, "timeout", peak
    return None, 0, "no_data", peak


VARIANTS = {
    "C0 现行(SL6+trail3/1.5)": dict(stages=[(3.0, 1.5)]),
    "L1 SL6+保本1.0+trail3/1.5": dict(stages=[(1.0, 0.0), (3.0, 1.5)]),
    "L2 SL6+保本1.0+trail1.5/1.0": dict(stages=[(1.0, 0.0), (1.5, 1.0)]),
    "L3 SL6+保本0.8+trail2/1+trail3/1.5": dict(stages=[(0.8, 0.0), (2.0, 1.0), (3.0, 1.5)]),
    "L4 SL6+trail1.5/1.0(无保本)": dict(stages=[(1.5, 1.0)]),
    "L5 SL6+保本0.5+trail1.5/1.0": dict(stages=[(0.5, 0.0), (1.5, 1.0)]),
    "L6 SL6+保本1.0+trail2/1.0": dict(stages=[(1.0, 0.0), (2.0, 1.0)]),
}


def main() -> int:
    trades = load_trades(14)
    h1 = load_klines({t["symbol"] for t in trades})
    print(f"近 14 天 mid/long 已平仓: {len(trades)} 笔")

    sims = {name: [] for name in VARIANTS}
    actual = []
    rows_meta = []
    for t in trades:
        s = pick(h1, t["symbol"])
        if not s:
            continue
        entry = float(t["entry_price"] or 0)
        if entry <= 0:
            continue
        ts = int(t["opened_at"].timestamp())
        i = next((k for k, row in enumerate(s) if row[0] >= ts), None)
        if i is None:
            continue
        side = str(t["side"] or "long")
        for name, cfg in VARIANTS.items():
            r = sim_ladder(s, i, entry, side, **cfg)
            if r[0] is not None:
                sims[name].append(r)
        close = float(t["close_price"] or 0)
        if close > 0:
            hold_h = (t["closed_at"] - t["opened_at"]).total_seconds() / 3600 if t["closed_at"] else 0
            raw = (close - entry) / entry * 100 if side == "long" else (entry - close) / entry * 100
            actual.append((raw - cost_pct(hold_h), hold_h,
                           str(t["close_reason"] or "?")[:24], float(t["peak_pnl_pct"] or 0) * 100))
        rows_meta.append((t["symbol"], side, str(t["opened_at"])[:16]))

    def stats(rows):
        if not rows:
            return None
        nets = [r[0] for r in rows]
        return {"n": len(rows), "mean": round(sum(nets) / len(nets), 3),
                "median": round(st.median(nets), 3),
                "win": round(sum(1 for x in nets if x > 0) / len(nets), 3),
                "sum": round(sum(nets), 1)}

    print(f"\n{'方案':<38}{'n':>5}{'净均值':>9}{'中位':>8}{'胜率':>7}{'合计':>9}")
    base = stats(sims["C0 现行(SL6+trail3/1.5)"])
    out = {}
    for name, rows in sims.items():
        s = stats(rows)
        out[name] = s
        delta = (s["mean"] - base["mean"]) if (s and base) else 0
        print(f"{name:<38}{s['n']:>5}{s['mean']:>+9.3f}{s['median']:>+8.3f}"
              f"{s['win']:>7.3f}{s['sum']:>+9.1f}   Δ={delta:+.3f}")
    a = stats([(x[0], x[1]) for x in actual])
    print(f"{'实际（含主动出场通道）':<38}{a['n']:>5}{a['mean']:>+9.3f}{a['median']:>+8.3f}"
          f"{a['win']:>7.3f}{a['sum']:>+9.1f}")

    print("\n=== 峰值<3% 子集（现行 trail 不激活的那批）===")
    low_peak_idx = [k for k, m in enumerate(rows_meta)
                    if k < len(actual) and actual[k][3] < 3.0]
    print(f"  n={len(low_peak_idx)}")
    for name, rows in sims.items():
        sub = [rows[k] for k in low_peak_idx if k < len(rows)]
        s = stats(sub)
        if s:
            print(f"  {name:<36} 净均值={s['mean']:>+8.3f}% 中位={s['median']:>+8.3f}% 胜率={s['win']:.3f} 合计={s['sum']:>+8.1f}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n": len(actual),
        "variants": out,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
