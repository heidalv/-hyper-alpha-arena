# -*- coding: utf-8 -*-
"""
反事实出场回放（设计研究，非后端代码）2026-09-29
================================================
问题：mid 车道 60d 实测 301 笔平仓、净盈亏比 0.45（负 Kelly）。
假设：§5.4 的最优障碍出场（σ 定价止损 / 1R 锁本 / r2 放利润 / Chandelier 尾随 / 时间止损）
在本仓真实 15m K 线路径上回放，能否把盈亏比扭到 >=1.5？哪组参数是"区域"而非"单点"？

方法：
- 取账户 14 mid 平仓（近 60 天，add_count=0，size==original_size）
- 每个仓位取 (symbol, exchange) 的 15m K 线 [entry-14d, close+1d]
- 止损距离 R = k * ATR14(1h)（入场时刻的 ATR，用入场前 14 根 1h bar）
- 规则（long 为例，short 镜像）：
    SL   = entry - k*ATR
    TP1  = entry + 1*R ：平 50%，SL 移至 entry（锁本）
    TP2  = entry + r2*R：平 30%，余 20% 跟 Chandelier（最高价 - c*ATR）
    时间止损 τ：入场后 τ 小时既没到 TP1 也没到 SL → 市价平
- 保守约定：同一根 bar 内若 SL 与 TP 都可达，按 SL 先触发计
- 费用：0.05% taker + 0.05% 滑点（每笔成交名义），与实际净额对比时换算口径一致
- 网格：k ∈ {1.0,1.5,2.0}, r2 ∈ {2.0,3.0}, τ ∈ {24,48}，c=2 固定 → 12 组合
  + 基线 A（只 SL 无阶梯）、基线 B（只阶梯无 SL）
"""
import json
import sys
import zoneinfo
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import psycopg2

TZ = zoneinfo.ZoneInfo("Asia/Shanghai")
PG = dict(host="localhost", user="laobao", password="alpha_pass")
EXCH_PREF = ["bybit", "okx", "binance", "hyperliquid", "asterdex"]
FEE_BPS = 5.0          # taker 0.05%
SLIP_BPS = 5.0         # 滑点 0.05%（每笔成交）
CHAND_C = 2.0          # Chandelier 乘数（ATR 单位）


def get_conn(db):
    c = psycopg2.connect(dbname=db, **PG)
    c.set_session(autocommit=True)
    return c


def load_positions():
    q = """
    SET app.is_admin='on';
    SELECT id, symbol, side, entry_price, close_price, size, original_size,
           leverage, margin, opened_at, closed_at, close_reason, exchange,
           coalesce(strategy_id,'') AS strategy_id,
           unrealized_pnl, coalesce(final_fee_paid,0) AS fee,
           peak_unrealized_pnl, trough_unrealized_pnl
    FROM paper_positions
    WHERE account_id=14 AND status='closed' AND timeframe_tier='mid'
      AND closed_at IS NOT NULL AND closed_at >= now() - interval '60 days'
      AND add_count=0 AND size=original_size
    ORDER BY closed_at;
    """
    with get_conn("alpha_arena") as conn:
        df = pd.read_sql(q, conn)
    df["opened_ts"] = df["opened_at"].dt.tz_localize(TZ).astype("int64") // 10**9
    df["closed_ts"] = df["closed_at"].dt.tz_localize(TZ).astype("int64") // 10**9
    return df


def load_klines(symbol, exchange, t0, t1, period="15m"):
    """15m bars for one (symbol,exchange), covering [t0, t1] plus 14d of history."""
    q = """
    SELECT timestamp, open_price, high_price, low_price, close_price
    FROM crypto_klines
    WHERE symbol=%s AND exchange=%s AND period=%s
      AND timestamp >= %s AND timestamp < %s
    ORDER BY timestamp;
    """
    with get_conn("alpha_market") as conn:
        cur = conn.cursor()
        cur.execute(q, (symbol, exchange, period, int(t0), int(t1)))
        rows = cur.fetchall()
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=["ts", "o", "h", "l", "c"]).astype(float)
    df["ts"] = df["ts"].astype("int64")
    return df


def hourly_atr(df, ts):
    """ATR14(1h) 在时刻 ts 的值（用 ts 之前的 1h bar；无足够历史则用 15m ATR56 近似）。"""
    past = df[df.ts < ts]
    if len(past) < 400:
        return None
    h = past.set_index(pd.to_datetime(past.ts, unit="s")).resample("1h").agg(
        {"h": "max", "l": "min", "c": "last"}).dropna()
    if len(h) < 20:
        return None
    tr = pd.concat([h.h - h.l, (h.h - h.c.shift()).abs(), (h.l - h.c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean().iloc[-1]
    return float(atr)


def price_sane(row, bars):
    """校验 kline 价格与 paper 入场价是否同一资产（同名不同币防护）。"""
    after = bars[bars.ts >= row.opened_ts]
    if after.empty:
        return None
    px = float(after.iloc[0].c)
    if px <= 0 or row.entry_price <= 0:
        return None
    ratio = px / row.entry_price
    return ratio if abs(ratio - 1.0) < 0.10 else None


def replay_one(row, bars, k, r2, tau_h, use_sl=True, use_ladder=True, chand_c=CHAND_C):
    """回放单仓。返回 (net_usd, fee_usd, exit_kind, n_fills)。size 方向：long +1 / short -1。"""
    e = row.entry_price
    dirn = 1.0 if row.side == "long" else -1.0
    # 名义：margin×leverage（USD）。size 单位在 BTC 空头行是"张"（×0.001），不可直接乘价。
    notional = (row.margin or 0) * (row.leverage or 0)
    if notional <= 0:
        notional = row.size * e
    t_open, t_close = row.opened_ts, row.closed_ts

    # 入场后的 bar（含入场时刻所在 bar）
    b = bars[(bars.ts >= t_open) & (bars.ts < t_close + 6 * 3600)].reset_index(drop=True)
    if b.empty:
        return None, None, "no_data", 0
    atr_entry = hourly_atr(bars, t_open)
    if atr_entry is None or atr_entry <= 0:
        return None, None, "no_atr", 0
    # [2026-09-29 规格对齐] 与已发布 barrier_ladder 一致：d = clamp(k×ATR/entry, [0.8%, 8%])
    _d_spec = min(max(k * atr_entry / e, 0.008), 0.08)
    R = _d_spec * e
    sl = e - dirn * R
    tp1 = e + dirn * R * 1.0
    tp2 = e + dirn * R * r2 if use_ladder else np.inf
    t_limit = t_open + tau_h * 3600

    pos = 1.0              # 剩余仓位比例
    state = 0               # 0=初始 1=已锁本 2=已到TP2（尾随）
    peak_h = e
    fills = 0
    pnl = 0.0
    fee = 0.0
    exit_kind = "time"

    def do_exit(px, frac, kind):
        nonlocal pos, pnl, fee, fills, exit_kind
        qty = pos * frac
        if qty <= 0:
            return
        pnl += (px - e) / e * dirn * notional * qty
        fee += notional * qty * (FEE_BPS + SLIP_BPS) / 1e4
        pos -= qty
        fills += 1
        exit_kind = kind

    for _, bar in b.iterrows():
        if pos <= 1e-12:
            break
        o, h, l = bar.o, bar.h, bar.l
        if dirn > 0:
            sl_hit = l <= sl
            tp1_hit = h >= tp1 and state == 0 and use_ladder
            tp2_hit = h >= tp2 and state == 1
        else:
            sl_hit = h >= sl
            tp1_hit = l <= tp1 and state == 0 and use_ladder
            tp2_hit = l <= tp2 and state == 1

        if state == 2:
            # Chandelier 尾随
            peak_h = max(peak_h, h) if dirn > 0 else min(peak_h, l)
            trail = peak_h - dirn * chand_c * atr_entry
            if (dirn > 0 and l <= trail) or (dirn < 0 and h >= trail):
                do_exit(trail, 1.0, "chand")
                break
            continue

        # 保守：同 bar 内 SL 优先
        if sl_hit and use_sl:
            do_exit(sl, 1.0, "sl")
            break
        if tp2_hit:
            do_exit(tp2, 0.30, "tp2")
            state = 2
            peak_h = max(peak_h, h) if dirn > 0 else min(peak_h, l)
            continue
        if tp1_hit:
            do_exit(tp1, 0.50, "tp1")
            sl = e          # 锁本
            state = 1
            continue
        if bar.ts >= t_limit and state == 0:
            # 时间止损（在下一根 bar 开盘近似市价）
            px = bars[bars.ts >= bar.ts].iloc[0].o if len(bars[bars.ts >= bar.ts]) else o
            do_exit(px, 1.0, "time")
            break

    if pos > 1e-12:
        # 直到实际平仓时间仍未触发 → 按实际收盘价了结（对照现实）
        do_exit(row.close_price, 1.0, "actual_close")

    return pnl, fee, exit_kind, fills


def btc_momentum_ok(row, btc_1d):
    """BTC 1d 收盘 > 5 根 1d bar 前的收盘（入场时刻的账面 regime 过滤）。"""
    past = btc_1d[btc_1d.ts < row.opened_ts]
    if len(past) < 6:
        return True  # 数据不足不滤
    return past.c.iloc[-1] > past.c.iloc[-6]


def summarize(pnls, fees, kinds):
    pnls = np.array(pnls)
    wins, losses = pnls[pnls > 0], pnls[pnls <= 0]
    return {
        "n": len(pnls),
        "wr_pct": round(100 * len(wins) / max(len(pnls), 1), 1),
        "avg_win": round(float(wins.mean()), 2) if len(wins) else 0.0,
        "avg_loss": round(float(losses.mean()), 2) if len(losses) else 0.0,
        "payoff": round(float(wins.mean() / abs(losses.mean())), 2) if len(wins) and len(losses) and losses.mean() else None,
        "net_total": round(float(pnls.sum()), 2),
        "fees_total": round(float(np.sum(fees)), 2),
        "avg_net": round(float(pnls.mean()), 2),
        "exit_kinds": json.dumps(kinds, ensure_ascii=False),
    }


def run_combo(pos, bars_cache, btc_1d, k, r2, tau, use_sl, use_ladder,
              filters=(), chand_c=CHAND_C):
    pnls, fees, kinds = [], [], {}
    skipped = 0
    for _, r in pos.iterrows():
        if r.symbol not in bars_cache:
            skipped += 1
            continue
        _, bars = bars_cache[r.symbol]
        if price_sane(r, bars) is None:
            skipped += 1
            continue
        ok = True
        for f in filters:
            if f == "F1_btc_up" and not btc_momentum_ok(r, btc_1d):
                ok = False
            elif f == "F2_price_ge_1" and r.entry_price < 1.0:
                ok = False
            elif f == "F3_tpl_range" and not str(r.strategy_id or "").startswith("tpl_mid_range"):
                ok = False
        if not ok:
            continue
        pnl, fee, kind, _ = replay_one(r, bars, k, r2, tau, use_sl, use_ladder, chand_c)
        if pnl is None:
            skipped += 1
            continue
        pnls.append(pnl)
        fees.append(fee)
        kinds[kind] = kinds.get(kind, 0) + 1
    return summarize(pnls, fees, kinds), skipped


def main():
    pos = load_positions()
    print(f"mid positions loaded: {len(pos)}")
    sym_ex = {}
    for _, r in pos.iterrows():
        sym_ex.setdefault(r.symbol, r.exchange or "")
    bars_cache = {}
    t_min = pos.opened_ts.min() - 14 * 86400
    t_max = pos.closed_ts.max() + 86400
    for sym, ex in sym_ex.items():
        got = None
        for cand in ([ex] if ex else []) + EXCH_PREF:
            df = load_klines(sym, cand, t_min, t_max)
            if df is not None and len(df) > 500:
                got = (cand, df)
                break
        if got:
            bars_cache[sym] = got
    print(f"klines cached: {len(bars_cache)}/{len(sym_ex)} symbols")

    # BTC 1d 动量（账面 regime 过滤用）
    btc_1d = load_klines("BTC", "bybit", t_min, t_max, period="1d")

    rows_out = []
    # ---- 实验 1：出场网格（无过滤） ----
    for k, r2, tau in [(1.0, 2.0, 24), (1.5, 2.0, 24), (2.0, 2.0, 24), (1.0, 3.0, 48)]:
        s, _ = run_combo(pos, bars_cache, btc_1d, k, r2, tau, True, True)
        s["combo"] = f"k{k}_r2_{r2}_t{tau}h"
        rows_out.append(s)
    # ---- 实验 2：实际成交（无过滤） ----
    pnls, fees, kinds = [], [], {}
    for _, r in pos.iterrows():
        pnls.append(r.unrealized_pnl - r.fee)
        fees.append(r.fee)
        kinds[str(r.close_reason or "")[:24]] = kinds.get(str(r.close_reason or "")[:24], 0) + 1
    s = summarize(pnls, fees, kinds)
    s["combo"] = "C_actual"
    rows_out.append(s)

    # ---- 实验 3：入场过滤 × 最佳出场（k=1.0, r2=2, t=24） ----
    for fname, flt in [("F1_btc_up", ("F1_btc_up",)),
                       ("F2_price_ge_1", ("F2_price_ge_1",)),
                       ("F3_tpl_range", ("F3_tpl_range",)),
                       ("F1+F2", ("F1_btc_up", "F2_price_ge_1")),
                       ("F1+F2+F3", ("F1_btc_up", "F2_price_ge_1", "F3_tpl_range"))]:
        s, _ = run_combo(pos, bars_cache, btc_1d, 1.0, 2.0, 24, True, True, filters=flt)
        s["combo"] = f"k1.0_r2_2_t24h_{fname}"
        rows_out.append(s)

    out = pd.DataFrame(rows_out)
    print(out.to_string(index=False))
    out.to_csv(r"D:\001Alpha\Hyper-Alpha-Arena\docs\counterfactual_exit_grid_20260929.csv", index=False)

    # ---- 实验 4：分币种（最佳出场规则） ----
    print("\n=== per-symbol (k=1.0 r2=2 t=24) ===")
    per = {}
    for _, r in pos.iterrows():
        if r.symbol not in bars_cache:
            continue
        _, bars = bars_cache[r.symbol]
        if price_sane(r, bars) is None:
            continue
        pnl, fee, kind, _ = replay_one(r, bars, 1.0, 2.0, 24, True, True)
        if pnl is None:
            continue
        d = per.setdefault(r.symbol, {"pnls": [], "kinds": {}})
        d["pnls"].append(pnl)
        d["kinds"][kind] = d["kinds"].get(kind, 0) + 1
    for sym in sorted(per, key=lambda s: -sum(per[s]["pnls"])):
        d = per[sym]
        pn = np.array(d["pnls"])
        w = (pn > 0).sum()
        print(f"{sym:8s} n={len(pn):3d} net={pn.sum():8.2f} wr={100*w/max(len(pn),1):4.1f}% avg={pn.mean():6.2f} kinds={d['kinds']}")
    print("saved docs/counterfactual_exit_grid_20260929.csv")

    # ---- 实验 5：long 车道 ----
    q = """
    SET app.is_admin='on';
    SELECT id, symbol, side, entry_price, close_price, size, original_size,
           leverage, margin, opened_at, closed_at, close_reason, exchange,
           coalesce(strategy_id,'') AS strategy_id,
           unrealized_pnl, coalesce(final_fee_paid,0) AS fee
    FROM paper_positions
    WHERE account_id=14 AND status='closed' AND timeframe_tier='long'
      AND closed_at IS NOT NULL AND closed_at >= now() - interval '60 days'
      AND add_count=0 AND size=original_size
    ORDER BY closed_at;
    """
    with get_conn("alpha_arena") as conn:
        lpos = pd.read_sql(q, conn)
    lpos["opened_ts"] = lpos["opened_at"].dt.tz_localize(TZ).astype("int64") // 10**9
    lpos["closed_ts"] = lpos["closed_at"].dt.tz_localize(TZ).astype("int64") // 10**9
    print(f"\nlong positions loaded: {len(lpos)}")
    lcache = {}
    lt_min = lpos.opened_ts.min() - 14 * 86400
    lt_max = lpos.closed_ts.max() + 86400
    for sym in sorted(set(lpos.symbol)):
        ex = lpos[lpos.symbol == sym].exchange.iloc[0] or ""
        for cand in ([ex] if ex else []) + EXCH_PREF:
            df = load_klines(sym, cand, lt_min, lt_max)
            if df is not None and len(df) > 500:
                lcache[sym] = df
                break
    lbtc = load_klines("BTC", "bybit", lt_min, lt_max, period="1d")
    # actual
    pnls = [(r.unrealized_pnl - r.fee) for _, r in lpos.iterrows()]
    s = summarize(pnls, [r.fee for _, r in lpos.iterrows()], {})
    s["combo"] = "L_actual"
    rows2 = [s]
    for k, r2, tau in [(2.0, 2.0, 720), (2.0, 3.0, 720), (3.0, 3.0, 720), (2.0, 3.0, 168)]:
        pnls, fees, kinds = [], [], {}
        for _, r in lpos.iterrows():
            if r.symbol not in lcache:
                continue
            bars = lcache[r.symbol]
            if price_sane(r, bars) is None:
                continue
            pnl, fee, kind, _ = replay_one(r, bars, k, r2, tau, True, True, chand_c=3.0)
            if pnl is None:
                continue
            pnls.append(pnl)
            fees.append(fee)
            kinds[kind] = kinds.get(kind, 0) + 1
        s = summarize(pnls, fees, kinds)
        s["combo"] = f"L_k{k}_r2_{r2}_t{tau}h"
        rows2.append(s)
    print(pd.DataFrame(rows2).to_string(index=False))


if __name__ == "__main__":
    main()
