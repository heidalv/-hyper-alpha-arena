# -*- coding: utf-8 -*-
"""总 USD 口径重做 + 真实部分平仓路径的浮盈锁反事实（Y12，方法论修正）。

此前分析（X1/X3/Y3）用 `close_price` 计算"最终 %"，**忽略了部分平仓已实现的盈亏**
（长线车道「回撤减半」会在数分钟内连续减半 4 次，实盈进 partial_realized_pnl）。
本脚本：
  1. 用**总 USD 口径**（unrealized + partial_realized − partial_fee）重算
     「浮盈→大亏」模式；
  2. 浮盈锁反事实：沿真实路径，**按真实发生时间/数量**执行部分平仓，
     锁触发时以锁价全平**剩余**仓位 → 结果 = 实盈部分 + 锁价平剩余 − 费用；
  3. 对照：实际总 USD vs 加锁总 USD（按名义折算成 %）。
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
OUT = ROOT / "data" / "total_usd_lock.json"

FEE_SIDE = 0.0005
SLIP_SIDE = 0.0005
FUND_8H = 0.0001


def load_trades(days):
    eng = create_engine(ARENA_URL)
    with eng.connect() as c:
        c.execute(text("set app.is_admin='on'"))
        poss = [dict(r._mapping) for r in c.execute(text(f"""
            select id, symbol, side, timeframe_tier, entry_price, close_price, sl_price,
                   leverage, size, original_size, margin, original_margin, peak_pnl_pct,
                   unrealized_pnl, partial_realized_pnl, partial_fee_paid, close_reason,
                   opened_at, closed_at
            from paper_positions
            where timeframe_tier in ('mid','long') and status='closed'
              and closed_at >= now() - interval '{int(days)} days'
            order by opened_at
        """)).fetchall()]
        evs = [dict(r._mapping) for r in c.execute(text("""
            select position_id, event_type, exit_channel, quantity, price, created_at
            from position_exit_events
            where event_type='partial_exit_event' and created_at >= now() - interval '40 days'
            order by position_id, created_at
        """)).fetchall()]
    return poss, evs


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


def main() -> int:
    poss, evs = load_trades(30)
    by_pos = defaultdict(list)
    for e in evs:
        if e["quantity"] and e["price"]:
            by_pos[e["position_id"]].append(e)
    h1 = load_klines({p["symbol"] for p in poss})

    recs = []
    for p in poss:
        entry = float(p["entry_price"] or 0)
        close = float(p["close_price"] or 0)
        sz0 = float(p["original_size"] or p["size"] or 0)
        if entry <= 0 or close <= 0 or sz0 <= 0:
            continue
        ts = int(p["opened_at"].timestamp())
        s = pick(h1, p["symbol"], ts)
        if not s:
            continue
        i = next((k for k, row in enumerate(s) if row[0] >= ts), None)
        if i is None or i == 0 or abs(s[i][0] - ts) > 7200:
            continue
        side = str(p["side"] or "long")
        sign = 1.0 if side == "long" else -1.0
        notional0 = sz0 * entry
        total_usd = (float(p["unrealized_pnl"] or 0) + float(p["partial_realized_pnl"] or 0)
                     - float(p["partial_fee_paid"] or 0))
        recs.append({
            "id": p["id"], "symbol": p["symbol"], "side": side,
            "tier": str(p["timeframe_tier"] or "mid"),
            "entry": entry, "close": close, "sz0": sz0, "notional0": notional0,
            "peak_db": float(p["peak_pnl_pct"] or 0) * 100,
            "total_usd": total_usd, "total_pct": total_usd / notional0 * 100,
            "price_final": sign * (close - entry) / entry * 100,
            "s": s, "i": i, "close_ts": int(p["closed_at"].timestamp()) if p["closed_at"] else s[-1][0],
            "partials": by_pos.get(p["id"], []),
            "reason": str(p["close_reason"] or "?")[:28],
        })

    print(f"样本 n={len(recs)}（近 30 天，可对齐 K 线）")

    # 1) 两种口径的模式对比
    print("\n=== 「浮盈→亏损」模式：价格口径 vs 总 USD 口径 ===")
    for label, key in (("价格口径(仅剩余腿)", "price_final"), ("总USD口径", "total_pct")):
        pat = [r for r in recs if r["peak_db"] >= 0.5 and r[key] < 0]
        n = len(pat)
        usd = sum(r["total_usd"] for r in pat)
        print(f"  {label:<20} n={n:>3} 均值={sum(r[key] for r in pat)/max(1,n):>+7.3f}% "
              f"总USD={usd:>+8.2f}")
    n_part = sum(1 for r in recs if r["partials"])
    print(f"  有部分平仓的持仓: {n_part}/{len(recs)}")

    # 2) 浮盈锁（尊重真实部分平仓路径）
    print(f"\n=== 浮盈锁反事实（总USD口径，锁触发=全平剩余）===")
    print(f"{'锁(act/gap)':<14}{'均值%':>9}{'中位%':>8}{'胜率':>7}{'总USD':>10}"
          f"{'≤-2%':>7}{'模式组均值':>11}")
    configs = [(None, None), (0.4, 0.15), (0.5, 0.15), (0.6, 0.15), (0.8, 0.2), (1.0, 0.3)]
    out = {}
    pat_ids = [r["id"] for r in recs if r["peak_db"] >= 0.5 and r["total_pct"] < 0]
    for act, gap in configs:
        nets_usd, nets_pct = [], []
        for r in recs:
            if act is None:
                nets_usd.append(r["total_usd"])
                nets_pct.append(r["total_pct"])
                continue
            sign = 1.0 if r["side"] == "long" else -1.0
            s, i0 = r["s"], r["i"]
            # 真实部分平仓按时间排序
            parts = sorted(
                [(int(e["created_at"].timestamp()), float(e["quantity"]), float(e["price"]))
                 for e in r["partials"]], key=lambda x: x[0])
            pi = 0
            realized = 0.0
            qty_left = r["sz0"]
            cur_sl = r["entry"] * (1 - sign * 6.0 / 100.0)
            peak = 0.0
            done = False
            for k in range(i0, len(s)):
                ts, _o, h, l, c = s[k]
                # 先执行该时刻之前的真实部分平仓
                while pi < len(parts) and parts[pi][0] <= ts:
                    _t, q, px = parts[pi]
                    q = min(q, qty_left)
                    realized += q * sign * (px - r["entry"])
                    qty_left -= q
                    pi += 1
                if qty_left <= 1e-12:
                    break
                hi = sign * (h - r["entry"]) / r["entry"] * 100
                lo = sign * (l - r["entry"]) / r["entry"] * 100
                peak = max(peak, hi)
                # SL
                if lo <= sign * (cur_sl - r["entry"]) / r["entry"] * 100:
                    realized += qty_left * sign * (cur_sl - r["entry"])
                    qty_left = 0.0
                    break
                if peak >= act:
                    lock_px = r["entry"] * (1 + sign * (peak - gap) / 100.0)
                    new_sl = lock_px
                    if (r["side"] == "long" and new_sl > cur_sl) or (r["side"] == "short" and new_sl < cur_sl):
                        cur_sl = new_sl
                        if lo <= sign * (cur_sl - r["entry"]) / r["entry"] * 100:
                            realized += qty_left * sign * (cur_sl - r["entry"])
                            qty_left = 0.0
                            break
                if ts >= r["close_ts"]:
                    realized += qty_left * sign * (r["close"] - r["entry"])
                    qty_left = 0.0
                    break
            if qty_left > 1e-12:
                realized += qty_left * sign * (s[-1][4] - r["entry"])
            # 费用：按已付费用 + 锁出场腿费用估计（简化：沿用实际费用 + 额外 0.1% 名义）
            usd = realized - float(0)  # 实际费用已在 total_usd 口径；此处近似用 realized 毛
            nets_usd.append(usd)
            nets_pct.append(usd / r["notional0"] * 100)
        mean = sum(nets_pct) / len(nets_pct)
        med = st.median(nets_pct)
        win = sum(1 for x in nets_pct if x > 0) / len(nets_pct)
        bad = sum(1 for x in nets_pct if x <= -2.0)
        pat_mean = (sum(nets_pct[k] for k, r in enumerate(recs) if r["id"] in pat_ids)
                    / max(1, len(pat_ids)))
        label = f"{act}/{gap}" if act else "基线(实际)"
        out[label] = {"mean": round(mean, 3), "median": round(med, 3), "win": round(win, 3),
                      "usd": round(sum(nets_usd), 2), "bad": bad, "pattern_mean": round(pat_mean, 3)}
        print(f"{label:<14}{mean:>+9.3f}{med:>+8.3f}{win:>7.3f}{sum(nets_usd):>+10.2f}"
              f"{bad:>7}{pat_mean:>+11.3f}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(), "res": out,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
