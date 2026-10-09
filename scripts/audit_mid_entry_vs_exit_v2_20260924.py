# -*- coding: utf-8 -*-
"""目标① 根本问题（v2，加对照与正确的 SL 判定）：中线亏损来自**入场**还是**出场**？

v1 的两处问题（自查）：
  1) SL 判定用了 1h **收盘**序列而不是**最低价** ⇒ 低估止损触发、高估前向收益；
  2) 只报了前向绝对收益，**没有同期无条件基线** —— 在上涨窗口里任何入场的前向都是正的，
     这正是 R2 踩过的坑（把 beta 当 alpha）。本版必须给"超额"。

本版口径：
  - 前向收益：从入场时刻起 h 小时的**收盘对收盘**；若期间**最低价**触及该仓硬 SL → 按 SL 计；
  - **对照基线**：同一批币、同一窗口内**每根 4h 时点**的同口径前向收益（无信号、无条件）；
  - 超额 = 该桶前向均值 − 基线均值；实现值同样与基线比较；
  - 分 regime（4h 震荡标签）与前后半；binance/okx 双源。

**预登记验收（看结果前写定）：出场有救 = ①chop 桶 f24h 或 f48h **超额** > 0
    ②同一桶实现值 < 0（相对基线更负）③前后半一致 ④双源一致**
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


def fwd(tss, hi, lo, cl, ts, hrs, sl_px):
    """前向收益%（收盘对收盘；期间最低价触及 sl_px 则按 SL 计）。"""
    if not tss or tss[-1] < ts + hrs * H:
        return None
    j = bisect.bisect_left(tss, ts)
    k = bisect.bisect_left(tss, ts + hrs * H)
    if k >= len(tss) or cl[j] <= 0:
        return None
    exp = (tss[k] - tss[j]) / H
    if k - j < 3 or (exp > 0 and (k - j) / exp < 0.85):
        return None
    entry = cl[j]
    if sl_px and sl_px > 0:
        for m in range(j, k):
            if lo[m] <= sl_px:
                return (sl_px / entry - 1.0) * 100.0
    return (cl[k] / entry - 1.0) * 100.0


def main() -> int:
    with psycopg.connect(ARENA, autocommit=True) as ac, psycopg.connect(MARKET, autocommit=True) as mc:
        acur = ac.cursor(); acur.execute("SET app.is_admin='on'")
        acur.execute(
            """select symbol, timeframe_tier, entry_price, original_size, sl_price, close_price,
                      mark_price, partial_realized_pnl, opened_at, peak_pnl_pct
               from paper_positions
               where account_id=14 and side='long' and opened_at >= %s order by opened_at""",
            (dt.datetime.fromtimestamp(SINCE, CST).replace(tzinfo=None),))
        rows = acur.fetchall()
        mcur = mc.cursor()
        for ex in ("binance", "okx"):
            px: dict = {}
            for sym in {r[0] for r in rows}:
                mcur.execute(
                    """select timestamp, high_price, low_price, close_price from crypto_klines
                       where symbol=%s and exchange=%s and period='1h' and environment='mainnet'
                       order by timestamp""", (sym, ex))
                b = [(int(r[0]), float(r[1]), float(r[2]), float(r[3])) for r in mcur.fetchall()]
                if len(b) > 200:
                    px[sym] = ([x[0] for x in b], [x[1] for x in b], [x[2] for x in b], [x[3] for x in b])
            # 4h 震荡标签缓存
            h4: dict = {}
            for sym in px:
                mcur.execute(
                    """select timestamp, close_price from crypto_klines where symbol=%s
                       and exchange='binance' and period='4h' and environment='mainnet' order by timestamp""",
                    (sym,))
                h = [(int(r[0]), float(r[1])) for r in mcur.fetchall()]
                hts = [x[0] for x in h]; hcl = [x[1] for x in h]
                k4 = 2.0 / 51.0
                e4 = hcl[0] if hcl else 0.0
                ema4 = [e4]
                for v in hcl[1:]:
                    e4 = v * k4 + e4 * (1 - k4); ema4.append(e4)
                h4[sym] = (hts, hcl, ema4)
            # ── 无条件基线：同币、同窗口、每根 4h 时点 ──
            base = {hz: [] for hz in HORIZONS}
            for sym, (tss, hi, lo, cl) in px.items():
                for t in tss:
                    if t < SINCE or (t - SINCE) % (4 * H) != 0:
                        continue
                    for hz in HORIZONS:
                        v = fwd(tss, hi, lo, cl, t, hz, None)
                        if v is not None:
                            base[hz].append(v)
            base_m = {hz: (st.mean(v) if v else float("nan")) for hz, v in base.items()}

            recs = []
            for sym, tier, entry, size, slp, cpx, mark, part, opened, peak in rows:
                tier = (tier or "").lower()
                if tier not in ("mid", "long") or not entry or not size or sym not in px:
                    continue
                tss, hi, lo, cl = px[sym]
                t0 = int(opened.replace(tzinfo=CST).timestamp()) if opened.tzinfo is None else int(opened.timestamp())
                j = bisect.bisect_left(tss, t0)
                if j >= len(tss) - 6:
                    continue
                hts, hcl, ema4 = h4[sym]
                j4 = bisect.bisect_right(hts, t0) - 1
                chop = None
                if j4 >= 30 and ema4[j4] > 0 and hcl[j4 - 6] > 0:
                    chop = (abs((hcl[j4] / hcl[j4 - 6] - 1.0) * 100) < 2.0
                            and abs((hcl[j4] / ema4[j4] - 1.0) * 100) < 1.5)
                e = float(entry); s = float(size)
                p = float(cpx) if cpx else float(mark or entry)
                notional = abs(e * s)
                realized = ((p - e) * s + float(part or 0)) / notional * 100.0 - FEE_PP
                sl_px = None
                if slp:
                    try:
                        v = float(slp)
                        if 0 < v < e:
                            sl_px = v
                    except (TypeError, ValueError):
                        pass
                rec = {"tier": tier, "t": t0, "realized": realized, "chop": chop,
                       "peak": float(peak or 0) * 100.0}
                for hz in HORIZONS:
                    rec["f%d" % hz] = fwd(tss, hi, lo, cl, t0, hz, sl_px)
                recs.append(rec)
            print("\n" + "=" * 104)
            print("源=%s ｜ 无条件基线（每根4h时点）：f6 %+.3f%% f12 %+.3f%% f24 %+.3f%% f48 %+.3f%%"
                  % (ex, base_m[6], base_m[12], base_m[24], base_m[48]))
            print("  %-15s %5s %10s %12s %12s %12s %12s"
                  % ("桶", "n", "实现%", "f24h超额", "f48h超额", "f24h绝对", "f48h绝对"))
            for tier in ("mid", "long"):
                for lab, filt in (("全部", lambda r: True), ("4h震荡", lambda r: r["chop"] is True),
                                  ("非震荡", lambda r: r["chop"] is False)):
                    seg = [r for r in recs if r["tier"] == tier and filt(r)]
                    if not seg:
                        continue
                    def m(key):
                        v = [r[key] for r in seg if r.get(key) is not None]
                        return st.mean(v) if v else float("nan")
                    print("  %-15s %5d %+10.3f %+12.3f %+12.3f %+12.3f %+12.3f"
                          % ("%s·%s" % (tier, lab), len(seg), m("realized"),
                             m("f24") - base_m[24], m("f48") - base_m[48], m("f24"), m("f48")))
            cut = dt.datetime(2026, 9, 19, tzinfo=CST).timestamp()
            print("  09-19 起：")
            for tier in ("mid", "long"):
                seg = [r for r in recs if r["tier"] == tier and r["t"] >= cut]
                if not seg:
                    continue
                def m2(key):
                    v = [r[key] for r in seg if r.get(key) is not None]
                    return st.mean(v) if v else float("nan")
                print("    %-5s n=%-3d 实现 %+.3f%% ｜ f24 超额 %+.3f%% ｜ f48 超额 %+.3f%%"
                      % (tier, len(seg), m2("realized"),
                         m2("f24") - base_m[24], m2("f48") - base_m[48]))
            # 越界自查：实现值不该超出物理范围
            bad = [r for r in recs if r["realized"] < -20 or r["realized"] > 200]
            if bad:
                print("  ⚠ 越界样本 %d 笔（|实现|异常）" % len(bad))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
