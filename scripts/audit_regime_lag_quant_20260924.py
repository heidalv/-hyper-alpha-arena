# -*- coding: utf-8 -*-
"""[R2 目标③] regime 确认滞后：把「结构性滞后」与「运行性滞后」拆开量化。

背景（R1 已确认的事实）：
  - `crypto_klines` 的日线表**包含正在形成的当日 bar**，其 close = 最新价（实测 BTC 1d
    2026-09-24 08:00 close=84109.90 ≈ 实时价）⇒ `_daily_regime` 其实**每半小时就跟着实时价更新**，
    并不是"等日线收盘"。
  - 那"慢"到底慢在哪？两个完全不同的东西被混为一谈：
      (A) **结构性滞后**：定义是 收盘 vs EMA200 且 60 日动量 ±5%。BTC 当前 mom60=+28.7%，
          要翻成 chop 需 mom60 掉到 +5% 以下（≈再跌 20% 或等 60 日基准滚出）→ 以**周**计。
      (B) **运行性滞后**：`_DAILY_REGIME_CACHE_TTL_S=1800`（缓存 30 分钟）+ K 线上游刷新间隔。

本脚本用**精确递推**在每小时上重算 regime（EMA 是单步递推，故
`ema_t = live_close_t*k + ema_昨收*(1-k)` 是精确值，不是近似），从而：
  1) 量化 (B)：实时感知版 regime 比"只用已收盘日线"版早多少小时给出同一个标签；
  2) 量化 (A)：在**真实转折**（从摆动高点 ≥10% 回撤）发生时，各"快速 regime"候选
     比日线 regime 早多少小时翻向；以及早翻的这段时间价格已经走了多少（= 慢确认的代价）；
  3) 假信号率：快速候选翻向后 48h 内又翻回的次数（whipsaw）。
只读，不改生产。
"""
from __future__ import annotations

import bisect
import datetime as dt
import io
import statistics as st
import sys

import psycopg

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
MARKET = "postgresql://laobao:alpha_pass@localhost:5432/alpha_market"
CST = dt.timezone(dt.timedelta(hours=8))
DAY = 86400
H = 3600
MAJORS = ["BTC", "ETH", "SOL", "XRP", "BNB", "LINK", "AVAX", "UNI", "VIRTUAL", "ASTER"]
K200 = 2.0 / 201.0


def ema_series(vals, k):
    e = vals[0]
    out = [e]
    for v in vals[1:]:
        e = v * k + e * (1 - k)
        out.append(e)
    return out


def regime_of(px, ema, mom60):
    if px > ema and mom60 > 0.05:
        return "up"
    if px < ema and mom60 < -0.05:
        return "down"
    return "chop"


def load(cur, sym, period, t0, t1):
    cur.execute(
        """select timestamp, close_price, high_price, low_price from crypto_klines
           where symbol=%s and exchange='binance' and period=%s and environment='mainnet'
             and timestamp between %s and %s order by timestamp""", (sym, period, t0, t1))
    return [(int(r[0]), float(r[1]), float(r[2]), float(r[3])) for r in cur.fetchall()]


def main() -> int:
    with psycopg.connect(MARKET, autocommit=True) as mc:
        cur = mc.cursor()
        cur.execute(
            """select symbol from crypto_klines where exchange='binance' and period='4h'
                 and environment='mainnet' and timestamp >= %s
               group by symbol having count(*)>=50
               order by sum(volume*close_price) desc nulls last limit 25""",
            (int(dt.datetime(2026, 8, 6, tzinfo=CST).timestamp()),))
        prev25 = [r[0] for r in cur.fetchall()]
        syms = MAJORS + [s for s in prev25 if s not in MAJORS]
        print("样本币 %d：%s" % (len(syms), ",".join(syms)))

        rows_out = []
        for sym in syms:
            daily = load(cur, sym, "1d", 0, 2 ** 31)
            hourly = load(cur, sym, "1h", 0, 2 ** 31)
            if len(daily) < 260 or len(hourly) < 2000:
                continue
            dts = [d[0] for d in daily]
            dcl = [d[1] for d in daily]
            ema200 = ema_series(dcl, K200)
            # 日线 close 索引：day_start -> idx
            h1 = [(t, c, hi, lo) for t, c, hi, lo in hourly]
            # 逐小时重算
            live_lab, closed_lab = [], []
            for t, c, hi, lo in h1:
                j = bisect.bisect_right(dts, t) - 1          # 当前（形成中）日 bar
                if j < 200:
                    live_lab.append("")
                    closed_lab.append("")
                    continue
                # (B) 实时感知版：当日 close 用实时价
                ema_live = c * K200 + ema200[j - 1] * (1 - K200)
                base_i = j - 60
                base = dcl[base_i] if base_i >= 0 else dcl[0]
                mom_live = (c / base - 1.0) if base > 0 else 0.0
                live_lab.append(regime_of(c, ema_live, mom_live))
                # 对照：只用已收盘日线（j-1 为最后一根已收盘）
                ema_cl = ema200[j - 1]
                px_cl = dcl[j - 1]
                base_c = dcl[j - 61] if j - 61 >= 0 else dcl[0]
                mom_cl = (px_cl / base_c - 1.0) if base_c > 0 else 0.0
                closed_lab.append(regime_of(px_cl, ema_cl, mom_cl))
            # 快速候选：4h EMA50 + mom24h / mom72h 基于 1h 序列
            closes = [c for _, c, _, _ in h1]
            ts = [t for t, _, _, _ in h1]
            e50_4h = None
            d4 = {}
            for i, (t, c, hi, lo) in enumerate(h1):
                d4[t] = c
            # 4h 序列（按 4h 边界取）
            k4 = 2.0 / 51.0
            e4 = None
            e4_series = {}
            prev4 = None
            for i, t in enumerate(ts):
                if t % (4 * H) == 0:
                    e4 = c if e4 is None else c * k4 + e4 * (1 - k4)
                    prev4 = e4
                e4_series[t] = prev4 if prev4 is not None else c

            def fast_lab(i, mom_h):
                if i < mom_h:
                    return ""
                c = closes[i]
                base = closes[i - mom_h]
                if base <= 0 or not e4_series.get(ts[i]):
                    return ""
                mom = c / base - 1.0
                if c < e4_series[ts[i]] and mom < -0.03:
                    return "down"
                if c > e4_series[ts[i]] and mom > 0.03:
                    return "up"
                return "chop"

            fast24 = [fast_lab(i, 24) for i in range(len(ts))]
            fast72 = [fast_lab(i, 72) for i in range(len(ts))]

            # ── (B) 运行性滞后：closed → live 的标签切换提前量 ──
            # 只在"closed 版整段稳定、live 版更早翻"的场景量化
            leads = []
            for i in range(1, len(ts)):
                if closed_lab[i] != closed_lab[i - 1] and closed_lab[i] and live_lab[i]:
                    # closed 在第 i 小时翻；找 live 版最近一次变成同一标签的更早时刻
                    tgt = closed_lab[i]
                    k = i
                    while k > 0 and live_lab[k - 1] == tgt:
                        k -= 1
                    leads.append((i - k))
            # ── 真实转折与各信号领先量 ──
            # 转折定义：1h 收盘从 5 日滚动高点回撤 ≥10%（首次触发为该腿起点）
            legs = []
            n = len(ts)
            win = 120
            armed = True
            for i in range(win, n):
                hi = max(closes[i - win:i + 1])
                if closes[i] <= hi * 0.90 and armed:
                    legs.append(i)
                    armed = False
                if closes[i] >= hi * 0.99:
                    armed = True
            slow_lead, f24_lead, f72_lead, missed = [], [], [], []
            for i in legs:
                if i < 160 or i + 48 >= n:
                    continue
                # [R2 修正] 必须排除"腿起点时 regime 已经是 down"的情形——否则
                # first_down 恒返回 0，把结构性滞后假报成 0 小时（旧版就是这个假象）。
                if live_lab[i] == "down":
                    continue
                # 该腿起点之后，各信号首次给出 down 的小时数
                def first_down(lab):
                    for k in range(i, min(i + 240, n)):
                        if lab[k] == "down":
                            return k - i
                    return None
                sd = first_down(live_lab)
                a = first_down(fast24)
                b = first_down(fast72)
                if sd is not None:
                    slow_lead.append(sd)
                if a is not None:
                    f24_lead.append(a)
                if b is not None:
                    f72_lead.append(b)
                if sd is not None and a is not None and a < sd:
                    missed.append((closes[i] / closes[i + sd] - 1) * 100)
            # ── 假信号率：fast24 down 后 48h 内 recovers（回到 up/chop） ──
            ws = tot = 0
            i = 1
            while i < n:
                if fast24[i] == "down" and fast24[i - 1] != "down":
                    tot += 1
                    for k in range(i + 1, min(i + 48, n)):
                        if fast24[k] != "down":
                            ws += 1
                            break
                    while i < n and fast24[i] == "down":
                        i += 1
                i += 1
            slow_flips = sum(1 for i in range(1, len(ts))
                             if live_lab[i] and live_lab[i - 1] and live_lab[i] != live_lab[i - 1])
            rows_out.append({
                "sym": sym, "lead": st.median(leads) if leads else None,
                "legs": len(slow_lead),
                "slow": st.median(slow_lead) if slow_lead else None,
                "f24": st.median(f24_lead) if f24_lead else None,
                "f72": st.median(f72_lead) if f72_lead else None,
                "missed": st.mean(missed) if missed else None,
                "ws": (100.0 * ws / tot) if tot else None, "wsn": tot,
                "slow_flips": slow_flips, "hours": len(ts),
            })

        print("\n[1] 运行性滞后（实时感知版 vs 只用已收盘日线，同一标签的提前小时数）")
        print("    %-9s %10s %12s %12s" % ("币", "中位提前h", "日线regime翻转次数", "样本小时"))
        lead_all = []
        for r in rows_out:
            if r["lead"] is not None:
                lead_all.append(r["lead"])
            print("    %-9s %10s %12d %12d"
                  % (r["sym"], ("%.0f" % r["lead"]) if r["lead"] is not None else "n/a",
                     r["slow_flips"], r["hours"]))
        if lead_all:
            print("    ⇒ 全样本中位提前 **%.0f 小时**（即「实时价参与 regime」最多也就快这么多）"
                  % st.median(lead_all))

        print("\n[2] 结构性滞后：真实转折（5日高点回撤≥10%）后，各信号首次翻 down 的小时数")
        print("    %-9s %6s %10s %10s %10s %12s" %
              ("币", "转折数", "日线(h)", "4hEMA50+24h(h)", "4hEMA50+72h(h)", "早翻期间已跌%"))
        s_all, a_all, b_all, m_all = [], [], [], []
        for r in rows_out:
            if r["legs"] == 0:
                continue
            for key, acc in (("slow", s_all), ("f24", a_all), ("f72", b_all)):
                if r[key] is not None:
                    acc.append(r[key])
            if r["missed"] is not None:
                m_all.append(r["missed"])
            print("    %-9s %6d %10s %14s %14s %12s"
                  % (r["sym"], r["legs"],
                     "%.0f" % r["slow"] if r["slow"] is not None else "未翻",
                     "%.0f" % r["f24"] if r["f24"] is not None else "未翻",
                     "%.0f" % r["f72"] if r["f72"] is not None else "未翻",
                     "%.1f" % r["missed"] if r["missed"] is not None else "-"))
        print("    ⇒ 中位：日线 %.0fh / 快24 %.0fh / 快72 %.0fh；快24 早于日线时，"
              "期间价格已跌均值 %.1f%%"
              % (st.median(s_all) if s_all else -1, st.median(a_all) if a_all else -1,
                 st.median(b_all) if b_all else -1, st.mean(m_all) if m_all else 0))

        print("\n[3] 快速候选的假信号率（翻 down 后 48h 内又翻回 up/chop 的比例）")
        for r in rows_out:
            print("    %-9s %.0f%%  （%d 次 down 段）"
                  % (r["sym"], r["ws"] if r["ws"] is not None else -1, r["wsn"]))
        wss = [r["ws"] for r in rows_out if r["ws"] is not None]
        if wss:
            print("    ⇒ 中位假信号率 **%.0f%%**" % st.median(wss))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
