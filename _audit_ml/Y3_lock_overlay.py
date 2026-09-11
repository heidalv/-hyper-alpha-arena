# -*- coding: utf-8 -*-
"""浮盈保护「路径叠加」实验（Y3）：在真实成交路径上叠加每 tick 保护。

与 X4/X6 的区别（方法论修正）：
  X4/X6 用**全量机械重放**（所有仓位都跑到 SL/trail/超时），忽略了主动出场通道
  掌握的信号信息，因此低估了现状、高估了机械锁的代价。
  本脚本做**路径叠加**：从真实成交的入场价出发，沿 1h K 线走到**实际平仓时刻**，
  期间若触发保护锁 → 以锁定价成交（更早出场）；若没触发 → 保持实际结果。
  这样每笔的对照是「同一笔交易、同一段行情，加锁 vs 不加锁」。

保护模型（每 tick 评估，等价于 ExitPolicy.evaluate 的 trailing 分支）：
  peak ≥ act 后：lock = peak − gap（只上移）→ 价格回到 lock 即出场；
  另叠加硬 SL（用真实 sl_price，缺失时用 6%）与持有上限。

输出：`data/lock_overlay.json`
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
OUT = ROOT / "data" / "lock_overlay.json"

FEE_SIDE = 0.0005
SLIP_SIDE = 0.0005
FUND_8H = 0.0001


def cost_pct(h):
    return (FEE_SIDE + SLIP_SIDE) * 2 * 100 + (h / 8.0) * FUND_8H * 100


def load_trades(days):
    eng = create_engine(ARENA_URL)
    with eng.connect() as c:
        c.execute(text("set app.is_admin='on'"))
        return [dict(r._mapping) for r in c.execute(text(f"""
            select id, symbol, side, timeframe_tier, entry_price, close_price, sl_price,
                   leverage, original_size, size, peak_pnl_pct,
                   unrealized_pnl, partial_realized_pnl, partial_fee_paid,
                   close_reason, opened_at, closed_at
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
    """选一条**覆盖 ts** 的序列（优先 asterdex）。"""
    for ex in ("asterdex", "binance"):
        v = series.get((ex, sym))
        if v and len(v) > 200 and v[0][0] <= ts <= v[-1][0] + 86400:
            return v
    return None


def simulate(s, i, entry, side, close_ts, act, gap, sl_pct, max_h=168):
    """从入场 bar i 走到实际平仓时刻 close_ts；返回 (净%, 出场原因, 峰值)。"""
    sign = 1.0 if side == "long" else -1.0
    cur_sl = entry * (1 - sign * sl_pct / 100.0)
    peak = 0.0
    locked = False
    for k in range(i, len(s)):
        ts, _o, h, l, c = s[k]
        hold = (ts - s[i][0]) / 3600.0
        if hold > max_h:
            return (sign * (c - entry) / entry * 100 - cost_pct(hold), "timeout", peak)
        hi = sign * (h - entry) / entry * 100
        lo = sign * (l - entry) / entry * 100
        peak = max(peak, hi)
        # 1) 硬 SL 先判（保守）
        if lo <= sign * (cur_sl - entry) / entry * 100:
            roi = sign * (cur_sl - entry) / entry * 100
            return (roi - cost_pct(hold), "sl", peak)
        # 2) 保护锁（每 tick 生效）
        if act and gap is not None and peak >= act:
            lock = peak - gap
            new_sl = entry * (1 + sign * lock / 100.0)
            if (side == "long" and new_sl > cur_sl) or (side == "short" and new_sl < cur_sl):
                cur_sl = new_sl
                locked = True
        # 3) 实际平仓时刻到达 → 保持实际结果（用实际平仓价）
        if ts >= close_ts:
            return (None, "actual", peak)
    return (None, "no_data", peak)


def main() -> int:
    grid = []
    for act in (0.3, 0.4, 0.5, 0.6, 0.8, 1.0, 1.5):
        for gap in (0.15, 0.2, 0.3, 0.5):
            grid.append((act, gap))
    grid.append((None, None))  # 基线：不加锁

    out = {}
    for days in (14, 30):
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
            actual = sign * (close - entry) / entry * 100 - cost_pct(hold_h)
            sl = float(t["sl_price"] or 0)
            sl_pct = abs(entry - sl) / entry * 100 if sl > 0 else 6.0
            sl_pct = min(max(sl_pct, 0.5), 12.0)
            recs.append({
                "s": s, "i": i, "entry": entry, "side": side,
                "close_ts": int(t["closed_at"].timestamp()) if t["closed_at"] else s[-1][0],
                "actual": actual, "peak_db": float(t["peak_pnl_pct"] or 0) * 100,
                "sl_pct": sl_pct, "symbol": t["symbol"],
            })

        print(f"\n=== {days} 天窗口：可模拟 {len(recs)} 笔 ===")
        print(f"{'act/gap':<12}{'均值':>9}{'中位':>8}{'胜率':>7}{'合计':>9}"
              f"{'≤-2%笔数':>9}{'最差':>8}{'模式组均值':>11}{'锁定笔数':>9}")
        res = {}
        pat_idx = [k for k, r in enumerate(recs) if r["peak_db"] >= 0.5 and r["actual"] < 0]
        for act, gap in grid:
            nets, locked_n, worst = [], 0, 0.0
            for r in recs:
                v, why, _pk = simulate(r["s"], r["i"], r["entry"], r["side"], r["close_ts"],
                                       act, gap, r["sl_pct"])
                if v is None:
                    v = r["actual"]
                else:
                    locked_n += 1
                nets.append(v)
                worst = min(worst, v)
            mean = sum(nets) / len(nets)
            med = st.median(nets)
            win = sum(1 for x in nets if x > 0) / len(nets)
            bad = sum(1 for x in nets if x <= -2.0)
            pat_mean = (sum(nets[k] for k in pat_idx) / len(pat_idx)) if pat_idx else 0
            label = f"{act}/{gap}" if act else "基线(无锁)"
            res[label] = {"n": len(nets), "mean": round(mean, 3), "median": round(med, 3),
                          "win": round(win, 3), "sum": round(sum(nets), 1),
                          "bad_le_-2": bad, "worst": round(worst, 2),
                          "pattern_mean": round(pat_mean, 3), "locked_n": locked_n}
            print(f"{label:<12}{mean:>+9.3f}{med:>+8.3f}{win:>7.3f}{sum(nets):>+9.1f}"
                  f"{bad:>9}{worst:>+8.2f}{pat_mean:>+11.3f}{locked_n:>9}")
        out[f"{days}d"] = res

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(), "grid": out,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
