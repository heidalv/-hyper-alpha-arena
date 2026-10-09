# -*- coding: utf-8 -*-
"""目标③/① 关键检验：learned 窄带闸（chg24 < +3% → 缩仓×0.25）**实际命中点**的前向结果。

为什么必须用"实际命中"：R1 的教训——位置闸我先用"全样本分档"得出"应放宽"，
换成"日志里真实命中点"后**完全反转**（命中点前向 24h 均 −4.4%、胜率 8%）。
所以对**任何入场闸**的评估，对照集必须是"该闸真实拦下的那些决策点"，不是所有时点。

本轮 LIVE 实测（重启后 8 小时）：stage=fuse 共 **776** 条决策，其中
**326 条（42%）**是 `midlong_long_learned_block`（learned 窄带，chg24<+3% 砍到 ×0.25），
位置闸只命中 1 次；且近期所有新仓都是 `size×0.25 (probe)`。
⇒ 这条闸就是"确认完只等待不开仓/开小仓"在实盘里的主要来源，必须用命中口径判它。

方法：
  - 解析 `logs/backend.log`：`paper_probe×0.25: midlong_long_learned_block:
    learned_long_up_chg24_<v><+3.0`，取 symbol / 时刻 / chg24 值；
  - 同一 symbol 30 分钟内折叠为一次决策；
  - 前向 = **24h 原始收盘对收盘**（与闸自己的标定口径一致），binance + okx 双源；
  - 对照 = 同期每根 4h 时点的同口径前向收益；
  - 再按 chg24 分档（深跌/小跌/小涨），看是否"下界一刀切"切错了地方。

**预登记验收（看结果前写定）**
  放宽下界的依据 = ①命中点前向均值**不低于**基线 ②chg24 ≥ −5% 这一段自身不为负
                  ③前后半一致 ④双源一致
只读。
"""
from __future__ import annotations

import bisect
import datetime as dt
import io
import re
import statistics as st
import sys

import psycopg

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
MARKET = "postgresql://laobao:alpha_pass@localhost:5432/alpha_market"
CST = dt.timezone(dt.timedelta(hours=8))
SINCE = int(dt.datetime(2026, 9, 15, tzinfo=CST).timestamp())
H = 3600
HORIZON_H = 24.0
LOG = r"logs\backend.log"
LINE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*?tier=(?P<tier>\w+) symbol=(?P<sym>[A-Z0-9]+)"
    r".*?learned_long_up_chg24_(?P<chg>[+-]?[0-9.]+)<")


def load(cur, ex, sym, t0, t1):
    cur.execute(
        """select timestamp, close_price from crypto_klines
           where symbol=%s and exchange=%s and period='1h' and environment='mainnet'
             and timestamp between %s and %s order by timestamp""", (sym, ex, t0, t1))
    rows = cur.fetchall()
    return ([float(r[1]) for r in rows], [int(r[0]) for r in rows])


def fwd(closes, tss, ts, hz):
    if not tss or tss[-1] < ts + hz * 3600:
        return None
    j = bisect.bisect_left(tss, ts)
    k = bisect.bisect_left(tss, ts + hz * 3600)
    if k >= len(tss):
        return None
    exp = (tss[k] - tss[j]) / 3600.0
    if k - j < 4 or (exp > 0 and (k - j) / exp < 0.85) or closes[j] <= 0:
        return None
    return (closes[k] / closes[j] - 1.0) * 100.0


def main() -> int:
    now = int(dt.datetime.now(CST).timestamp())
    hits = []
    with open(LOG, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            m = LINE.search(line)
            if not m:
                continue
            try:
                ts = int(dt.datetime.strptime(m.group("ts"), "%Y-%m-%d %H:%M:%S")
                         .replace(tzinfo=CST).timestamp())
            except ValueError:
                continue
            if ts < SINCE:
                continue
            hits.append({"ts": ts, "sym": m.group("sym"), "tier": m.group("tier"),
                         "chg": float(m.group("chg"))})
    print("learned 窄带闸命中行（09-15 起）：%d" % len(hits))
    if not hits:
        return 0
    hits.sort(key=lambda x: (x["sym"], x["ts"]))
    folded = []
    for h in hits:
        if folded and folded[-1]["sym"] == h["sym"] and h["ts"] - folded[-1]["ts"] < 1800:
            continue
        folded.append(h)
    print("折叠后决策点：%d（跨 %d 个币）" % (len(folded), len({h['sym'] for h in folded})))
    tiers = {}
    for h in folded:
        tiers[h["tier"]] = tiers.get(h["tier"], 0) + 1
    print("tier 分布：%s" % tiers)
    span = (min(h["ts"] for h in folded), max(h["ts"] for h in folded))
    print("时间跨度：%s → %s"
          % (dt.datetime.fromtimestamp(span[0], CST).strftime("%m-%d %H:%M"),
             dt.datetime.fromtimestamp(span[1], CST).strftime("%m-%d %H:%M")))

    with psycopg.connect(MARKET, autocommit=True) as mc:
        cur = mc.cursor()
        used = sorted({h["sym"] for h in folded})
        for ex in ("binance", "okx"):
            book = {}
            for s in used:
                closes, tss = load(cur, ex, s, min(span[0] - 6 * H, SINCE), now)
                if len(closes) > 200:
                    book[s] = (closes, tss)
            if not book:
                print("\n源=%s 无数据" % ex)
                continue
            base = []
            for s, (closes, tss) in book.items():
                for t in tss:
                    if t >= SINCE and (t - SINCE) % (4 * H) == 0:
                        r = fwd(closes, tss, t, HORIZON_H)
                        if r is not None:
                            base.append(r)
            bm = st.mean(base) if base else 0.0
            res = []
            for h in folded:
                if h["sym"] not in book:
                    continue
                closes, tss = book[h["sym"]]
                r = fwd(closes, tss, h["ts"], HORIZON_H)
                if r is None:
                    continue
                res.append({**h, "ret": r})
            print("\n" + "=" * 92)
            print("源=%s | 前向 %.0fh 原始收盘 | 可评估命中 %d/%d | 同期基线 %+.3f%%(n=%d)"
                  % (ex, HORIZON_H, len(res), len(folded), bm, len(base)))
            if not res:
                continue
            rets = [x["ret"] for x in res]
            print("  命中点整体（=闸拦下、只给 ×0.25 的那些）：均 %+.3f%%  对基线 %+.3f%%  胜率 %.0f%%"
                  % (st.mean(rets), st.mean(rets) - bm,
                     100.0 * sum(1 for x in rets if x > 0) / len(rets)))
            res.sort(key=lambda x: x["ts"])
            half = len(res) // 2
            print("  前半 均 %+.3f%%（n=%d）｜后半 均 %+.3f%%（n=%d）"
                  % (st.mean([x["ret"] for x in res[:half]]), half,
                     st.mean([x["ret"] for x in res[half:]]), len(res) - half))
            print("  %-22s %5s %10s %9s" % ("chg24 分档", "n", "均前向%", "对基线"))
            for lo, hi, lab in ((-100, -5, "深跌 <−5%"), (-5, -2, "−5%~−2%"),
                                (-2, 0, "−2%~0%"), (0, 3, "0%~+3%"), (3, 100, "≥+3%")):
                seg = [x["ret"] for x in res if lo <= x["chg"] < hi]
                if seg:
                    print("  %-22s %5d %+10.3f %+9.3f" % (lab, len(seg), st.mean(seg), st.mean(seg) - bm))
            print("  逐笔（全部）：")
            for x in res:
                print("    %s %-8s %-5s chg24=%+7.2f%%  前向 %+7.2f%%"
                      % (dt.datetime.fromtimestamp(x["ts"], CST).strftime("%m-%d %H:%M"),
                         x["sym"], x["tier"], x["chg"], x["ret"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
