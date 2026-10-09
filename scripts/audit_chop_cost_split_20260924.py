# -*- coding: utf-8 -*-
"""[R3 目标①] 震荡 regime 里的中线：到底是"没信号"还是"信号被成本吃掉"？

第 17 轮已用 walk-forward 否掉"4h 震荡不入场"这个闸；第 20 轮指出中线成本占毛利 41%。
本轮把两件事合起来算：**按开仓时的 4h 震荡标签分桶，看毛盈亏/手续费/净额**，
回答"震荡里可行的做法是什么"。

口径：
  - 样本：模拟账户 14、09-15 后开仓的 mid 仓（closed + open）。
  - 4h 震荡标签（与第 17 轮同口径）：|mom24| < 2% **且** |close/EMA50 − 1| < 1.5%。
  - 毛盈亏 = (close−entry)×size（open 用 mark）、已实现部分；手续费 = 按该笔入场名义 × 双边 0.10pp 估。
  - 同时给"死单率"（峰值 <1%，用 peak_pnl_pct 列）。
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


def main() -> int:
    with psycopg.connect(ARENA, autocommit=True) as ac, psycopg.connect(MARKET, autocommit=True) as mc:
        acur = ac.cursor(); acur.execute("SET app.is_admin='on'")
        acur.execute(
            """select symbol, entry_price, original_size, close_price, mark_price, status,
                      unrealized_pnl, partial_realized_pnl, opened_at, peak_pnl_pct
               from paper_positions
               where account_id=14 and side='long' and timeframe_tier='mid' and opened_at >= %s
               order by opened_at""",
            (dt.datetime.fromtimestamp(SINCE, CST).replace(tzinfo=None),))
        rows = acur.fetchall()
        mcur = mc.cursor()
        cache: dict = {}
        buckets = {"chop": [], "nonchop": []}
        for sym, entry, size, cpx, mark, status, upnl, part, opened, peak in rows:
            if not entry or not size:
                continue
            if sym not in cache:
                mcur.execute(
                    """select timestamp, open_price, high_price, low_price, close_price
                       from crypto_klines where symbol=%s and exchange='binance' and period='4h'
                         and environment='mainnet' order by timestamp""", (sym,))
                bars = [(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]))
                        for r in mcur.fetchall()]
                closes = [b[4] for b in bars]
                k = 2.0 / 51.0
                e = closes[0]
                ema = [e]
                for v in closes[1:]:
                    e = v * k + e * (1 - k)
                    ema.append(e)
                cache[sym] = (bars, ema)
            bars, ema = cache[sym]
            t0 = int(opened.replace(tzinfo=CST).timestamp()) if opened.tzinfo is None else int(opened.timestamp())
            j = bisect.bisect_right([b[0] for b in bars], t0) - 1
            if j < 26:
                continue
            mom24 = (bars[j][4] / bars[j - 6][4] - 1.0) * 100 if bars[j - 6][4] > 0 else 0.0
            dist50 = (bars[j][4] / ema[j] - 1.0) * 100 if ema[j] > 0 else 0.0
            is_chop = abs(mom24) < 2.0 and abs(dist50) < 1.5
            px = float(cpx) if cpx else float(mark or entry)
            gross = (px - float(entry)) * float(size) + float(part or 0)
            fee = abs(float(entry) * float(size)) * FEE_PP / 100.0
            # [修正] `peak_pnl_pct` 存的是**小数**（实测 ASTER #4684 = 0.012244 → 1.22%），
            # 不是百分点；旧写法 `peak < 1.0` 恒真 ⇒ 死单率假报 100%。
            _pk = float(peak or 0)
            buckets["chop" if is_chop else "nonchop"].append(
                {"sym": sym, "gross": gross, "fee": fee, "net": gross - fee,
                 "dead": (_pk < 0.01), "peak_pct": _pk * 100.0})
        print("09-15 后 mid 仓 %d 笔（含未平），按开仓时 4h 震荡标签分桶：\n" % len(rows))
        print("  %-9s %5s %10s %9s %10s %10s %9s %9s %9s"
              % ("桶", "n", "毛盈亏$", "手续费$", "净额$", "均净$/笔", "成本/毛", "死单率", "峰值中位"))
        for key, label in (("chop", "4h震荡"), ("nonchop", "非震荡")):
            v = buckets[key]
            if not v:
                continue
            g = sum(x["gross"] for x in v); f = sum(x["fee"] for x in v); n_ = sum(x["net"] for x in v)
            ratio = (f / abs(g) * 100) if g else float("nan")
            print("  %-9s %5d %+10.2f %9.2f %+10.2f %+10.3f %8.0f%% %8.0f%% %8.2f%%"
                  % (label, len(v), g, f, n_, n_ / len(v), ratio,
                     100.0 * sum(1 for x in v if x["dead"]) / len(v),
                     st.median([x["peak_pct"] for x in v])))
        allv = buckets["chop"] + buckets["nonchop"]
        if allv:
            g = sum(x["gross"] for x in allv); f = sum(x["fee"] for x in allv); n_ = sum(x["net"] for x in allv)
            print("  %-9s %5d %+10.2f %9.2f %+10.2f %+10.3f %8.0f%% %8.0f%% %8.2f%%"
                  % ("合计", len(allv), g, f, n_, n_ / len(allv), f / abs(g) * 100 if g else 0,
                     100.0 * sum(1 for x in allv if x["dead"]) / len(allv),
                     st.median([x["peak_pct"] for x in allv])))
        # 每笔需要多大的毛收益才能覆盖手续费
        if allv:
            med_fee = st.median([x["fee"] for x in allv])
            med_notional = med_fee / (FEE_PP / 100.0)
            print("\n  中位单笔手续费 $%.3f（≈名义 $%.0f × 0.10pp）" % (med_fee, med_notional))
            print("  ⇒ 若单笔毛收益中位达不到该值，中线在震荡里就是给手续费打工。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
