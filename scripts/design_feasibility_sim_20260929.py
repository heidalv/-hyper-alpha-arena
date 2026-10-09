# -*- coding: utf-8 -*-
"""
整体设计可行性测试 2026-09-29（设计研究，非后端代码）
=====================================================
把《底层数学模型设计》组装成一台信号驱动的模拟交易机，在本仓真实行情上做端到端测试：
  P3 入场：m 分数（btc_1d_mom z + funding_z7d）> 0.29 且价格≥$1；
           方向配对：z_btc > +0.5 → long；z_btc < −0.5 → short（short 侧 m 只含 |z_btc|，保守）
  P1 出场：SL=k·ATR(1h)（k=1.0，距离钳制 [0.8%, 8%]）→ TP1=1R 平50%+锁本 → TP2=2R 平30%
           → 余20% Chandelier(2×ATR) → τ=24h 时间止损；同 bar SL 优先（保守）
  P2 风控：最多 6 仓并发 / 同向最多 4 仓 / 日亏 −1.5% 权益熔断 24h
  仓位：风险预算 0.5% 权益/笔，notional=risk/SL距离，杠杆≤4，单仓保证金≤8% 权益
  成本：0.05% taker + 0.05% 滑点（按成交名义）；资金费按每 8h 结算点实际费率（asof）
窗口：A=2026-08-30→09-29（参数拟合期=样本内）；B=2026-07-01→08-30（参数冻结=样本外）
消融：full / 无m过滤 / 无价格过滤 / 无出场阶梯（SL+TP1直平）/ 无账面风控
"""
import sys
import zoneinfo
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import psycopg2

sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena\scripts")
import counterfactual_exit_replay_20260929 as R

TZ = R.TZ
load_klines = R.load_klines
PG = R.PG
EXCH_PREF = R.EXCH_PREF

M_TH = 0.29        # m 分数门槛（P3 预研）
Z_BTC_TH = 0.5     # BTC 动量 z 门槛（方向配对）
K_ATR = 1.0        # SL = k×ATR(1h)
SL_MIN, SL_MAX = 0.008, 0.08
R1, R2 = 1.0, 2.0
TAU_H = 24.0
CHAND = 2.0
RISK_PCT = 0.005
MAX_POS = 6
MAX_SAME_DIR = 4
DAILY_LOSS_HALT = 0.015
LEV_CAP = 4.0
MARGIN_CAP_EQ = 0.08
FEE_BPS, SLIP_BPS = 5.0, 5.0
SYMBOLS = ["BTC", "ETH", "SOL", "BNB", "XRP", "UNI", "VIRTUAL", "ASTER", "XPL", "ATOM", "AAVE", "DOT", "LINK"]


def get_conn(db):
    c = psycopg2.connect(dbname=db, **PG)
    c.set_session(autocommit=True)
    return c


def load_funding(sym, exchange, t0, t1):
    q = """SELECT timestamp, funding_rate FROM perp_funding
           WHERE symbol=%s AND exchange=%s AND timestamp>=%s AND timestamp<%s ORDER BY timestamp"""
    with get_conn("alpha_market") as conn:
        df = pd.read_sql(q, conn, params=(sym, exchange, int(t0 * 1000), int(t1 * 1000)))
    if df.empty:
        return None
    df["ts"] = (df.timestamp / 1000).astype("int64")
    df = df.sort_values("ts").drop_duplicates("ts")
    return df


def prep_market(t0, t1):
    """15m bars per symbol + funding + BTC 1d。返回 dict。"""
    mk = {}
    for sym in SYMBOLS:
        got = None
        for ex in ("bybit", "okx", "binance", "hyperliquid", "asterdex"):
            df = load_klines(sym, ex, t0 - 30 * 86400, t1 + 86400)
            if df is not None and len(df) > 800:
                got = (ex, df)
                break
        if got is None:
            continue
        ex, bars = got
        fr = load_funding(sym, ex, t0 - 30 * 86400, t1 + 86400)
        if fr is None:
            # 换交易所找 funding
            for ex2 in ("binance", "okx", "bybit", "hyperliquid", "asterdex"):
                fr = load_funding(sym, ex2, t0 - 30 * 86400, t1 + 86400)
                if fr is not None:
                    break
        mk[sym] = dict(exchange=ex, bars=bars, funding=fr)
    btc_1d = load_klines("BTC", "bybit", t0 - 40 * 86400, t1 + 86400, period="1d")
    return mk, btc_1d


def z_series(s):
    return (s - s.rolling(21, min_periods=5).mean()) / s.rolling(21, min_periods=5).std()


def z_at(series, ts):
    """ts 时刻（不含）的 z 值。series: DataFrame(ts, z)。"""
    i = np.searchsorted(series.ts.values, ts, side="right") - 1
    return series.z.iloc[i] if i >= 0 else np.nan


def signals(mk, btc_1d, t, apply_m=True, apply_price=True, apply_dir=True):
    """t 时刻各币信号。返回 list[(sym, dirn)]。"""
    z_btc = np.nan
    i = np.searchsorted(btc_1d.ts.values, t, side="right") - 1
    if i >= 5:
        ret = btc_1d.c.iloc[i] / btc_1d.c.iloc[i - 5] - 1
        hist = btc_1d.c.iloc[max(0, i - 40):i].pct_change().dropna()
        z_btc = (ret - hist.mean()) / hist.std() if len(hist) > 10 and hist.std() > 0 else np.nan
    if z_btc != z_btc:
        return []
    out = []
    for sym, d in mk.items():
        bars = d["bars"]
        j = np.searchsorted(bars.ts.values, t, side="right") - 1
        if j < 0:
            continue
        px = bars.c.iloc[j]
        if apply_price and px < 1.0:
            continue
        zf = np.nan
        if d["funding"] is not None:
            fr = d["funding"]
            frz = z_series(fr.funding_rate)
            zf = z_at(pd.DataFrame({"ts": fr.ts, "z": frz}), t)
        if apply_m and zf != zf:
            continue
        m_long = (z_btc + zf) / np.sqrt(2) if zf == zf else z_btc
        m_short = abs(z_btc)  # short 侧保守：funding 方向未验证
        if apply_dir:
            if z_btc > Z_BTC_TH and m_long > M_TH:
                out.append((sym, +1))
            elif z_btc < -Z_BTC_TH and m_short > M_TH:
                out.append((sym, -1))
        else:
            if m_long > M_TH:
                out.append((sym, +1))
    return out


def hourly_atr(bars, t):
    return R.hourly_atr(bars, t)


class Pos:
    __slots__ = ("sym", "dirn", "entry", "notional", "sl", "tp1", "tp2", "trail_peak",
                 "t_open", "qty", "state", "margin", "cum_pnl", "cum_fee", "n_fills", "reason")

    def __init__(self, sym, dirn, entry, notional, atr):
        self.sym, self.dirn, self.entry, self.notional = sym, dirn, entry, notional
        d = min(max(K_ATR * atr / entry, SL_MIN), SL_MAX)
        self.sl = entry * (1 - dirn * d)
        self.tp1 = entry * (1 + dirn * d * R1)
        self.tp2 = entry * (1 + dirn * d * R2)
        self.trail_peak = entry
        self.t_open = None
        self.qty = 1.0
        self.state = 0
        self.margin = notional / LEV_CAP
        self.cum_pnl = 0.0
        self.cum_fee = 0.0
        self.n_fills = 0
        self.reason = "open"


def fill(p, px, frac, kind):
    p.cum_pnl += (px - p.entry) / p.entry * p.dirn * p.notional * frac
    p.cum_fee += p.notional * frac * (FEE_BPS + SLIP_BPS) / 1e4
    p.qty -= frac
    p.n_fills += 1
    p.reason = kind


def step_positions(mk, positions, t, equity, trades, use_ladder=True):
    """推进所有持仓一 bar。t = 当前 bar 的 ts（bar 区间 [t, t+15m)）。返回 (pnl, fee)。"""
    pnl, fee = 0.0, 0.0
    closed = []
    for p in positions:
        d = mk[p.sym]
        bars = d["bars"]
        j = np.searchsorted(bars.ts.values, t, side="right") - 1
        if j < 0 or j >= len(bars) - 1:
            continue
        bar = bars.iloc[j]
        if p.state == 2:
            p.trail_peak = max(p.trail_peak, bar.h) if p.dirn > 0 else min(p.trail_peak, bar.l)
            atr0 = abs(p.entry - p.sl) / K_ATR
            trail = p.trail_peak - p.dirn * CHAND * atr0
            hit = bar.l <= trail if p.dirn > 0 else bar.h >= trail
            if hit:
                q = p.qty
                fill(p, trail, q, "chand")
                pnl += (trail - p.entry) / p.entry * p.dirn * p.notional * q
                fee += p.notional * q * (FEE_BPS + SLIP_BPS) / 1e4
                closed.append(p)
            continue
        sl_hit = bar.l <= p.sl if p.dirn > 0 else bar.h >= p.sl
        if use_ladder:
            tp2_hit = (bar.h >= p.tp2 and p.state == 1) if p.dirn > 0 else (bar.l <= p.tp2 and p.state == 1)
            tp1_hit = (bar.h >= p.tp1 and p.state == 0) if p.dirn > 0 else (bar.l <= p.tp1 and p.state == 0)
        else:
            # 无阶梯：TP1 直平全部（1R 全平，无锁本、无 TP2、无尾随）
            tp2_hit = False
            tp1_hit = (bar.h >= p.tp1) if p.dirn > 0 else (bar.l <= p.tp1)
        if sl_hit:
            q = p.qty
            fill(p, p.sl, q, "sl")
            pnl += (p.sl - p.entry) / p.entry * p.dirn * p.notional * q
            fee += p.notional * q * (FEE_BPS + SLIP_BPS) / 1e4
            closed.append(p)
        elif tp2_hit:
            fill(p, p.tp2, 0.30, "tp2")
            pnl += (p.tp2 - p.entry) / p.entry * p.dirn * p.notional * 0.30
            fee += p.notional * 0.30 * (FEE_BPS + SLIP_BPS) / 1e4
            p.state = 2
            p.trail_peak = max(p.trail_peak, bar.h) if p.dirn > 0 else min(p.trail_peak, bar.l)
        elif tp1_hit:
            frac = 0.50 if use_ladder else p.qty
            fill(p, p.tp1, frac, "tp1")
            pnl += (p.tp1 - p.entry) / p.entry * p.dirn * p.notional * frac
            fee += p.notional * frac * (FEE_BPS + SLIP_BPS) / 1e4
            if use_ladder:
                p.sl = p.entry
                p.state = 1
            else:
                closed.append(p)
        elif (t - p.t_open) >= TAU_H * 3600 and p.state == 0:
            q = p.qty
            fill(p, bar.o, q, "time")
            pnl += (bar.o - p.entry) / p.entry * p.dirn * p.notional * q
            fee += p.notional * q * (FEE_BPS + SLIP_BPS) / 1e4
            closed.append(p)
    for p in closed:
        trades.append(p)
        positions.remove(p)
    return pnl, fee


def apply_funding(mk, positions, t):
    """8h 结算：按最近一次费率计。返回 funding pnl。"""
    out = 0.0
    for p in positions:
        d = mk[p.sym]
        if d["funding"] is None:
            continue
        fr = d["funding"]
        i = np.searchsorted(fr.ts.values, t, side="right") - 1
        if i < 0:
            continue
        rate = float(fr.funding_rate.iloc[i])
        out += -p.dirn * rate * p.notional * p.qty  # 正费率：long 付、short 收
    return out


def simulate(mk, btc_1d, t0, t1, apply_m=True, apply_price=True, apply_dir=True,
             use_ladder=True, use_guard=True, label=""):
    equity = 4511.89
    start_eq = equity
    positions, trades = [], []
    eq_curve, day_pnl, day = [], 0.0, None
    halt_until = 0
    n_signals = 0
    t = t0
    while t < t1:
        dt = datetime.fromtimestamp(t, TZ)
        if day is None:
            day = dt.date()
        if dt.date() != day:
            day_pnl = 0.0
            day = dt.date()
        # 决策（4h 对齐）
        if dt.hour % 4 == 0 and dt.minute == 0 and t >= halt_until:
            if len(positions) < MAX_POS:
                sigs = signals(mk, btc_1d, t, apply_m, apply_price, apply_dir)
                n_signals += len(sigs)
                n_long = sum(1 for p in positions if p.dirn > 0)
                n_short = len(positions) - n_long
                for sym, dirn in sigs:
                    if len(positions) >= MAX_POS:
                        break
                    if any(p.sym == sym for p in positions):
                        continue
                    if use_guard:
                        if dirn > 0 and n_long >= MAX_SAME_DIR:
                            continue
                        if dirn < 0 and n_short >= MAX_SAME_DIR:
                            continue
                    d = mk[sym]
                    j = np.searchsorted(d["bars"].ts.values, t, side="right") - 1
                    if j < 0:
                        continue
                    px = d["bars"].o.iloc[j]
                    atr = hourly_atr(d["bars"], t)
                    if atr is None or atr <= 0:
                        continue
                    dist = min(max(K_ATR * atr / px, SL_MIN), SL_MAX)
                    risk = equity * RISK_PCT
                    notional = risk / dist
                    margin = notional / LEV_CAP
                    if margin > equity * MARGIN_CAP_EQ:
                        notional = equity * MARGIN_CAP_EQ * LEV_CAP
                    p = Pos(sym, dirn, px, notional, atr)
                    p.t_open = t
                    positions.append(p)
                    if dirn > 0:
                        n_long += 1
                    else:
                        n_short += 1
        # 推进持仓
        pnl, fee = step_positions(mk, positions, t, equity, trades, use_ladder)
        equity += pnl - fee
        day_pnl += pnl - fee
        # 资金费（每 8h）
        if dt.hour % 8 == 0 and dt.minute == 0:
            fund = apply_funding(mk, positions, t)
            equity += fund
            day_pnl += fund
        if day_pnl <= -DAILY_LOSS_HALT * start_eq and use_guard:
            halt_until = max(halt_until, t + 24 * 3600)
        eq_curve.append((t, equity, len(positions)))
        t += 900  # 15m
    # 窗口末尾强制盯市平仓（按最后一根收盘价）
    for p in positions:
        d = mk[p.sym]
        last_px = d["bars"].c.iloc[-1]
        q = p.qty
        p.cum_pnl += (last_px - p.entry) / p.entry * p.dirn * p.notional * q
        p.cum_fee += p.notional * q * (FEE_BPS + SLIP_BPS) / 1e4
        equity += (last_px - p.entry) / p.entry * p.dirn * p.notional * q
        if p.reason == "open":
            p.reason = "eow_mark"
        trades.append(p)
    positions = []
    eq = pd.DataFrame(eq_curve, columns=["ts", "equity", "npos"])
    # 逐笔统计
    nets = np.array([p.cum_pnl - p.cum_fee for p in trades])
    wins = nets[nets > 0]
    losses = nets[nets <= 0]
    longs = np.array([p.cum_pnl - p.cum_fee for p in trades if p.dirn > 0])
    shorts = np.array([p.cum_pnl - p.cum_fee for p in trades if p.dirn < 0])
    return dict(label=label, eq=eq, n_trades=len(trades), n_signals=n_signals,
                final_equity=equity, ret=(equity / start_eq - 1) * 100,
                wr=100 * len(wins) / max(len(nets), 1),
                avg_win=float(wins.mean()) if len(wins) else 0.0,
                avg_loss=float(losses.mean()) if len(losses) else 0.0,
                payoff=(wins.mean() / abs(losses.mean())) if len(wins) and len(losses) and losses.mean() else 0.0,
                net_total=float(nets.sum()), fees=float(np.sum([p.cum_fee for p in trades])),
                net_long=float(longs.sum()) if len(longs) else 0.0,
                net_short=float(shorts.sum()) if len(shorts) else 0.0,
                reasons={r: sum(1 for p in trades if p.reason == r) for r in ("sl", "tp1", "tp2", "chand", "time", "eow_mark")})


def run_window(t0, t1, label):
    mk, btc_1d = prep_market(t0, t1)
    print(f"\n===== {label}: {datetime.fromtimestamp(t0, TZ):%m-%d} → {datetime.fromtimestamp(t1, TZ):%m-%d} =====")
    variants = [
        dict(apply_m=True, apply_price=True, apply_dir=True, use_ladder=True, use_guard=True, label="full"),
        dict(apply_m=False, apply_price=True, apply_dir=True, use_ladder=True, use_guard=True, label="no_m"),
        dict(apply_m=True, apply_price=False, apply_dir=True, use_ladder=True, use_guard=True, label="no_price"),
        dict(apply_m=True, apply_price=True, apply_dir=False, use_ladder=True, use_guard=True, label="no_dir_pair"),
        dict(apply_m=True, apply_price=True, apply_dir=True, use_ladder=False, use_guard=True, label="no_ladder"),
        dict(apply_m=True, apply_price=True, apply_dir=True, use_ladder=True, use_guard=False, label="no_guard"),
    ]
    for v in variants:
        r = simulate(mk, btc_1d, t0, t1, **{k: v[k] for k in v if k != "label"}, label=v["label"])
        eq = r["eq"]
        dd = (eq.equity.cummax() - eq.equity).max() / eq.equity.cummax().max() * 100
        print(f"{v['label']:12s} trades={r['n_trades']:4d} signals={r['n_signals']:5d} "
              f"ret={r['ret']:+7.2f}% maxDD={dd:5.1f}% WR={r['wr']:4.1f}% "
              f"win={r['avg_win']:+6.2f} loss={r['avg_loss']:+6.2f} payoff={r['payoff']:.2f} "
              f"netL={r['net_long']:+8.1f} netS={r['net_short']:+8.1f} fees={r['fees']:7.1f}")
        if v["label"] == "full":
            print(f"          exits: {r['reasons']}")
        r.pop("eq")
        r["maxdd"] = round(dd, 1)
    return mk, btc_1d


def main():
    # 样本内窗口（参数拟合期）
    t0A = int(datetime(2026, 8, 30, 0, 0, tzinfo=TZ).timestamp())
    t1A = int(datetime(2026, 9, 29, 0, 0, tzinfo=TZ).timestamp())
    # 样本外窗口（参数冻结）
    t0B = int(datetime(2026, 7, 1, 0, 0, tzinfo=TZ).timestamp())
    t1B = int(datetime(2026, 8, 30, 0, 0, tzinfo=TZ).timestamp())
    mkA, btcA = run_window(t0A, t1A, "A 样本内 09月（参数拟合期）")
    mkB, btcB = run_window(t0B, t1B, "B 样本外 07-08月（参数冻结）")


if __name__ == "__main__":
    main()
