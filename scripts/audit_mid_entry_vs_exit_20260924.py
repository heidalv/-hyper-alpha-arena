# -*- coding: utf-8 -*-
"""目标① 的根本问题：中线的亏损来自**入场**还是**出场**？

思路：对每一笔真实中线入场，算它的**前向收益路径**（6/12/24/48h 收盘对收盘，并用该仓
记录的硬 SL 做下界），再与**实际实现**的结果对比：

  - 若前向均值为正、而实际为负 ⇒ 问题在**出场**（有肉没吃到）→ 出场侧还有救；
  - 若前向均值本身为负 ⇒ 问题在**入场**（进场就是错的）→ 出场怎么改都没用。

为什么必须分 regime：R3/R5 已量出 mid·4h震荡 峰值中位仅 0.41%、均净 −7.26/笔，
而 mid·非震荡 −0.76/笔 ⇒ 两者病因可能不同。

**预登记验收（看结果前写定）：出场有救 = ①chop 桶 24h 或 48h 前向均值 > 0
    ②同一桶实际实现均值 < 0 ③前后半一致 ④binance/okx 双源一致**
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
FEE_PP = 0.10
HORIZONS = (6, 12, 24, 48)
K200 = 2.0 / 201.0


def fwd_ret(tss, closes, ts, hrs, sl_px):
    """前向收益%（收盘对收盘）；若区间内 low ≤ sl_px 则按止损价计（近似：取触达时点）。"""
    if not tss or tss[-1] < ts + hrs * H:
        return None
    j = bisect.bisect_left(tss, ts)
    k = bisect.bisect_left(tss, ts + hrs * H)
    if k >= len(tss) or closes[j] <= 0:
        return None
    exp = (tss[k] - tss[j]) / H
    if k - j < 3 or (exp > 0 and (k - j) / exp < 0.85):
        return None
    entry = closes[j]
    ret = (closes[k] / entry - 1.0) * 100.0
    if sl_px and sl_px > 0:
        # 分批近似：用 1h 序列检查是否先触 SL
        for m in range(j, k):
            if m < len(closes) and closes[m] <= sl_px:
                return (sl_px / entry - 1.0) * 100.0
    return ret


def main() -> int:
    with psycopg.connect(ARENA, autocommit=True) as ac, psycopg.connect(MARKET, autocommit=True) as mc:
        acur = ac.cursor(); acur.execute("SET app.is_admin='on'")
        acur.execute(
            """select symbol, timeframe_tier, entry_price, original_size, sl_price, close_price,
                      mark_price, status, unrealized_pnl, partial_realized_pnl, opened_at, peak_pnl_pct
               from paper_positions
               where account_id=14 and side='long' and opened_at >= %s order by opened_at""",
            (dt.datetime.fromtimestamp(SINCE, CST).replace(tzinfo=None),))
        rows = acur.fetchall()
        mcur = mc.cursor()
        h4cache: dict = {}
        for ex in ("binance", "okx"):
            book: dict = {}
            recs = []
            for sym, tier, entry, size, cpx, mark, slp, status, upnl, part, opened, peak in rows:
                tier = (tier or "").lower()
                if tier not in ("mid", "long") or not entry or not size:
                    continue
                if sym not in book:
                    mcur.execute(
                        """select timestamp, close_price from crypto_klines where symbol=%s and exchange=%s
                           and period='1h' and environment='mainnet' order by timestamp""", (sym, ex))
                    b = [(int(r[0]), float(r[1])) for r in mcur.fetchall()]
                    book[sym] = ([x[0] for x in b], [x[1] for x in b])
                if sym not in h4cache:
                    mcur.execute(
                        """select timestamp, close_price from crypto_klines where symbol=%s and exchange='binance'
                           and period='4h' and environment='mainnet' order by timestamp""", (sym,))
                    h = [(int(r[0]), float(r[1])) for r in mcur.fetchall()]
                    hts = [x[0] for x in h]; hcl = [x[1] for x in h]
                    k4 = 2.0 / 51.0
                    e4 = hcl[0] if hcl else 0.0
                    ema4 = [e4]
                    for v in hcl[1:]:
                        e4 = v * k4 + e4 * (1 - k4); ema4.append(e4)
                    h4cache[sym] = (hts, hcl, ema4)
                tss, closes = book[sym]
                t0 = int(opened.replace(tzinfo=CST).timestamp()) if opened.tzinfo is None else int(opened.timestamp())
                j = bisect.bisect_left(tss, t0)
                if j >= len(tss) - 6:
                    continue
                hts, hcl, ema4 = h4cache[sym]
                j4 = bisect.bisect_right(hts, t0) - 1
                chop = None
                if j4 >= 30 and ema4[j4] > 0 and hcl[j4 - 6] > 0:
                    mom24 = (hcl[j4] / hcl[j4 - 6] - 1.0) * 100
                    dist50 = (hcl[j4] / ema4[j4] - 1.0) * 100
                    chop = abs(mom24) < 2.0 and abs(dist50) < 1.5
                e = float(entry); s = float(size)
                px = float(cpx) if cpx else float(mark or entry)
                notional = abs(e * s)
                realized = ((px - e) * s + float(part or 0)) / notional * 100.0 - FEE_PP
                sl_px = None
                if slp:
                    try:
                        v = float(slp)
                        if 0 < v < e:
                            sl_px = v
                    except (TypeError, ValueError):
                        pass
                rec = {"sym": sym, "tier": tier, "t": t0, "realized": realized, "chop": chop,
                       "peak": float(peak or 0) * 100.0}
                for hz in HORIZONS:
                    rec["f%d" % hz] = fwd_ret(tss, closes, t0, hz, sl_px)
                # 4h 震荡标签（开仓时）
                recs.append(rec)
            print("\n" + "=" * 100)
            print("源=%s ｜ 单位：%%（前向=收盘对收盘；含 SL 下界近似；实现=含费）" % ex)
            print("  %-14s %5s %10s %10s %10s %10s %10s %10s"
                  % ("桶", "n", "实现%", "f6h%", "f12h%", "f24h%", "f48h%", "峰值中位%"))
            for tier in ("mid", "long"):
                for lab, filt in (("全部", lambda r: True),
                                  ("4h震荡", lambda r: r.get("chop")),
                                  ("非震荡", lambda r: r.get("chop") is False)):
                    seg = [r for r in recs if r["tier"] == tier and filt(r)]
                    if not seg:
                        continue
                    def m(key):
                        v = [r[key] for r in seg if r.get(key) is not None]
                        return st.mean(v) if v else float("nan")
                    print("  %-14s %5d %+10.3f %+10.3f %+10.3f %+10.3f %+10.3f %10.2f"
                          % ("%s·%s" % (tier, lab), len(seg), m("realized"), m("f6"), m("f12"),
                             m("f24"), m("f48"), st.median([r["peak"] for r in seg])))
            # 前后半
            cut = dt.datetime(2026, 9, 19, tzinfo=CST).timestamp()
            print("  修正后窗口（09-19 起）：")
            for tier in ("mid", "long"):
                seg = [r for r in recs if r["tier"] == tier and r["t"] >= cut]
                if not seg:
                    continue
                v24 = [r["f24"] for r in seg if r.get("f24") is not None]
                print("    %-5s n=%-3d 实现 %+.3f%% ｜ f24h %s ｜ f48h %s"
                      % (tier, len(seg), st.mean([r["realized"] for r in seg]),
                         ("%+.3f%%" % st.mean(v24)) if v24 else "n/a",
                         ("%+.3f%%" % st.mean([r["f48"] for r in seg if r.get("f48") is not None]))
                         if any(r.get("f48") is not None for r in seg) else "n/a"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
