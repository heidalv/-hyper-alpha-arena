# -*- coding: utf-8 -*-
"""[R3 目标③ v2] 「快信号只用于保护」的**忠实**反事实：直接驱动生产出场策略引擎。

v1 为什么不可信（自查记录）：v1 自己重写了出场逻辑（只建了 SL + 追踪5/2.5 + 最长持有），
漏掉了生产实际生效的整条规则链（`min_roi_decay` 时间递减 ROI、`trailing_callback`、
`structural_invalidation`、硬 SL 价、time_limit）。逐笔对照即可证伪：
ASTER #4684 实盘 **+$1.78**（7.2h 被 `exit_policy:trailing_callback` 平掉，峰值仅 1.22%），
v1 模型给 **+$77.41**（≈43 倍）；整体 v1 基线 +1609 USD vs 实盘已实现 ≈ **−58 USD**。
⇒ v1 的 −69.73 差值**不可用**（既不能证明、也不能否证该想法）。

v2 改为**调用生产函数本身**：`backend.services.exit.exit_policy.ExitPolicy.for_lane()` +
`evaluate()`，A/B 只差一处——快信号触发后把 `trailing_activation_pct` / `trailing_callback_pct`
调紧（其余规则、其余车道参数完全一致）。

**预登记验收标准（看结果前写定，事后不得修改）**
  通过 = ①总盈亏改善 > 0 ②最差 3 笔不变差 ③前后半都改善 ④最大单币贡献 < 50% ⑤触发 ≥ 10 笔
  任一不满足 → 不落地。
只读。
"""
from __future__ import annotations

import bisect
import dataclasses
import datetime as dt
import io
import sys

import psycopg

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

from dotenv import load_dotenv  # noqa: E402

load_dotenv(".env", override=True)

from backend.services.exit.exit_policy import ExitPolicy, ExitSnapshot, evaluate  # noqa: E402

ARENA = "postgresql://laobao:alpha_pass@localhost:5432/alpha_arena"
MARKET = "postgresql://laobao:alpha_pass@localhost:5432/alpha_market"
CST = dt.timezone(dt.timedelta(hours=8))
SINCE = int(dt.datetime(2026, 9, 15, tzinfo=CST).timestamp())
H = 3600
FEE_PP = 0.10
K200 = 2.0 / 201.0
TIGHTEN_ACT, TIGHTEN_CB = 1.0, 0.5   # B：激活 1.0%、回撤 0.5%（原 mid 5.0/2.5）


def load_1h(cur, sym):
    cur.execute(
        """select timestamp, high_price, low_price, close_price from crypto_klines
           where symbol=%s and exchange='binance' and period='1h' and environment='mainnet'
           order by timestamp""", (sym,))
    return [(int(r[0]), float(r[1]), float(r[2]), float(r[3])) for r in cur.fetchall()]


def daily_regime(cur, sym, bars):
    cur.execute(
        """select timestamp, close_price from crypto_klines
           where symbol=%s and exchange='binance' and period='1d' and environment='mainnet'
           order by timestamp""", (sym,))
    d = [(int(r[0]), float(r[1])) for r in cur.fetchall()]
    if len(d) < 260:
        return {}
    dts = [x[0] for x in d]
    dcl = [x[1] for x in d]
    e = dcl[0]
    ema = [e]
    for v in dcl[1:]:
        e = v * K200 + e * (1 - K200)
        ema.append(e)
    out = {}
    for t, hi, lo, c in bars:
        j = bisect.bisect_right(dts, t) - 1
        if j < 200:
            continue
        ema_live = c * K200 + ema[j - 1] * (1 - K200)
        base = dcl[j - 60] if j - 60 >= 0 else dcl[0]
        mom = (c / base - 1.0) if base > 0 else 0.0
        out[t] = "up" if (c > ema_live and mom > 0.05) else ("down" if (c < ema_live and mom < -0.05) else "chop")
    return out


def fast_regime(bars):
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


def replay(policy, entry, size, sl0, seg, f_reg, d_reg, tighten):
    """用生产 evaluate() 逐 bar 走一遍。返回 (pnl_usd, triggered, reason)。"""
    pol = policy
    sl = sl0
    peak = 0.0
    triggered = False
    elapsed0 = seg[0][0]
    for t, hi, lo, c in seg:
        el = t - elapsed0
        if tighten and not triggered and d_reg.get(t) == "up" and f_reg.get(t) == "down":
            triggered = True
            pol = dataclasses.replace(policy,
                                      trailing_activation_pct=TIGHTEN_ACT,
                                      trailing_callback_pct=TIGHTEN_CB)
        roi_hi = (hi - entry) / entry * 100.0
        peak = max(peak, roi_hi)
        # 先按 bar 内极值判一次（捕捉硬 SL/结构/追踪回撤），再按收盘判一次
        for cur in (lo, c):
            v = evaluate(pol, ExitSnapshot(side="long", entry=entry, current=cur,
                                           elapsed_sec=el, peak_roi_pct=peak, sl_price=sl))
            if v.action == "close":
                return (cur - entry) * size - entry * size * FEE_PP / 100.0, triggered, v.reason
            if v.action == "tighten_sl" and v.new_sl:
                sl = max(sl or 0.0, float(v.new_sl))
        lo_roi = (lo - entry) / entry * 100.0
        if sl and lo <= sl:
            return (sl - entry) * size - entry * size * FEE_PP / 100.0, triggered, "hard_sl"
    last = seg[-1][3]
    return (last - entry) * size - entry * size * FEE_PP / 100.0, triggered, "eod"


def main() -> int:
    pol_mid = ExitPolicy.for_lane("mid")
    pol_long = ExitPolicy.for_lane("long")
    print("生产策略：mid act=%s cb=%s sl_pct=%s time_limit=%s min_roi=%s"
          % (pol_mid.trailing_activation_pct, pol_mid.trailing_callback_pct,
             pol_mid.sl_pct, pol_mid.time_limit_sec, pol_mid.min_roi))
    print("          long act=%s cb=%s sl_pct=%s time_limit=%s"
          % (pol_long.trailing_activation_pct, pol_long.trailing_callback_pct,
             pol_long.sl_pct, pol_long.time_limit_sec))
    with psycopg.connect(ARENA, autocommit=True) as ac, psycopg.connect(MARKET, autocommit=True) as mc:
        acur = ac.cursor(); acur.execute("SET app.is_admin='on'")
        acur.execute(
            """select symbol, timeframe_tier, entry_price, original_size, sl_price, opened_at
               from paper_positions
               where account_id=14 and opened_at >= %s and side='long'
               order by opened_at""",
            (dt.datetime.fromtimestamp(SINCE, CST).replace(tzinfo=None),))
        rows = acur.fetchall()
        mcur = mc.cursor()
        cache: dict = {}
        trades = []
        for sym, tier, entry, size, slp, opened in rows:
            tier = (tier or "").lower()
            if tier not in ("mid", "long") or not entry or not size:
                continue
            if sym not in cache:
                bars = load_1h(mcur, sym)
                cache[sym] = (bars, [b[0] for b in bars], daily_regime(mcur, sym, bars), fast_regime(bars))
            bars, tss, dreg, freg = cache[sym]
            t0 = int(opened.replace(tzinfo=CST).timestamp()) if opened.tzinfo is None else int(opened.timestamp())
            j = bisect.bisect_left(tss, t0)
            if j >= len(tss) - 24:
                continue
            e = float(entry); s = float(size)
            # 初始硬 SL：优先用仓位上记录的 sl_price（±15% 内视为可用），否则用车道声明
            sl0 = None
            if slp:
                try:
                    v = float(slp)
                    if 0 < v < e and (e - v) / e <= 0.15:
                        sl0 = v
                except (TypeError, ValueError):
                    pass
            if sl0 is None:
                pol = pol_mid if tier == "mid" else pol_long
                sl0 = e * (1 - (float(pol.sl_pct or 8.0)) / 100.0)
            pol = pol_mid if tier == "mid" else pol_long
            seg = bars[j:]
            a, _, ra = replay(pol, e, s, sl0, seg, freg, dreg, tighten=False)
            b, trig, rb = replay(pol, e, s, sl0, seg, freg, dreg, tighten=True)
            trades.append({"sym": sym, "tier": tier, "t": t0, "a": a, "b": b, "trig": trig,
                           "ra": ra, "rb": rb})
        if not trades:
            print("无可评估样本"); return 0
        trades.sort(key=lambda x: x["t"])
        tot_a = sum(t["a"] for t in trades); tot_b = sum(t["b"] for t in trades)
        diff = tot_b - tot_a
        trig_n = sum(1 for t in trades if t["trig"])
        print("\n样本 %d 笔（mid %d / long %d）；B 触发 %d 笔"
              % (len(trades), sum(1 for t in trades if t["tier"] == "mid"),
                 sum(1 for t in trades if t["tier"] == "long"), trig_n))
        print("  A 生产策略 %+.2f USD ；B 快信号收紧 %+.2f USD ；**差 %+.2f USD**" % (tot_a, tot_b, diff))
        for tier in ("mid", "long"):
            seg = [t for t in trades if t["tier"] == tier]
            if seg:
                aa = sum(t["a"] for t in seg); bb = sum(t["b"] for t in seg)
                print("    %-5s n=%-3d A %+8.2f  B %+8.2f  差 %+8.2f（触发 %d）"
                      % (tier, len(seg), aa, bb, bb - aa, sum(1 for t in seg if t["trig"])))
        wa = sum(t["a"] for t in sorted(trades, key=lambda x: x["a"])[:3])
        wb = sum(t["b"] for t in sorted(trades, key=lambda x: x["b"])[:3])
        print("  最差3笔 A %+.2f / B %+.2f ⇒ %s" % (wa, wb, "尾部不变差" if wb >= wa else "**尾部变差**"))
        half = len(trades) // 2
        d1 = sum(t["b"] - t["a"] for t in trades[:half]); d2 = sum(t["b"] - t["a"] for t in trades[half:])
        print("  前半差 %+.2f ；后半差 %+.2f" % (d1, d2))
        per = {}
        for t in trades:
            per[t["sym"]] = per.get(t["sym"], 0.0) + (t["b"] - t["a"])
        top = max(per.items(), key=lambda kv: abs(kv[1])) if per else ("-", 0.0)
        conc = abs(top[1]) / abs(diff) * 100 if diff else 0.0
        print("  最大单币贡献 %s %+.2f（占差 %.0f%%）" % (top[0], top[1], conc))
        crit = [diff > 0, wb >= wa, d1 > 0 and d2 > 0, conc < 50.0, trig_n >= 10]
        print("\n  预登记验收：①改善%.0f ②尾部%.0f ③双半%.0f ④集中度%.0f ⑤触发%d ⇒ **%s**"
              % (crit[0], crit[1], crit[2], crit[3], trig_n, "通过" if all(crit) else "未通过"))
        print("\n  逐笔（有差的全部）：")
        for t in trades:
            if abs(t["b"] - t["a"]) > 1e-9:
                print("    %s %-8s %-5s A %+7.2f(%s)  B %+7.2f(%s)  差 %+7.2f %s"
                      % (dt.datetime.fromtimestamp(t["t"], CST).strftime("%m-%d %H:%M"), t["sym"], t["tier"],
                         t["a"], t["ra"][:18], t["b"], t["rb"][:18], t["b"] - t["a"],
                         "触发" if t["trig"] else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
