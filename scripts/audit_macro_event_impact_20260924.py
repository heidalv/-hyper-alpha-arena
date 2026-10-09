# -*- coding: utf-8 -*-
"""[R1-宏观] 2026 宏观事件（FOMC / 非农 / CPI）对 BTC/ETH 与全市场的影响 + BTC/ETH 领先性。

用户口径（2026-09-24）：「美国宣布非农经济、宣布加息降息的时间，都是变盘时间，
尤其是 btc 和 eth，主导全部经济」。

来源（官方/权威日历，2026 美东时间）：
  FOMC 决议：Jan28 Mar18 Apr29 Jun17 Jul29 **Sep16** | Oct28 Dec9（未来）
  Employment Report(非农)：Jan9 Feb6 Mar6 Apr3 May8 Jun5 Jul2 Aug7 **Sep4** | Oct2 Nov6 Dec4
  CPI：Jan13 Feb11 Mar11 Apr10 May12 Jun10 Jul14 Aug12 **Sep11** | Oct14 Nov10 Dec10
  PPI：Sep10 Oct15 Nov13 Dec15
发布时刻：非农/CPI/PPI = 08:30 ET = 12:30 UTC = **20:30 CST**；FOMC 决议 = 14:00 ET = **02:00 CST(次日)**。

本脚本回答三件事（只用 K 线市场数据，不引用任何业绩记录）：
  1) 事件窗口波动 vs 基线：事件前后 24h 的已实现波动是否显著放大（"变盘时间"是否成立）
  2) 方向翻转：事件前 24h 与事件后 24h 收益符号相反（趋势反转）的概率
  3) BTC/ETH 领先性：BTC/ETH 1h 收益与山寨币收益的领先滞后互相关（谁主导）
只读。
"""
from __future__ import annotations

import datetime as dt
import io
import math
import statistics as st
import sys

import psycopg

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
MARKET = "postgresql://laobao:alpha_pass@localhost:5432/alpha_market"
UTC = dt.timezone.utc
CST = dt.timezone(dt.timedelta(hours=8))
H = 3600

FOMC = ["2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17", "2026-07-29", "2026-09-16"]
NFP = ["2026-01-09", "2026-02-06", "2026-03-06", "2026-04-03", "2026-05-08", "2026-06-05",
       "2026-07-02", "2026-08-07", "2026-09-04"]
CPI = ["2026-01-13", "2026-02-11", "2026-03-11", "2026-04-10", "2026-05-12", "2026-06-10",
       "2026-07-14", "2026-08-12", "2026-09-11"]
FUTURE = {"FOMC": ["2026-10-28", "2026-12-09"], "NFP": ["2026-10-02", "2026-11-06", "2026-12-04"],
          "CPI": ["2026-10-14", "2026-11-10", "2026-12-10"]}
ALTS = ["SOL", "XRP", "BNB", "LINK", "AVAX", "UNI", "DOGE", "ADA", "SUI", "1000PEPE"]


def ev_ts(datestr: str, hour_cst: int) -> int:
    y, m, d = (int(x) for x in datestr.split("-"))
    if hour_cst >= 24:  # FOMC 02:00 CST 次日
        base = dt.datetime(y, m, d, 2, 0, tzinfo=CST) + dt.timedelta(days=1)
    else:
        base = dt.datetime(y, m, d, hour_cst, 0, tzinfo=CST)
    return int(base.timestamp())


def load_1h(cur, sym):
    cur.execute(
        """select timestamp, high_price, low_price, close_price from crypto_klines
           where symbol=%s and exchange='binance' and period='1h' and environment='mainnet'
           order by timestamp""", (sym,))
    rows = [(int(r[0]), float(r[1]), float(r[2]), float(r[3])) for r in cur.fetchall()]
    return rows, [x[0] for x in rows]


def ret_between(bars, t0, t1):
    seg = [x for x in bars if t0 <= x[0] < t1]
    if len(seg) < 2 or seg[0][3] <= 0:
        return None
    return (seg[-1][3] / seg[0][3] - 1.0) * 100


def realized_vol(bars, t0, t1):
    seg = [x for x in bars if t0 <= x[0] < t1]
    if len(seg) < 4:
        return None
    rs = []
    for i in range(1, len(seg)):
        if seg[i - 1][3] > 0:
            rs.append(abs(seg[i][3] / seg[i - 1][3] - 1.0) * 100)
    return st.mean(rs) * math.sqrt(len(rs)) if rs else None


def main() -> int:
    events = ([("FOMC", d, 2) for d in FOMC] + [("NFP", d, 20) for d in NFP]
              + [("CPI", d, 20) for d in CPI])
    events.sort(key=lambda x: x[1])
    with psycopg.connect(MARKET, autocommit=True) as mc:
        cur = mc.cursor()
        data = {}
        for s in ["BTC", "ETH"] + ALTS:
            bars, tss = load_1h(cur, s)
            if len(bars) > 1000:
                data[s] = (bars, tss)
        have = sorted(data)
        print("可用 1h 序列：%s" % ",".join(have))
        span0 = max(data[s][0][0][0] for s in have)
        span1 = min(data[s][0][-1][0] for s in have)
        print("共同区间：%s → %s"
              % (dt.datetime.fromtimestamp(span0, CST).strftime("%Y-%m-%d"),
                 dt.datetime.fromtimestamp(span1, CST).strftime("%Y-%m-%d")))

        # ── 1) 事件窗口 vs 基线 ──
        print("\n[1] 事件窗口已实现波动 vs 同日历基线（BTC / ETH，1h |收益| 均值×√n，24h 窗）：")
        base_windows = []
        for s in ("BTC", "ETH"):
            bars = data[s][0]
            t = bars[0][0]
            while t + 24 * H < bars[-1][0]:
                v = realized_vol(bars, t, t + 24 * H)
                if v is not None:
                    base_windows.append(v)
                t += 6 * H
        base_med = st.median(base_windows)
        print("    基线（滚动 24h 窗，每 6h 取样）中位波动 %.3f%%（n=%d）" % (base_med, len(base_windows)))
        print("    %-6s %-11s %-16s %9s %9s %9s %9s %9s"
              % ("类型", "日期", "CST", "前24h%", "后1h%", "后24h%", "后48h%", "波动比"))
        for kind, d, hh in events:
            ts = ev_ts(d, hh)
            if ts < span0 + 48 * H or ts > span1 - 48 * H:
                continue
            for s in ("BTC", "ETH"):
                bars = data[s][0]
                pre = ret_between(bars, ts - 24 * H, ts - H)
                h1 = ret_between(bars, ts - H, ts + H)
                p24 = ret_between(bars, ts, ts + 24 * H)
                p48 = ret_between(bars, ts, ts + 48 * H)
                vol = realized_vol(bars, ts - 12 * H, ts + 12 * H)
                if None in (pre, p24, p48, vol):
                    continue
                print("    %-6s %-11s %-16s %+9.2f %+9.2f %+9.2f %+9.2f %9.2f"
                      % (kind, d, dt.datetime.fromtimestamp(ts, CST).strftime("%m-%d %H:%M"),
                         pre, h1, p24, p48, vol / base_med))
            if kind == "FOMC" and d == "2026-09-16":
                print("    " + "-" * 88)

        # ── 2) 方向翻转概率 ──
        print("\n[2] 「变盘」检验：事件前24h 与 后24h 收益符号相反的比例（全部事件 × BTC/ETH/山寨）：")
        for kind in ("FOMC", "NFP", "CPI"):
            flip = tot = 0
            big_flip = big_tot = 0
            for k2, d, hh in events:
                if k2 != kind:
                    continue
                ts = ev_ts(d, hh)
                if ts < span0 + 48 * H or ts > span1 - 48 * H:
                    continue
                for s in have:
                    bars = data[s][0]
                    pre = ret_between(bars, ts - 24 * H, ts - H)
                    post = ret_between(bars, ts, ts + 24 * H)
                    if pre is None or post is None:
                        continue
                    tot += 1
                    if pre * post < 0:
                        flip += 1
                    if abs(pre) > 3.0:
                        big_tot += 1
                        if pre * post < 0:
                            big_flip += 1
            if tot:
                print("    %-5s 翻转 %d/%d = %.0f%%   其中「事件前已明显走一段(|前24h|>3%%)」时翻转 %d/%d = %.0f%%"
                      % (kind, flip, tot, 100.0 * flip / tot, big_flip, big_tot,
                         100.0 * big_flip / max(1, big_tot)))
        # 基线翻转率
        flip = tot = 0
        for s in have:
            bars = data[s][0]
            t = bars[0][0]
            while t + 48 * H < bars[-1][0]:
                pre = ret_between(bars, t, t + 24 * H)
                post = ret_between(bars, t + 24 * H, t + 48 * H)
                if pre is not None and post is not None:
                    tot += 1
                    if pre * post < 0:
                        flip += 1
                t += 24 * H
        if tot:
            print("    %-5s 翻转 %d/%d = %.0f%%（非事件日随机窗口基线）"
                  % ("基线", flip, tot, 100.0 * flip / tot))

        # ── 3) BTC/ETH 领先性 ──
        print("\n[3] BTC/ETH 领先性：1h 收益互相关（2026 全年，正 k = BTC 领先 k 小时）：")
        print("    %-10s %s" % ("币", "  ".join("k=%+d" % k for k in (-3, -2, -1, 0, 1, 2, 3))))
        for s in ["ETH"] + ALTS:
            if s not in data:
                continue
            btc = {t: c for t, _, _, c in data["BTC"][0]}
            alt = {t: c for t, _, _, c in data[s][0]}
            common = sorted(set(btc) & set(alt))
            if len(common) < 500:
                continue
            rb, ra = {}, {}
            for i in range(1, len(common)):
                t0, t1 = common[i - 1], common[i]
                if t1 - t0 != H or btc[t0] <= 0 or alt[t0] <= 0:
                    continue
                rb[t1] = btc[t1] / btc[t0] - 1.0
                ra[t1] = alt[t1] / alt[t0] - 1.0
            keys = sorted(set(rb) & set(ra))
            out = []
            for k in (-3, -2, -1, 0, 1, 2, 3):
                xs, ys = [], []
                for t in keys:
                    t2 = t + k * H
                    if t2 in ra:
                        xs.append(rb[t])
                        ys.append(ra[t2])
                if len(xs) < 200:
                    out.append("  n/a")
                    continue
                mx, my = st.mean(xs), st.mean(ys)
                num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
                den = math.sqrt(sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys))
                out.append("%+.2f" % (num / den if den else 0.0))
            print("    %-10s %s" % (s, "  ".join(out)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
