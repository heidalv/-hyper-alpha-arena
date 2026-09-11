# -*- coding: utf-8 -*-
"""中长线「全链策略回放」（第十三轮）。

把**当前已落地的新配置**叠加在 99 笔真实 mid/long 入场信号上，测算反事实净期望：

入场闸：
  1. 位置闸：ranging/unknown 下 24h 区间分位 ≥60% 拒多、≤40% 拒空；
     24h 已跌≥5% 拒多、已涨≥5% 拒空（`midlong_location_gate.py` 同口径）。
  2. regime 门：up/chop 只多、down 只空（`midlong_circuit_gate` 同口径）；
  3. 空头：默认 regime_gated → 只保留 down regime 的空头信号。

出场结构（当前 .env）：
  SL 6% / 追踪激活 3% / 回撤 1.5% / 持有上限 168h（与 EXIT_POLICY_MID 对齐）。

成本：taker 5bp + 滑点 5bp 单边（真实往返 ≈ 20bp），资金费按 0.01%/8h。
口径：价格口径净收益（%），与 §4.2 反事实可对比。

用法：.venv\\Scripts\\python.exe backend/scripts/replay_midlong_policy.py
输出：控制台 + `data/midlong_policy_replay.json`
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
OUT = ROOT / "data" / "midlong_policy_replay.json"

FEE_SIDE = 0.0005      # taker 5bp
SLIP_SIDE = 0.0005     # 滑点 5bp
FUND_8H = 0.0001       # 资金费
SL_PCT = 6.0
TRAIL_TRIG = 3.0
TRAIL_GAP = 1.5
MAX_HOLD_H = 168
LOC_MAX_LONG = 60.0
LOC_MIN_SHORT = 40.0
LOC_ADVERSE = 5.0


def parse(v):
    if v is None:
        return {}
    if isinstance(v, dict):
        return v
    try:
        return json.loads(v)
    except Exception:
        try:
            return eval(v)
        except Exception:
            return {}


def cost_pct(h):
    return (FEE_SIDE + SLIP_SIDE) * 2 * 100 + (h / 8.0) * FUND_8H * 100


def load_trades():
    eng = create_engine(ARENA_URL)
    with eng.connect() as c:
        c.execute(text("set app.is_admin='on'"))
        rows = [dict(r._mapping) for r in c.execute(text("""
            select id, symbol, side, entry_price, decision_context, opened_at, strategy_id
            from strategy_trades order by id desc limit 3000
        """)).fetchall()]
    out = []
    for r in rows:
        if str(r["strategy_id"] or "").startswith("e2e_"):
            continue
        d = parse(r["decision_context"])
        if d.get("nature") not in ("swing", "trend_follow", "position"):
            continue
        if not r["opened_at"] or not r["entry_price"]:
            continue
        r["d"] = d
        out.append(r)
    return out


def load_klines(symbols):
    data = defaultdict(list)
    eng = create_engine(MARKET_URL)
    with eng.connect() as c:
        c.execute(text("set statement_timeout='900000'"))
        for exch in ("asterdex", "binance"):
            for s, ts, o, h, l, cl in c.execute(text("""
                select symbol, timestamp, open_price, high_price, low_price, close_price
                from crypto_klines where period='1h' and exchange=:ex and symbol = any(:syms)
                order by symbol, timestamp
            """), {"ex": exch, "syms": list(symbols)}).fetchall():
                data[(exch, s)].append((int(ts), float(o), float(h), float(l), float(cl)))
    # 日线（regime 用）
    daily = defaultdict(list)
    with eng.connect() as c:
        c.execute(text("set statement_timeout='900000'"))
        for exch in ("asterdex", "binance"):
            for s, ts, o, h, l, cl in c.execute(text("""
                select symbol, timestamp, open_price, high_price, low_price, close_price
                from crypto_klines where period='1d' and exchange=:ex and symbol = any(:syms)
                order by symbol, timestamp
            """), {"ex": exch, "syms": list(symbols)}).fetchall():
                daily[(exch, s)].append((int(ts), float(o), float(h), float(l), float(cl)))
    return data, daily


def pick(data, sym):
    for ex in ("asterdex", "binance"):
        v = data.get((ex, sym))
        if v and len(v) > 500:
            return v
    return None


def daily_series(daily, sym):
    for ex in ("asterdex", "binance"):
        v = daily.get((ex, sym))
        if v and len(v) > 60:
            return v
    return None


def regime_at(series, ts, *, sim_ts_list=None):
    """日线 regime（EMA200 + 60 日动量）。sim_ts_list 支持 monkeypatch 测试。"""
    if series is None:
        return "unknown"
    i = None
    for k, row in enumerate(series):
        if row[0] >= ts:
            i = k
            break
    if i is None:
        i = len(series) - 1
    elif i > 0:
        i -= 1  # 用截至该时刻的已收盘日线
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


def location_blocked(series, i, side, regime):
    """位置闸。返回 (blocked, reason)。与 midlong_location_gate 同口径。"""
    if regime not in ("ranging", "unknown"):
        return False, ""
    if i < 24:
        return False, ""
    win = series[max(0, i - 23): i + 1]
    hi = max(r[2] for r in win)
    lo = min(r[3] for r in win)
    entry = series[i][1]
    pos = (entry - lo) / (hi - lo) * 100 if hi > lo else 50.0
    chg24 = (series[i][4] / series[i - 24][4] - 1) * 100 if i >= 24 and series[i - 24][4] > 0 else 0.0
    if side == "long":
        if pos >= LOC_MAX_LONG:
            return True, f"loc_high_{pos:.0f}"
        if chg24 <= -LOC_ADVERSE:
            return True, f"knife_{chg24:.1f}"
    else:
        if pos <= LOC_MIN_SHORT:
            return True, f"loc_low_{pos:.0f}"
        if chg24 >= LOC_ADVERSE:
            return True, f"chase_{chg24:.1f}"
    return False, ""


def sim_exit(series, i, side, sl_pct=SL_PCT, trig=TRAIL_TRIG, gap=TRAIL_GAP, max_h=MAX_HOLD_H):
    """当前出场结构：SL + 追踪 + 时间上限。返回 (毛%, 持有h, exit_kind)。"""
    entry = series[i][1]
    peak = 0.0
    last = i
    for k in range(i, min(i + int(max_h) + 1, len(series))):
        _ts, _o, h, l, c = series[k]
        hold = k - i
        if side == "long":
            mfe = (h - entry) / entry * 100
            sl_hit = l <= entry * (1 - sl_pct / 100)
        else:
            mfe = (entry - l) / entry * 100
            sl_hit = h >= entry * (1 + sl_pct / 100)
        peak = max(peak, mfe)
        if sl_hit:
            return -sl_pct, hold, "sl"
        if peak >= trig:
            trail = peak - gap
            if mfe <= trail:
                return max(trail, 0.0), hold, "trail"
        last = k
        if k == min(i + int(max_h), len(series) - 1):
            break
    sgn = 1 if side == "long" else -1
    return (series[last][4] - entry) / entry * 100 * sgn, last - i, "timeout"


def summarize(name, rows):
    if not rows:
        print(f"  {name}: 无样本")
        return None
    nets = [r["net_pct"] for r in rows]
    wins = sum(1 for x in nets if x > 0)
    kinds = defaultdict(int)
    for r in rows:
        kinds[r["exit"]] += 1
    s = {
        "name": name, "n": len(rows),
        "net_mean": round(sum(nets) / len(nets), 3),
        "net_median": round(st.median(nets), 3),
        "net_sum": round(sum(nets), 1),
        "win_rate": round(wins / len(nets), 3),
        "exits": dict(kinds),
        "hold_h_median": round(st.median([r["hold_h"] for r in rows]), 2),
    }
    print(f"  {name:<40} n={s['n']:>3} 净均值={s['net_mean']:>+7.3f}% 中位={s['net_median']:>+7.3f}% "
          f"胜率={s['win_rate']:.3f} 持有中位={s['hold_h_median']}h 出场={dict(kinds)}")
    return s


def main() -> int:
    trades = load_trades()
    data, daily = load_klines({t["symbol"] for t in trades})
    rows_all, rows_pass = [], []
    block_stats = defaultdict(int)

    for t in trades:
        s = pick(data, t["symbol"])
        if not s:
            continue
        ts = int(t["opened_at"].timestamp())
        i = next((k for k, row in enumerate(s) if row[0] >= ts), None)
        if i is None or i + 2 >= len(s):
            continue
        side = t["side"]
        ds = daily_series(daily, t["symbol"])
        reg = regime_at(ds, ts)
        # regime 门（空头只在 down，多头不在 down）
        if side == "short" and reg != "down":
            block_stats[f"regime_short_{reg}"] += 1
            continue
        if side == "long" and reg == "down":
            block_stats["regime_long_down"] += 1
            continue
        # 位置闸（ranging/unknown）
        blocked, reason = location_blocked(s, i, side, reg)
        if blocked:
            block_stats[reason] += 1
            continue
        g, hold_h, kind = sim_exit(s, i, side)
        net = g - cost_pct(hold_h)
        rows_pass.append({
            "symbol": t["symbol"], "side": side, "regime": reg,
            "gross_pct": round(g, 3), "net_pct": round(net, 3),
            "hold_h": round(hold_h, 2), "exit": kind,
        })

    print(f"真实信号 {len(trades)} 笔；通过当前全部闸门 {len(rows_pass)} 笔")
    print(f"拦截统计: {dict(block_stats)}\n")

    print("=== 当前全链策略（位置闸+regime门+新出场） ===")
    full = summarize("全链策略", rows_pass)
    print("\n=== 对比：只出场（不加入场闸） ===")
    rows_exit_only = []
    for t in trades:
        s = pick(data, t["symbol"])
        if not s:
            continue
        ts = int(t["opened_at"].timestamp())
        i = next((k for k, row in enumerate(s) if row[0] >= ts), None)
        if i is None or i + 2 >= len(s):
            continue
        g, hold_h, kind = sim_exit(s, i, t["side"])
        rows_exit_only.append({
            "gross_pct": round(g, 3), "net_pct": round(g - cost_pct(hold_h), 3),
            "hold_h": round(hold_h, 2), "exit": kind,
        })
    summarize("仅新出场", rows_exit_only)
    print("\n=== 对比：实际历史结果 ===")
    act = [float(t.get("pnl") or 0) for t in trades]
    # strategy_trades 表无 pnl 字段（此回放源只有入场），实际结果用 paper_positions 已在 §1 报告：
    print("  （见 §1：mid 228 笔 -107.22、单笔 -0.47；long 53 笔 -20.36）")

    print("\n=== 按 regime 拆分（通过闸门的） ===")
    for reg in ("up", "chop", "down"):
        rr = [r for r in rows_pass if r["regime"] == reg]
        summarize(f"regime={reg}", rr)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n_trades": len(trades), "n_pass": len(rows_pass),
        "block_stats": dict(block_stats),
        "full_policy": full,
        "rows": rows_pass,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
