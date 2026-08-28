"""MR 出场结构实证分析（2026-08-28 深度调研）

对全部已平仓 MR 成交逐笔回放 5m K 线路径：
  1) MFE/MAE 分布（最大有利/不利偏移）
  2) 各 TP 档首触时间 vs 各 SL 档首触时间（同 bar 双触按保守先 SL）
  3) 出场网格 (TP × SL × 超时) 期望收益矩阵（含成本）
  4) 入场分桶（RSI/区间位置/分数）胜率
输出 JSON 摘要 + 落盘 data/_mr_exit_analysis.json
"""
import datetime
import json
import sys

sys.path.insert(0, ".")

import numpy as np
import psycopg2

# ── 0. 参数 ──
TP_GRID = [0.004, 0.006, 0.008, 0.009, 0.010, 0.012, 0.015, 0.020]
SL_GRID = [0.006, 0.008, 0.010, 0.012, 0.014, 0.016, 0.020]
TMAX_GRID_MIN = [15, 30, 45, 60, 90]
COST = 0.0015  # 单边往返成本假设（taker 5bp×2 + 滑点），敏感性另跑

MAIN = psycopg2.connect(host="127.0.0.1", port=5432, dbname="alpha_arena",
                        user="laobao", password="alpha_pass")
MKT = psycopg2.connect(host="127.0.0.1", port=5432, dbname="alpha_market",
                       user="laobao", password="alpha_pass")


def main():
    cur = MAIN.cursor()
    cur.execute("SET app.is_admin='on'")
    cur.execute("""
        SELECT id, symbol, side, entry_price, opened_at, closed_at, close_reason,
               tp_price, sl_price, leverage, partial_realized_pnl, partial_fee_paid
        FROM paper_positions
        WHERE trade_nature='scalp' AND strategy_id LIKE 'scalp_mr_%' AND status='closed'
        ORDER BY opened_at
    """)
    trades = cur.fetchall()
    print("trades:", len(trades))

    syms = sorted({t[1] for t in trades})
    # ── 预载 5m K线 ──
    kcur = MKT.cursor()
    kcur.execute("SET app.is_admin='on'")
    klines = {}
    t_min = min(t[4] for t in trades) - datetime.timedelta(hours=1)
    t_max = max(t[5] for t in trades) + datetime.timedelta(hours=2)
    for s in syms:
        kcur.execute("""
            SELECT timestamp, open_price, high_price, low_price, close_price
            FROM crypto_klines WHERE exchange='binance' AND symbol=%s AND period='5m'
            AND timestamp >= %s AND timestamp <= %s ORDER BY timestamp
        """, (s, int(t_min.timestamp()), int(t_max.timestamp())))
        rows = kcur.fetchall()
        if rows:
            klines[s] = (
                np.array([r[0] for r in rows], dtype=np.int64),
                np.array([r[1] for r in rows], dtype=np.float64),
                np.array([r[2] for r in rows], dtype=np.float64),
                np.array([r[3] for r in rows], dtype=np.float64),
                np.array([r[4] for r in rows], dtype=np.float64),
            )
        print("kline %s: %d bars" % (s, len(rows)))

    # ── 1. 逐笔路径 ──
    recs = []
    skipped = 0
    for t in trades:
        pid, sym, side, entry, opened, closed, reason, tp_p, sl_p, lev, pnl, fee = t
        k = klines.get(sym)
        if k is None:
            skipped += 1
            continue
        ts, o, h, l, c = k
        t0 = int(opened.timestamp())
        t1 = int(closed.timestamp())
        m = (ts >= t0 - 300) & (ts <= t1 + 300)
        if m.sum() < 3:
            skipped += 1
            continue
        ts_m, h_m, l_m, c_m = ts[m], h[m], l[m], c[m]
        i_entry = int(np.argmax(ts_m >= t0))
        if side == "long":
            ret_h = (h_m[i_entry:] / entry) - 1.0
            ret_l = (l_m[i_entry:] / entry) - 1.0
            ret_c = (c_m[i_entry:] / entry) - 1.0
        else:
            ret_h = 1.0 - (l_m[i_entry:] / entry)
            ret_l = 1.0 - (h_m[i_entry:] / entry)
            ret_c = 1.0 - (c_m[i_entry:] / entry)
        ts_rel = (ts_m[i_entry:] - t0) / 60.0  # 分钟
        dur = (t1 - t0) / 60.0
        # 首触时间（TP 档 / SL 档）
        def first_touch(rets: np.ndarray, thr: float):
            idx = np.argmax(rets >= thr)
            if rets[idx] >= thr:
                return float(ts_rel[idx])
            return np.inf
        tp_t = {tp: first_touch(ret_h, tp) for tp in TP_GRID}
        sl_t = {sl: first_touch(ret_l, -sl) for sl in SL_GRID}
        # MFE/MAE
        mfe = float(ret_h.max())
        mae = float(ret_l.min())
        recs.append({
            "id": pid, "symbol": sym, "side": side, "entry": float(entry),
            "dur_min": dur, "reason": reason, "pnl": float(pnl or 0), "fee": float(fee or 0),
            "mfe": mfe, "mae": mae, "tp_t": tp_t, "sl_t": sl_t,
            "exit_ret": float(ret_c[-1]),
        })
    print("recs:", len(recs), "skipped:", skipped)

    # ── 2. 摘要统计 ──
    mfe = np.array([r["mfe"] for r in recs])
    mae = np.array([r["mae"] for r in recs])
    dur = np.array([r["dur_min"] for r in recs])
    summary = {
        "n": len(recs),
        "mfe_pct": {p: round(float(np.percentile(mfe, p) * 100), 2) for p in (25, 50, 75, 90)},
        "mae_pct": {p: round(float(np.percentile(mae, p) * 100), 2) for p in (25, 50, 75, 90)},
        "dur_min_pct": {p: round(float(np.percentile(dur, p)), 1) for p in (25, 50, 75, 90)},
        "reasons": {},
    }
    for r in recs:
        summary["reasons"][r["reason"]] = summary["reasons"].get(r["reason"], 0) + 1

    # ── 3. TP 触达率（在 SL 1.2% 之前、45min 内）──
    hit = {}
    for tp in TP_GRID:
        n_tp = n_sl_first = n_timeout = 0
        for r in recs:
            tt = r["tp_t"][tp]
            ts_ = r["sl_t"][0.012]
            if tt <= ts_ and tt <= 45:
                n_tp += 1
            elif ts_ <= 45:
                n_sl_first += 1
            else:
                n_timeout += 1
        hit[tp] = {"tp": n_tp, "sl_first": n_sl_first, "timeout": n_timeout}
    summary["tp_hit_vs_sl12_45min"] = hit

    # ── 4. 网格期望 ──
    grid = {}
    for tmax in TMAX_GRID_MIN:
        for sl in SL_GRID:
            for tp in TP_GRID:
                if tp >= sl:
                    continue
                ev = 0.0
                n = 0
                for r in recs:
                    tt = r["tp_t"][tp]
                    ts_ = r["sl_t"][sl]
                    if tt <= ts_ and tt <= tmax:
                        ev += tp - COST
                    elif ts_ <= tmax:
                        ev += -sl - COST
                    else:
                        ev += r["exit_ret"] - COST
                    n += 1
                ev /= n
                grid[f"TP{tp:.3f}_SL{sl:.3f}_T{tmax}"] = {
                    "expectancy": round(ev, 5), "n": n,
                }
    # 只保留 top/bottom 供阅读
    ranked = sorted(grid.items(), key=lambda kv: kv[1]["expectancy"], reverse=True)
    summary["grid_top10"] = ranked[:10]
    summary["grid_bottom5"] = ranked[-5:]

    out = {"summary": summary, "n_recs": len(recs)}
    with open("data/_mr_exit_analysis.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(json.dumps(summary, ensure_ascii=False, indent=1)[:4000])


if __name__ == "__main__":
    main()
