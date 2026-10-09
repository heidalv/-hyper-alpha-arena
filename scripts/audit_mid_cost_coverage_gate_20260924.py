# -*- coding: utf-8 -*-
"""目标① 的正向回答：能不能用**事前可观测的波动/预期波幅**把中线单笔毛利抬到覆盖手续费？

动机（R3 实测）：
  - 中线 09-15 后净亏 −170.60，其中手续费 50.49；中位单笔手续费 **$0.68**（名义≈$680 × 0.10pp）；
  - 震荡桶峰值中位仅 **0.41%**、69% 死单 ⇒ "没有波动可赚"时，费用与止损照付；
  - 第 17 轮已否掉"regime 闸"、第 20 轮指出成本占毛利 41% ⇒ 本轮换一个**不同机制**的闸：
    **成本覆盖率**（预期波幅 / 手续费），不是 regime、不是价格位置。

两个候选闸（都用**入场时点**可得的信息，无前视）：
  G1 绝对波动：入场时 4h ATR%(14) ≥ 阈值
  G2 相对波动：入场时 4h ATR%(14) ÷ 该币在本窗口的中位 ATR% ≥ 阈值
     （G2 用币内相对值，避免"只是选中了当期涨得多的币"）

**预登记验收标准（看结果前写定，事后不得修改）**
  通过 = ①高波动桶净值为正 **且** 低波动桶净值为负
        ②前后半样本方向一致（高桶在前后半都优于低桶）
        ③过滤掉的比例 < 60%（不能靠砍掉大半成交来粉饰）
        ④G1 与 G2 方向一致
  任一不满足 → 不落地。
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
FEE_PP = 0.10      # 双边合计（taker 0.05% × 2）
ATR_N = 14


def atr_pct(bars, i, n=ATR_N):
    """4h ATR%(n)：用 TR 均值 / 收盘价。i 为**信号 bar 索引**，只用 i 及之前的数据。"""
    if i < n:
        return None
    trs = []
    for k in range(i - n + 1, i + 1):
        hi, lo, pc = bars[k][2], bars[k][3], bars[k - 1][4]
        trs.append(max(hi - lo, abs(hi - pc), abs(lo - pc)))
    c = bars[i][4]
    if c <= 0:
        return None
    return st.mean(trs) / c * 100.0


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
        trades = []
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
                cache[sym] = (bars, [b[0] for b in bars])
            bars, tss = cache[sym]
            t0 = int(opened.replace(tzinfo=CST).timestamp()) if opened.tzinfo is None else int(opened.timestamp())
            # 入场时**最后一根已收盘**的 4h bar（无前视：bar 收盘 ≤ 入场时刻）
            j = bisect.bisect_right(tss, t0) - 1
            if j < ATR_N + 1:
                continue
            a = atr_pct(bars, j)
            if a is None:
                continue
            px = float(cpx) if cpx else float(mark or entry)
            notional = abs(float(entry) * float(size))
            gross = (px - float(entry)) * float(size) + float(part or 0)
            fee = notional * FEE_PP / 100.0
            trades.append({"sym": sym, "t": t0, "atr": a, "net": gross - fee,
                           "gross": gross, "fee": fee,
                           "peak": float(peak or 0) * 100.0,
                           "closed": bool(status == "closed")})
        if not trades:
            print("无样本"); return 0
        trades.sort(key=lambda x: x["t"])
        # 每币中位 ATR% → G2 相对波动
        med = {}
        for s in {t["sym"] for t in trades}:
            v = [t["atr"] for t in trades if t["sym"] == s]
            med[s] = st.median(v) if v else 0.0
        for t in trades:
            t["rel"] = (t["atr"] / med[t["sym"]]) if med.get(t["sym"]) else 0.0
        n = len(trades)
        tot_net = sum(t["net"] for t in trades)
        print("09-15 后 mid 仓 %d 笔（含未平）；合计净额 %+.2f；中位手续费 $%.3f；"
              "中位入场 ATR%%=%.2f" % (n, tot_net, st.median([t["fee"] for t in trades]),
                                       st.median([t["atr"] for t in trades])))

        def bucket_report(key: str, label: str, edges) -> None:
            print("\n  ── %s 分档 ──" % label)
            print("    %-16s %5s %10s %10s %9s %8s %9s"
                  % ("档", "n", "净额$", "均净$/笔", "胜率", "死单率", "峰值中位"))
            for lo, hi, name in edges:
                seg = [t for t in trades if lo <= t[key] < hi]
                if not seg:
                    continue
                net = sum(t["net"] for t in seg)
                print("    %-16s %5d %+10.2f %+10.3f %7.0f%% %7.0f%% %8.2f%%"
                      % (name, len(seg), net, net / len(seg),
                         100.0 * sum(1 for t in seg if t["net"] > 0) / len(seg),
                         100.0 * sum(1 for t in seg if t["peak"] < 1.0) / len(seg),
                         st.median([t["peak"] for t in seg])))

        q = [st.quantiles([t["atr"] for t in trades], n=5)[i] for i in range(4)]
        bucket_report("atr", "G1 绝对 ATR%（五分位）", [
            (0, q[0], "Q1 最低(<%.2f)" % q[0]), (q[0], q[1], "Q2"), (q[1], q[2], "Q3"),
            (q[2], q[3], "Q4"), (q[3], 1e9, "Q5 最高(≥%.2f)" % q[3])])
        r = [st.quantiles([t["rel"] for t in trades], n=5)[i] for i in range(4)]
        bucket_report("rel", "G2 相对波动（÷币内中位）", [
            (0, r[0], "Q1 最低(<%.2f)" % r[0]), (r[0], r[1], "Q2"), (r[1], r[2], "Q3"),
            (r[2], r[3], "Q4"), (r[3], 1e9, "Q5 最高(≥%.2f)" % r[3])])

        # 候选阈值：只为"高波动桶净值为正、低波动桶为负"服务，取中位数作为闸
        print("\n  ── 以中位数为闸的对照（通过=高桶为正且低桶为负）──")
        for key, label in (("atr", "G1 绝对ATR%"), ("rel", "G2 相对波动")):
            m = st.median([t[key] for t in trades])
            hi = [t for t in trades if t[key] >= m]
            lo = [t for t in trades if t[key] < m]
            hn = sum(t["net"] for t in hi); ln = sum(t["net"] for t in lo)
            half = len(trades) // 2
            h1h = sum(t["net"] for t in hi if t["t"] <= trades[half]["t"])
            h1l = sum(t["net"] for t in lo if t["t"] <= trades[half]["t"])
            h2h = sum(t["net"] for t in hi if t["t"] > trades[half]["t"])
            h2l = sum(t["net"] for t in lo if t["t"] > trades[half]["t"])
            passed = (hn > 0 and ln < 0 and (h1h > h1l) and (h2h > h2l))
            print("    %-12s 闸=%.3f  高桶 n=%-3d 净 %+8.2f (均 %+.3f) | 低桶 n=%-3d 净 %+8.2f (均 %+.3f)"
                  % (label, m, len(hi), hn, hn / max(1, len(hi)), len(lo), ln, ln / max(1, len(lo))))
            print("                 前半 高%+.2f vs 低%+.2f ；后半 高%+.2f vs 低%+.2f ⇒ %s"
                  % (h1h, h1l, h2h, h2l, "方向一致" if (h1h > h1l and h2h > h2l) else "**方向不一致**"))
            print("                 过滤比例 %.0f%%（<60%% 才可接受）⇒ %s"
                  % (100.0 * len(lo) / len(trades), "OK" if len(lo) / len(trades) < 0.60 else "过多"))
            print("                 判定：**%s**" % ("通过" if passed else "未通过"))
        # 覆盖手续费所需的最小波幅
        med_fee_pct = st.median([t["fee"] / (t["fee"] / (FEE_PP / 100.0)) for t in trades]) if trades else 0
        print("\n  参考：手续费 = 名义 × %.2f%% ⇒ 单笔毛收益必须 > 0.10%% 才不亏手续费；"
              "当前中位峰值 %.2f%%、中位 ATR%% %.2f"
              % (FEE_PP, st.median([t["peak"] for t in trades]), st.median([t["atr"] for t in trades])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
