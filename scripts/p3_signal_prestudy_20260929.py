# -*- coding: utf-8 -*-
"""
P3 造血信号实证预研 2026-09-29（设计研究，非后端代码）
=====================================================
问题：§5.6 证明"出场手术 + 入场过滤"只能止血（−82%），翻正要靠入场边际。
本脚本 = §5.3 三重检验的雏形：
  对 mid 60d 每笔入场打 11 个特征快照（funding z / OI 变化 / 多空比 / taker 流 /
  清算脉冲 / BTC 动量 / 自身动量 / 区间位置 / ATR），
  检验它们对入场后 1h/4h/24h 前向收益的条件期望；
  再做 OOS 方向验证（train 42d / test 18d）、成本折减、特征正交性；
  最后把"边际分数"接入 §5.6 的出场回放，回答：高边际子集能否转正（P1+P3 联合门禁原型）。
"""
import json
import sys
import zoneinfo
from datetime import datetime

import numpy as np
import pandas as pd
import psycopg2

sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena\scripts")
import counterfactual_exit_replay_20260929 as R

TZ = R.TZ
PG = R.PG
EXCH_PREF = R.EXCH_PREF
load_klines = R.load_klines
price_sane = R.price_sane
replay_one = R.replay_one

COST_BPS = 15.0   # 往返成本折减（0.05% taker×2 + 0.05% 滑点）用于 24h 前向收益的净化


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
           unrealized_pnl, coalesce(final_fee_paid,0) AS fee
    FROM paper_positions
    WHERE account_id=14 AND status='closed' AND timeframe_tier='mid'
      AND closed_at IS NOT NULL AND closed_at >= now() - interval '60 days'
    ORDER BY closed_at;
    """
    with get_conn("alpha_arena") as conn:
        df = pd.read_sql(q, conn)
    df["opened_ts"] = df["opened_at"].dt.tz_localize(TZ).astype("int64") // 10**9
    df["closed_ts"] = df["closed_at"].dt.tz_localize(TZ).astype("int64") // 10**9
    return df


def fetch_series(table, symbol, exchange, t0, t1, cols, agg="sum"):
    """拉取单币市场特征序列。cols: [(col, alias), ...]，首列为时间戳。
    agg='sum' 时按小时聚合（时间戳列换为小时桶），输出列 ts_ms + 各聚合列。"""
    t_col = cols[0][0]
    if agg:
        sel = f"floor({t_col}/3600000)*3600000 AS ts_ms"
        sel += ", " + ", ".join(f"SUM({c}) AS {a}" for c, a in cols[1:])
        q = f"""
        SELECT {sel} FROM {table}
        WHERE symbol=%s AND exchange=%s AND {t_col} >= %s AND {t_col} < %s
        GROUP BY 1 ORDER BY 1;
        """
    else:
        sel = ", ".join(f"{c} AS {a}" for c, a in cols)
        q = f"""
        SELECT {sel} FROM {table}
        WHERE symbol=%s AND exchange=%s AND {t_col} >= %s AND {t_col} < %s
        ORDER BY {t_col};
        """
    with get_conn("alpha_market") as conn:
        df = pd.read_sql(q, conn, params=(symbol, exchange, int(t0 * 1000), int(t1 * 1000)))
    return df


def asof(sr_ts, sr_val, q_ts):
    """向量化 asof 取值。sr_ts 升序秒。返回与 q_ts 等长数组（无值→NaN）。"""
    idx = np.searchsorted(sr_ts, q_ts, side="right") - 1
    out = np.full(len(q_ts), np.nan)
    ok = idx >= 0
    out[ok] = sr_val[np.clip(idx[ok], 0, len(sr_val) - 1)]
    return out


def build_features(pos, bars_cache, t0, t1):
    """返回 pos + 特征矩阵 + 前向收益矩阵。"""
    pos = pos.copy()
    F = {}
    for sym in sorted(set(pos.symbol)):
        ex = bars_cache[sym][0] if sym in bars_cache else None
        if ex is None:
            continue
        bars = bars_cache[sym][1]
        idx = pos.symbol == sym
        ts = pos.opened_ts[idx].values
        price = pos.entry_price[idx].values

        # 前向收益（价格路径）
        fwd = {h: [] for h in (1, 4, 24)}
        for t, px in zip(ts, price):
            after = bars[bars.ts >= t]
            for h in (1, 4, 24):
                need = t + h * 3600
                hit = after[after.ts >= need]
                fwd[h].append(float(hit.iloc[0].c / px - 1) if len(hit) else np.nan)
        for h in (1, 4, 24):
            F[(sym, f"fwd_{h}h")] = pd.Series(fwd[h], index=pos.index[idx])

        # 价格类特征（klines）
        # ret_24h
        vals = []
        for t in ts:
            past = bars[(bars.ts < t) & (bars.ts >= t - 86400)]
            vals.append(float(past.iloc[-1].c / past.iloc[0].c - 1) if len(past) > 10 else np.nan)
        F[(sym, "ret_24h")] = pd.Series(vals, index=pos.index[idx])
        # pos_in_range20d
        vals = []
        for t, px in zip(ts, price):
            past = bars[(bars.ts < t) & (bars.ts >= t - 20 * 86400)]
            if len(past) < 50:
                vals.append(np.nan)
                continue
            hi, lo = past.h.max(), past.l.min()
            vals.append((px - lo) / (hi - lo) if hi > lo else np.nan)
        F[(sym, "pos_range20d")] = pd.Series(vals, index=pos.index[idx])
        # atr1h_pct
        vals = []
        for t, px in zip(ts, price):
            atr = R.hourly_atr(bars, t)
            vals.append(atr / px if atr else np.nan)
        F[(sym, "atr1h_pct")] = pd.Series(vals, index=pos.index[idx])

        # funding：8h 结算序列，7d 滚动 z + 原始值
        try:
            fr = fetch_series("perp_funding", sym, ex, t0, t1, [("timestamp", "ts_ms"), ("funding_rate", "funding_rate")])
            fr["ts"] = fr.ts_ms // 1000
            fr = fr.sort_values("ts")
            z = (fr.funding_rate - fr.funding_rate.rolling(21).mean()) / fr.funding_rate.rolling(21).std()
            F[(sym, "funding_z7d")] = pd.Series(asof(fr.ts.values, z.values, ts), index=pos.index[idx])
            F[(sym, "funding_level")] = pd.Series(asof(fr.ts.values, fr.funding_rate.values, ts), index=pos.index[idx])
        except Exception as e:
            print("funding fail", sym, e)

        # position_structure：OI 24h 变化、多空比、taker 买卖比（该表仅 binance）
        try:
            ps = fetch_series("position_structure", sym, "binance", t0, t1,
                              [("ts_ms", "ts_ms"), ("open_interest_value", "open_interest_value"),
                               ("global_ls_ratio", "global_ls_ratio"), ("taker_buy_sell_ratio", "taker_buy_sell_ratio")])
            ps = ps.drop_duplicates("ts_ms").sort_values("ts_ms")
            ps["ts"] = ps.ts_ms // 1000
            oi = asof(ps.ts.values, ps.open_interest_value.values, ts)
            oi24 = asof(ps.ts.values, ps.open_interest_value.values, ts - 86400)
            F[(sym, "oi_chg24_pct")] = pd.Series((oi / oi24 - 1) * 100, index=pos.index[idx])
            F[(sym, "ls_ratio")] = pd.Series(asof(ps.ts.values, ps.global_ls_ratio.values, ts), index=pos.index[idx])
            F[(sym, "taker_bs_ratio")] = pd.Series(asof(ps.ts.values, ps.taker_buy_sell_ratio.values, ts), index=pos.index[idx])
        except Exception as e:
            print("pos_structure fail", sym, e)

        # market_trades_aggregated：1h taker 不平衡（小时聚合；binance→hyperliquid→asterdex）
        try:
            tr = None
            for tex in ("binance", "hyperliquid", "asterdex"):
                tr = fetch_series("market_trades_aggregated", sym, tex, t0, t1,
                                  [("timestamp", "ts_ms"), ("taker_buy_notional", "taker_buy_notional"),
                                   ("taker_sell_notional", "taker_sell_notional")], agg="sum")
                if len(tr) > 0:
                    break
            tr = tr.drop_duplicates("ts_ms").sort_values("ts_ms")
            tr["ts"] = tr.ts_ms // 1000
            imb = (tr.taker_buy_notional - tr.taker_sell_notional) / (tr.taker_buy_notional + tr.taker_sell_notional)
            F[(sym, "taker_imb_1h")] = pd.Series(asof(tr.ts.values, imb.values, ts), index=pos.index[idx])
        except Exception as e:
            print("trades fail", sym, e)

        # liquidation_events：4h 多空清算脉冲
        try:
            li = fetch_series("liquidation_events", sym, ex, t0, t1,
                              [("ts_ms", "ts_ms"), ("long_usd", "long_usd"), ("short_usd", "short_usd")], agg="sum")
            li = li.sort_values("ts_ms")
            li["ts"] = li.ts_ms // 1000
            vals = []
            for t in ts:
                w = li[(li.ts < t) & (li.ts >= t - 4 * 3600)]
                if len(w) == 0 or (w.long_usd.sum() + w.short_usd.sum()) <= 0:
                    vals.append(np.nan)
                else:
                    vals.append((w.long_usd.sum() - w.short_usd.sum()) / (w.long_usd.sum() + w.short_usd.sum()))
            F[(sym, "liq_imb_4h")] = pd.Series(vals, index=pos.index[idx])
        except Exception as e:
            print("liq fail", sym, e)

    # BTC 1d 动量
    btc = load_klines("BTC", "bybit", t0, t1, period="1d")
    btc_ts = btc.ts.values
    btc_c = btc.c.values
    vals = []
    for _, r in pos.iterrows():
        i = np.searchsorted(btc_ts, r.opened_ts, side="right") - 1
        vals.append(btc_c[i] / btc_c[i - 5] - 1 if i >= 5 else np.nan)
    F[("GLOBAL", "btc_1d_mom")] = pd.Series(vals, index=pos.index)

    Feat = pd.DataFrame(index=pos.index)
    for k, v in F.items():
        if k[0] == "GLOBAL":
            Feat[k[1]] = v
        elif isinstance(v, pd.Series):
            Feat.loc[v.index, k[1]] = v.values
    return pos, Feat


def tercile_stats(df, feat, target="fwd_24h", train_mask=None):
    z = (df[feat] - df[feat].mean()) / df[feat].std()
    q1, q2 = df[feat].quantile([1 / 3, 2 / 3])
    grp = np.where(df[feat] <= q1, 0, np.where(df[feat] <= q2, 1, 2))
    rows = []
    for g, lab in [(0, "低"), (1, "中"), (2, "高")]:
        m = (grp == g)
        if train_mask is not None:
            m = m & train_mask
        v = df[target][m].dropna()
        rows.append((lab, len(v), v.mean(), v.std() / np.sqrt(max(len(v), 1))))
    lo, hi = rows[0], rows[2]
    diff = hi[2] - lo[2]
    se = np.sqrt(hi[3] ** 2 + lo[3] ** 2)
    return rows, diff / se if se > 0 else np.nan


def main():
    pos = load_positions()
    print(f"positions: {len(pos)}")
    sym_ex = {}
    for _, r in pos.iterrows():
        sym_ex.setdefault(r.symbol, r.exchange or "")
    bars_cache = {}
    t_min = pos.opened_ts.min() - 30 * 86400
    t_max = pos.closed_ts.max() + 86400
    for sym, ex in sym_ex.items():
        for cand in ([ex] if ex else []) + EXCH_PREF:
            df = load_klines(sym, cand, t_min, t_max)
            if df is not None and len(df) > 500:
                bars_cache[sym] = (cand, df)
                break
    print(f"klines cached: {len(bars_cache)}/{len(sym_ex)}")

    pos, Feat = build_features(pos, bars_cache, t_min, t_max)
    df = pd.concat([pos[["id", "symbol", "side", "opened_ts", "closed_ts", "entry_price",
                         "unrealized_pnl", "fee", "margin", "leverage"]], Feat], axis=1)
    df["net"] = df.unrealized_pnl - df.fee
    df["notional"] = (df.margin * df.leverage).where(df.margin * df.leverage > 0, df.entry_price * 1.0)
    df["ret_actual"] = df.net / df.notional * 100
    df["fwd_24h_pct"] = df.fwd_24h * 100
    df["fwd_24h_net_pct"] = df.fwd_24h_pct - COST_BPS / 100

    train = df.opened_ts < df.opened_ts.quantile(0.7)
    feats = ["funding_z7d", "funding_level", "oi_chg24_pct", "ls_ratio", "taker_bs_ratio",
             "taker_imb_1h", "liq_imb_4h", "btc_1d_mom", "ret_24h", "pos_range20d", "atr1h_pct"]

    print(f"\n=== 特征覆盖率（n={len(df)}）===")
    for f in feats:
        print(f"{f:16s} n={df[f].notna().sum():4d}")

    print("\n=== 单特征 × fwd_24h(%)：分位均值 + t(top-low)  [train n≈70%] ===")
    print(f"{'feature':16s} {'n_train':>7s} {'低':>8s} {'中':>8s} {'高':>8s} {'t':>6s} {'corr':>6s}")
    results = {}
    for f in feats:
        d = df[[f, "fwd_24h_pct"]].dropna()
        if len(d) < 40:
            print(f"{f:16s} insufficient n={len(d)}")
            continue
        rows, tval = tercile_stats(d, f, "fwd_24h_pct")
        tr = d[d.index.isin(df.index[train])]
        rows_tr, t_tr = tercile_stats(tr, f, "fwd_24h_pct")
        corr = np.corrcoef(d[f], d.fwd_24h_pct)[0, 1]
        print(f"{f:16s} {len(tr):7d} {rows_tr[0][2]:8.2f} {rows_tr[1][2]:8.2f} {rows_tr[2][2]:8.2f} {t_tr:6.2f} {corr:6.2f}")
        results[f] = dict(rows=rows_tr, t=t_tr, corr=corr)

    # OOS 方向验证：train 上 high>low 的特征，在 test 上是否同向
    print("\n=== OOS 方向验证（train 选方向 → test 复核）===")
    promoted = []
    for f in feats:
        if f not in results:
            continue
        r = results[f]
        if np.isnan(r["t"]) or abs(r["t"]) < 1.0:
            continue
        d_tr = df[df.index.isin(df.index[train])][[f, "fwd_24h_pct"]].dropna()
        d_te = df[df.index.isin(df.index[~train])][[f, "fwd_24h_pct"]].dropna()
        if len(d_te) < 20:
            continue
        rows_tr, _ = tercile_stats(d_tr, f, "fwd_24h_pct")
        rows_te, t_te = tercile_stats(d_te, f, "fwd_24h_pct")
        same_dir = (rows_tr[2][2] - rows_tr[0][2]) * (rows_te[2][2] - rows_te[0][2]) > 0
        print(f"{f:16s} train hi-lo={rows_tr[2][2]-rows_tr[0][2]:+.2f}  test hi-lo={rows_te[2][2]-rows_te[0][2]:+.2f}  t_te={t_te:+.2f}  {'✓同向' if same_dir else '✗反向'}")
        if same_dir and abs(t_te) >= 0.5:
            promoted.append(f)

    print(f"\npromoted features: {promoted}")

    # 正交性（|corr| < 0.6）
    if len(promoted) >= 2:
        C = df[promoted].corr()
        print("\n=== 特征相关矩阵 ===")
        print(C.round(2).to_string())

    # 边际分数 m = Σ z（promoted，等权，方向取 train 上 high-low 符号）
    df["m"] = np.nan
    zsum = pd.Series(0.0, index=df.index)
    for f in promoted:
        d = df[[f, "fwd_24h_pct"]].dropna()
        rows_tr, _ = tercile_stats(d[d.index.isin(df.index[train])], f, "fwd_24h_pct")
        sign = 1.0 if rows_tr[2][2] > rows_tr[0][2] else -1.0
        z = (df[f] - df[f].mean()) / df[f].std()
        zsum = zsum.add(sign * z, fill_value=0.0)
    df["m"] = zsum / np.sqrt(len(promoted))

    m1, m2 = df.m.quantile([1 / 3, 2 / 3])
    grp = np.where(df.m <= m1, 0, np.where(df.m <= m2, 1, 2))
    print("\n=== 边际分数 m 分桶 × 24h 前向（净成本 15bp）===")
    for g, lab in [(0, "低"), (1, "中"), (2, "高")]:
        v = df.fwd_24h_net_pct[(grp == g)]
        va = df.ret_actual[(grp == g)]
        print(f"m {lab}: n={len(v):3d}  fwd24h_net={v.mean():+.2f}%  ret_actual={va.mean():+.2f}%")

    # 关键问题：高 m 子集在 §5.6 出场回放下能否转正
    print("\n=== P1+P3 联合：高 m 子集 × 最优障碍回放 (k=1.0 r2=2 t=24) ===")
    for g, lab in [(0, "低m"), (2, "高m")]:
        pnls, fees = [], []
        for _, r in pos.iterrows():
            if r.symbol not in bars_cache:
                continue
            _, bars = bars_cache[r.symbol]
            if price_sane(r, bars) is None:
                continue
            if df.loc[r.name, "m"] != df.loc[r.name, "m"]:
                continue
            gv = 0 if df.loc[r.name, "m"] <= m1 else (2 if df.loc[r.name, "m"] > m2 else 1)
            if gv != g:
                continue
            pnl, fee, _, _ = replay_one(r, bars, 1.0, 2.0, 24, True, True)
            if pnl is None:
                continue
            pnls.append(pnl)
            fees.append(fee)
        pnls = np.array(pnls)
        w = (pnls > 0).sum()
        print(f"{lab}: n={len(pnls):3d} 净={pnls.sum():+.2f} 每笔={pnls.mean():+.2f} WR={100*w/max(len(pnls),1):.1f}% 费={np.sum(fees):.1f}")

    df.to_csv(r"D:\001Alpha\Hyper-Alpha-Arena\docs\p3_signal_prestudy_features_20260929.csv", index=False)
    print("saved docs/p3_signal_prestudy_features_20260929.csv")


if __name__ == "__main__":
    main()
