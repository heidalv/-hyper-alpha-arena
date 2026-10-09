# -*- coding: utf-8 -*-
"""[R3 目标③] 「快信号只用于**保护**、不用于开仓判定」的反事实检验。

背景（R2 已量化）：
  - 日线 regime 在真实转折后翻 down 的中位时延 = **65 小时**（结构性，不可修）；
  - 4h EMA50+mom24h 的"快 regime"中位 **0 小时**就翻 down，但**假信号率中位 100%**
    ⇒ 用它做**开仓判定**必然被噪声淹没（R2 已否证）。
  - 那么剩下唯一没被否证的用法：**只让它收紧保护**（追踪/锁利），
    错了只是"少赚"（提前离场），不像开仓那样会"做错方向"。

本脚本用**真实成交的入场点**（模拟账户 14，09-15 后开仓的 mid+long，真金白银选出来的入场，
不存在入场端的挑选偏差），只替换**出场规则**做 A/B 对照：

  A 基线：已落地的出场栈
      mid ：SL 3% / 追踪 激活5.0% 回撤2.5% / 最长 48h
      long：分批止盈 8/15/25 ×50/25/25 / 保本(峰值≥2%) / 锁利(峰值≥3% 取峰值1/3) /
            −8% 硬闸 / 最长 7d
  B 快信号收紧（唯一差别）：当**快 regime 转 down 而日线 regime 仍为 up** 时，自该时刻起
      追踪改为 激活2.0% / 回撤1.0%；锁利阈值降到 峰值≥1% 且比例取 1/2。

**预登记验收标准（看结果前写定，事后不得修改）**
  通过 = ①总盈亏改善 > 0 ②最差 3 笔合计**不变差** ③前后半样本**都**改善
        ④改善不由单币主导（最大单币贡献 < 50%）⑤被触发的交易 ≥ 10 笔
  任一不满足 → 判定"未通过"，不落地。

路径用 binance 1h（覆盖率 100%），A/B 同一路径 ⇒ 绝对水平是近似、**差值**才是结论。
只读。
"""
from __future__ import annotations

import bisect
import datetime as dt
import io
import statistics as st
import sys

import psycopg

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ARENA = "postgresql://laobao:alpha_pass@localhost:5432/alpha_arena"
MARKET = "postgresql://laobao:alpha_pass@localhost:5432/alpha_market"
CST = dt.timezone(dt.timedelta(hours=8))
SINCE = int(dt.datetime(2026, 9, 15, tzinfo=CST).timestamp())
H = 3600
FEE_PP = 0.10            # 出入双边合计（pp），与其它脚本同口径
K200 = 2.0 / 201.0

# ── 已落地参数（与 .env 对齐）──
MID_SL = 3.0
MID_TRAIL_ACT, MID_TRAIL_CB = 5.0, 2.5
MID_MAX_H = 48.0
LONG_STAGES = ((8.0, 0.50), (15.0, 0.25), (25.0, 0.25))
LONG_BREAKEVEN_PEAK, LONG_LOCK_PEAK, LONG_LOCK_FRAC = 2.0, 3.0, 1.0 / 3.0
LONG_LOSS_CAP = 8.0
LONG_MAX_H = 168.0
# ── B 变体的收紧参数 ──
B_TRAIL_ACT, B_TRAIL_CB = 2.0, 1.0
B_LOCK_PEAK, B_LOCK_FRAC = 1.0, 0.5


def load_1h(cur, sym):
    cur.execute(
        """select timestamp, high_price, low_price, close_price from crypto_klines
           where symbol=%s and exchange='binance' and period='1h' and environment='mainnet'
           order by timestamp""", (sym,))
    return [(int(r[0]), float(r[1]), float(r[2]), float(r[3])) for r in cur.fetchall()]


def daily_regime_series(cur, sym, hourly_ts):
    """每小时上的日线 regime（**含形成中当日 bar**，与生产 _daily_regime 同口径）。"""
    cur.execute(
        """select timestamp, close_price from crypto_klines
           where symbol=%s and exchange='binance' and period='1d' and environment='mainnet'
           order by timestamp""", (sym,))
    d = [(int(r[0]), float(r[1])) for r in cur.fetchall()]
    if len(d) < 260:
        return {}
    dts = [x[0] for x in d]
    dcl = [x[1] for x in d]
    k = K200
    e = dcl[0]
    ema = [e]
    for v in dcl[1:]:
        e = v * k + e * (1 - k)
        ema.append(e)
    out = {}
    for t, c, hi, lo in hourly_ts:
        j = bisect.bisect_right(dts, t) - 1
        if j < 200:
            continue
        ema_live = c * k + ema[j - 1] * (1 - k)
        base = dcl[j - 60] if j - 60 >= 0 else dcl[0]
        mom = (c / base - 1.0) if base > 0 else 0.0
        if c > ema_live and mom > 0.05:
            out[t] = "up"
        elif c < ema_live and mom < -0.05:
            out[t] = "down"
        else:
            out[t] = "chop"
    return out


def fast_series(bars):
    """4h EMA50 + mom24h（只用已收盘 4h bar；每小时更新一次取值）。"""
    k4 = 2.0 / 51.0
    e4 = None
    out = {}
    closes = [b[3] for b in bars]
    for i, (t, hi, lo, c) in enumerate(bars):
        if t % (4 * H) == 0:
            e4 = c if e4 is None else c * k4 + e4 * (1 - k4)
        if e4 is None or i < 24:
            continue
        base = closes[i - 24]
        mom = (c / base - 1.0) if base > 0 else 0.0
        out[t] = "down" if (c < e4 and mom < -0.03) else ("up" if (c > e4 and mom > 0.03) else "chop")
    return out


def simulate(entry, size, bars, tier, d_reg, f_reg, tighten: bool):
    """返 (pnl_usd, exit_ts, 触发收紧?). 逐 1h bar 走。"""
    is_short = False
    sl = entry * (1 - MID_SL / 100.0) if tier == "mid" else entry * (1 - LONG_LOSS_CAP / 100.0)
    peak = 0.0
    remaining = size
    realized = 0.0
    staged_done = set()
    max_h = MID_MAX_H if tier == "mid" else LONG_MAX_H
    dead = bars[0][0] + int(max_h * H)
    triggered = False
    trail_act, trail_cb = MID_TRAIL_ACT, MID_TRAIL_CB
    lock_peak, lock_frac = (LONG_LOCK_PEAK, LONG_LOCK_FRAC) if tier == "long" else (1e9, 0.0)
    exit_price = None
    for t, hi, lo, c in bars:
        if t > dead:
            exit_price = c
            break
        # B 变体：快 regime 转 down 且日线仍 up → 收紧（只用 t 时刻已可得的信息）
        if tighten and not triggered and d_reg.get(t) == "up" and f_reg.get(t) == "down":
            triggered = True
            if tier == "mid":
                trail_act, trail_cb = B_TRAIL_ACT, B_TRAIL_CB
            else:
                lock_peak, lock_frac = B_LOCK_PEAK, B_LOCK_FRAC
        ext = (hi - entry) / entry * 100.0
        if ext > peak:
            peak = ext
        # 分批止盈（long）
        if tier == "long":
            for lvl, frac in LONG_STAGES:
                if lvl not in staged_done and hi >= entry * (1 + lvl / 100.0):
                    px = entry * (1 + lvl / 100.0)
                    qty = size * frac
                    realized += (px - entry) * qty
                    remaining -= qty
                    staged_done.add(lvl)
        # 止损推进
        cands = []
        if tier == "mid":
            if peak >= trail_act:
                cands.append(entry * (1 + (peak - trail_cb) / 100.0))
        else:
            if peak >= LONG_BREAKEVEN_PEAK:
                cands.append(entry)
            if peak >= lock_peak:
                cands.append(entry * (1 + lock_frac * peak / 100.0))
        cands.append(entry * (1 - (MID_SL if tier == "mid" else LONG_LOSS_CAP) / 100.0))
        sl = max(cands)
        # −8% 硬闸（long，tick 级，这里用 bar 内 low 判定）
        if tier == "long" and lo <= entry * (1 - LONG_LOSS_CAP / 100.0):
            exit_price = entry * (1 - LONG_LOSS_CAP / 100.0)
            break
        if lo <= sl:
            exit_price = sl
            break
        if remaining <= 1e-12:
            exit_price = c
            break
    if exit_price is None:
        exit_price = bars[-1][3]
    realized += (exit_price - entry) * max(0.0, remaining)
    # 手续费按名义计（近似）：双边 0.10pp × 入场名义
    fee = entry * size * FEE_PP / 100.0
    return realized - fee, triggered, peak


def main() -> int:
    with psycopg.connect(ARENA, autocommit=True) as ac, psycopg.connect(MARKET, autocommit=True) as mc:
        acur = ac.cursor(); acur.execute("SET app.is_admin='on'")
        acur.execute(
            """select symbol, timeframe_tier, entry_price, original_size, opened_at, status,
                      close_price, close_reason, peak_pnl_pct, unrealized_pnl, partial_realized_pnl
               from paper_positions
               where account_id=14 and opened_at >= %s and side='long'
               order by opened_at""",
            (dt.datetime.fromtimestamp(SINCE, CST).replace(tzinfo=None),))
        rows = acur.fetchall()
        print("09-15 后开仓的 mid/long 多头仓：%d 笔" % len(rows))
        mcur = mc.cursor()
        cache: dict = {}
        trades = []
        for sym, tier, entry, size, opened, status, close_px, reason, peak_col, upnl, part in rows:
            tier = (tier or "").lower()
            if tier not in ("mid", "long") or not entry or not size:
                continue
            if sym not in cache:
                bars = load_1h(mcur, sym)
                cache[sym] = (bars, [b[0] for b in bars],
                              daily_regime_series(mcur, sym, bars), fast_series(bars))
            bars, tss, dreg, freg = cache[sym]
            t0 = int(opened.replace(tzinfo=CST).timestamp()) if opened.tzinfo is None else int(opened.timestamp())
            j = bisect.bisect_left(tss, t0)
            if j >= len(tss):
                continue
            seg = bars[j:]
            if len(seg) < 24:
                continue
            e = float(entry); s = float(size)
            a_pnl, _, a_peak = simulate(e, s, seg, tier, dreg, freg, tighten=False)
            b_pnl, trig, _ = simulate(e, s, seg, tier, dreg, freg, tighten=True)
            trades.append({"sym": sym, "tier": tier, "t": t0, "a": a_pnl, "b": b_pnl,
                           "trig": trig, "notional": e * s})
        if not trades:
            print("无可评估样本")
            return 0
        trades.sort(key=lambda x: x["t"])
        tot_a = sum(t["a"] for t in trades)
        tot_b = sum(t["b"] for t in trades)
        diff = tot_b - tot_a
        trig_n = sum(1 for t in trades if t["trig"])
        print("\n样本 %d 笔（mid %d / long %d）；B 变体触发 %d 笔"
              % (len(trades), sum(1 for t in trades if t["tier"] == "mid"),
                 sum(1 for t in trades if t["tier"] == "long"), trig_n))
        print("  A 基线合计 %+.2f USD ；B 快信号收紧 %+.2f USD ；**差 %+.2f USD**"
              % (tot_a, tot_b, diff))
        for tier in ("mid", "long"):
            seg = [t for t in trades if t["tier"] == tier]
            if not seg:
                continue
            aa = sum(t["a"] for t in seg); bb = sum(t["b"] for t in seg)
            print("    %-5s n=%-3d A %+8.2f  B %+8.2f  差 %+8.2f（触发 %d）"
                  % (tier, len(seg), aa, bb, bb - aa, sum(1 for t in seg if t["trig"])))
        worst_a = sorted(trades, key=lambda x: x["a"])[:3]
        worst_b = sorted(trades, key=lambda x: x["b"])[:3]
        print("  最差 3 笔：A %+.2f / B %+.2f  ⇒ %s"
              % (sum(t["a"] for t in worst_a), sum(t["b"] for t in worst_b),
                 "尾部不变差" if sum(t["b"] for t in worst_b) >= sum(t["a"] for t in worst_a) else "**尾部变差**"))
        half = len(trades) // 2
        h1a = sum(t["a"] for t in trades[:half]); h1b = sum(t["b"] for t in trades[:half])
        h2a = sum(t["a"] for t in trades[half:]); h2b = sum(t["b"] for t in trades[half:])
        print("  前半 A %+.2f / B %+.2f （差 %+.2f）；后半 A %+.2f / B %+.2f （差 %+.2f）"
              % (h1a, h1b, h1b - h1a, h2a, h2b, h2b - h2a))
        per = {}
        for t in trades:
            per[t["sym"]] = per.get(t["sym"], 0.0) + (t["b"] - t["a"])
        top = max(per.items(), key=lambda kv: abs(kv[1])) if per else ("-", 0.0)
        conc = abs(top[1]) / abs(diff) * 100 if diff else 0.0
        print("  最大单币贡献：%s %+.2f（占差 %.0f%%）" % (top[0], top[1], conc))
        crit = [diff > 0,
                sum(t["b"] for t in worst_b) >= sum(t["a"] for t in worst_a),
                (h1b - h1a) > 0 and (h2b - h2a) > 0,
                conc < 50.0,
                trig_n >= 10]
        print("\n  预登记验收：①改善%.0f ②尾部%.0f ③双半%.0f ④集中度%.0f ⑤触发%d  ⇒ **%s**"
              % (crit[0], crit[1], crit[2], crit[3], trig_n,
                 "通过" if all(crit) else "未通过"))
        print("\n  逐笔（前 24）：")
        for t in trades[:24]:
            print("    %s %-8s %-5s A %+7.2f  B %+7.2f  差 %+7.2f  %s"
                  % (dt.datetime.fromtimestamp(t["t"], CST).strftime("%m-%d %H:%M"),
                     t["sym"], t["tier"], t["a"], t["b"], t["b"] - t["a"],
                     "触发" if t["trig"] else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
