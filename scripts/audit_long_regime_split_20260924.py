# -*- coding: utf-8 -*-
"""[R5 目标①] 长线（E1/trend）在震荡 regime 的实际表现 —— 补上目标①里"长线"这一半。

已有：中线在震荡桶 13 笔均净 −7.258/笔、峰值中位 0.41%、死单 69%（R3/R4）。
缺口：目标①写的是"中线/长线"，长线一侧从未按 regime 拆过。

本脚本：
  1) 长线仓（09-15 后开仓）按**开仓时**的日线 regime（up/chop/down）与 4h 震荡标签分桶；
  2) 每桶给 n / 毛盈亏 / 手续费 / 净额 / 均净 / 死单率 / 峰值中位；
  3) 同时给"若把中线的风险预算按桶期望重分配"的算术含义（只做算术，不改配置）。
口径：净额 = 毛盈亏 − 名义×0.10pp（双边 taker）；死单 = peak_pnl_pct(小数) < 0.01。
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
K200 = 2.0 / 201.0


def main() -> int:
    with psycopg.connect(ARENA, autocommit=True) as ac, psycopg.connect(MARKET, autocommit=True) as mc:
        acur = ac.cursor(); acur.execute("SET app.is_admin='on'")
        acur.execute(
            """select symbol, timeframe_tier, entry_price, original_size, close_price, mark_price,
                      status, unrealized_pnl, partial_realized_pnl, opened_at, peak_pnl_pct
               from paper_positions
               where account_id=14 and side='long' and opened_at >= %s
               order by opened_at""",
            (dt.datetime.fromtimestamp(SINCE, CST).replace(tzinfo=None),))
        rows = acur.fetchall()
        mcur = mc.cursor()
        dcache: dict = {}
        hcache: dict = {}
        recs = []
        for sym, tier, entry, size, cpx, mark, status, upnl, part, opened, peak in rows:
            tier = (tier or "").lower()
            if tier not in ("mid", "long") or not entry or not size:
                continue
            t0 = int(opened.replace(tzinfo=CST).timestamp()) if opened.tzinfo is None else int(opened.timestamp())
            if sym not in dcache:
                mcur.execute(
                    """select timestamp, close_price from crypto_klines where symbol=%s
                       and exchange='binance' and period='1d' and environment='mainnet' order by timestamp""",
                    (sym,))
                d = [(int(r[0]), float(r[1])) for r in mcur.fetchall()]
                if len(d) < 260:
                    dcache[sym] = None
                else:
                    dts = [x[0] for x in d]; dcl = [x[1] for x in d]
                    e = dcl[0]; ema = [e]
                    for v in dcl[1:]:
                        e = v * K200 + e * (1 - K200); ema.append(e)
                    dcache[sym] = (dts, dcl, ema)
            if sym not in hcache:
                mcur.execute(
                    """select timestamp, close_price from crypto_klines where symbol=%s
                       and exchange='binance' and period='4h' and environment='mainnet' order by timestamp""",
                    (sym,))
                h = [(int(r[0]), float(r[1])) for r in mcur.fetchall()]
                hts = [x[0] for x in h]; hcl = [x[1] for x in h]
                k = 2.0 / 51.0
                e4 = hcl[0] if hcl else 0.0
                ema4 = [e4]
                for v in hcl[1:]:
                    e4 = v * k + e4 * (1 - k); ema4.append(e4)
                hcache[sym] = (hts, hcl, ema4)
            dd = dcache.get(sym)
            if not dd:
                continue
            dts, dcl, ema = dd
            j = bisect.bisect_right(dts, t0) - 1
            if j < 200:
                continue
            # 日线 regime at entry（含形成中当日 bar：用 t0 时刻的 1h 收盘近似当时价）
            mcur.execute(
                """select close_price from crypto_klines where symbol=%s and exchange='binance'
                   and period='1h' and environment='mainnet' and timestamp <= %s
                   order by timestamp desc limit 1""", (sym, t0))
            r1 = mcur.fetchone()
            px = float(r1[0]) if r1 else dcl[j]
            ema_live = px * K200 + ema[j - 1] * (1 - K200)
            base = dcl[j - 60] if j - 60 >= 0 else dcl[0]
            mom = (px / base - 1.0) if base > 0 else 0.0
            dreg = "up" if (px > ema_live and mom > 0.05) else ("down" if (px < ema_live and mom < -0.05) else "chop")
            hts, hcl, ema4 = hcache[sym]
            j4 = bisect.bisect_right(hts, t0) - 1
            if j4 < 30:
                continue
            mom24 = (hcl[j4] / hcl[j4 - 6] - 1.0) * 100 if hcl[j4 - 6] > 0 else 0.0
            dist50 = (hcl[j4] / ema4[j4] - 1.0) * 100 if ema4[j4] > 0 else 0.0
            chop4 = abs(mom24) < 2.0 and abs(dist50) < 1.5
            p = float(cpx) if cpx else float(mark or entry)
            gross = (p - float(entry)) * float(size) + float(part or 0)
            notional = abs(float(entry) * float(size))
            fee = notional * FEE_PP / 100.0
            recs.append({"sym": sym, "tier": tier, "t": t0, "gross": gross, "fee": fee,
                         "net": gross - fee, "dreg": dreg, "chop4": chop4, "notional": notional,
                         "peak": float(peak or 0) * 100.0, "closed": status == "closed"})
        print("样本：09-15 后多头仓 %d 笔（mid %d / long %d）\n"
              % (len(recs), sum(1 for r in recs if r["tier"] == "mid"),
                 sum(1 for r in recs if r["tier"] == "long")))

        def show(title, group_key, groups):
            print("── %s ──" % title)
            print("  %-14s %5s %10s %9s %10s %10s %8s %9s"
                  % ("桶", "n", "毛盈亏$", "手续费$", "净额$", "均净$/笔", "死单率", "峰值中位"))
            for g in groups:
                seg = [r for r in recs if group_key(r) == g]
                if not seg:
                    continue
                gr = sum(x["gross"] for x in seg); fe = sum(x["fee"] for x in seg)
                ne = sum(x["net"] for x in seg)
                print("  %-14s %5d %+10.2f %9.2f %+10.2f %+10.3f %7.0f%% %8.2f%%"
                      % (g, len(seg), gr, fe, ne, ne / len(seg),
                         100.0 * sum(1 for x in seg if x["peak"] < 1.0) / len(seg),
                         st.median([x["peak"] for x in seg])))
            print()

        show("长线 · 按开仓时日线 regime", lambda r: ("long·" + r["dreg"]) if r["tier"] == "long" else None,
             ["long·up", "long·chop", "long·down"])
        show("中线 · 按开仓时日线 regime", lambda r: ("mid·" + r["dreg"]) if r["tier"] == "mid" else None,
             ["mid·up", "mid·chop", "mid·down"])
        show("按车道 × 4h 震荡标签", lambda r: "%s·%s" % (r["tier"], "4h震荡" if r["chop4"] else "非震荡"),
             ["long·4h震荡", "long·非震荡", "mid·4h震荡", "mid·非震荡"])

        # 组合视角：震荡桶里谁在赚钱
        chop = [r for r in recs if r["chop4"]]
        if chop:
            ch_l = sum(r["net"] for r in chop if r["tier"] == "long")
            ch_m = sum(r["net"] for r in chop if r["tier"] == "mid")
            print("震荡桶（4h 标签）合计：long %+.2f（n=%d） vs mid %+.2f（n=%d）"
                  % (ch_l, sum(1 for r in chop if r["tier"] == "long"),
                     ch_m, sum(1 for r in chop if r["tier"] == "mid")))
        # 只算修正后窗口
        cut = dt.datetime(2026, 9, 19, tzinfo=CST).timestamp()
        r2 = [r for r in recs if r["t"] >= cut]
        if r2:
            print("\n── 09-19 起（修正后）──")
            for t in ("mid", "long"):
                seg = [r for r in r2 if r["tier"] == t]
                if not seg:
                    continue
                ne = sum(x["net"] for x in seg)
                ch = [x for x in seg if x["chop4"]]
                nec = sum(x["net"] for x in ch)
                print("  %-5s n=%-3d 净 %+8.2f（均 %+.3f）｜其中 4h震荡 n=%-3d 净 %+8.2f"
                      % (t, len(seg), ne, ne / len(seg), len(ch), nec))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
