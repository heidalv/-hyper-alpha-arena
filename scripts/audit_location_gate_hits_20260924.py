# -*- coding: utf-8 -*-
"""[R1] 位置闸「实际命中」事后检验：把日志里真实发生的缩仓事件，用已落地出场栈重算前向收益。

为什么要用「实际命中」而不是「全样本分档」：
  位置闸的标定依据（`midlong_executor.py:555-558`）说的是"入场价落在 24h 区间
  80-100% 分位的那 33 笔，入场后 24h 均值 -3.89%、胜率 0.152"，即**真实开仓样本**。
  因此复核也必须用真实命中点，而不是所有 K 线时点。

口径：
  - 命中点来自 `logs/backend.log`：「位置闸 paper 缩仓×0.25（location_gate_veto: ...）」，
    含 symbol / tier / 分位 / 原因；同一 symbol 30 分钟内多次重复记录折叠为一次决策。
  - **前向收益用「原始收盘对收盘」**，因为位置闸自己的标定就是"入场后 24h 均值"，
    要 apples-to-apples 就必须同口径（而不是套用我们的出场栈）。
  - 主口径 24h（日志只覆盖到 09-22 23:35，48h 窗口大多未走完）；同时尽量给 48h。
  - 双源（binance / okx 1h），覆盖率闸 + 越界断言。
  - 对照组 = 同期「每根 4h 时点」的同口径前向收益均值。
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
SINCE = int(dt.datetime(2026, 9, 15, 0, 0).replace(tzinfo=CST).timestamp())
HORIZON_H = float(sys.argv[1]) if len(sys.argv) > 1 else 24.0
LOG = r"logs\backend.log"
LINE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*?tier=(?P<tier>\w+) symbol=(?P<sym>[A-Z0-9]+)"
    r".*?位置闸 paper 缩仓×[\d.]+（(?P<reason>[^）]*)")
POS = re.compile(r"分位(\d+)%")


def fwd_ret(closes, tss, ts, horizon_h):
    """原始收盘对收盘前向收益（%）。窗口不完整返回 None。"""
    if tss[-1] < ts + horizon_h * 3600:
        return None
    j = bisect.bisect_left(tss, ts)
    k = bisect.bisect_left(tss, ts + horizon_h * 3600)
    exp = (tss[k] - tss[j]) / 3600.0 if k < len(tss) else 0
    if k - j < 4 or (exp > 0 and (k - j) / exp < 0.85):
        return None
    if closes[j] <= 0:
        return None
    return (closes[k] / closes[j] - 1.0) * 100.0


def load_1h(cur, ex, sym, t0, t1):
    cur.execute(
        """select timestamp, close_price from crypto_klines
           where symbol=%s and exchange=%s and period='1h' and environment='mainnet'
             and timestamp between %s and %s order by timestamp""", (sym, ex, t0, t1))
    rows = cur.fetchall()
    return ([float(r[1]) for r in rows], [int(r[0]) for r in rows])


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
            p = POS.search(m.group("reason"))
            hits.append({"ts": ts, "sym": m.group("sym"), "tier": m.group("tier"),
                         "pos": int(p.group(1)) if p else None,
                         "reason": m.group("reason")[:70]})
    print("日志命中行（09-15 起）：%d" % len(hits))
    if not hits:
        return 0
    # 折叠：同 symbol 30 分钟内多次 = 一次决策
    hits.sort(key=lambda x: (x["sym"], x["ts"]))
    folded = []
    for h in hits:
        if folded and folded[-1]["sym"] == h["sym"] and h["ts"] - folded[-1]["ts"] < 1800:
            continue
        folded.append(h)
    print("折叠后决策点：%d" % len(folded))
    kinds = {}
    for h in folded:
        key = ("分位高" if "分位" in h["reason"] else
               "接刀" if "接刀" in h["reason"] else
               "追空" if "追空" in h["reason"] else "其它")
        kinds[key] = kinds.get(key, 0) + 1
    print("命中原因分布：%s" % kinds)
    tiers = {}
    for h in folded:
        tiers[h["tier"]] = tiers.get(h["tier"], 0) + 1
    print("命中 tier 分布：%s" % tiers)

    with psycopg.connect(MARKET, autocommit=True) as mc:
        cur = mc.cursor()
        used = sorted({h["sym"] for h in folded})
        for ex in ("binance", "okx"):
            book = {}
            for s in used:
                closes, tss = load_1h(cur, ex, s, SINCE - 6 * 3600, now)
                if len(closes) >= 100:
                    book[s] = (closes, tss)
            if not book:
                print("源=%s 无数据" % ex)
                continue
            # 对照：同期每根 4h 时点的同口径前向收益
            base = []
            for s, (closes, tss) in book.items():
                for t in tss:
                    if t < SINCE or (t - SINCE) % (4 * 3600) != 0:
                        continue
                    r = fwd_ret(closes, tss, t, HORIZON_H)
                    if r is not None:
                        base.append(r)
            bm = st.mean(base) if base else 0.0
            res = []
            for h in folded:
                if h["sym"] not in book:
                    continue
                closes, tss = book[h["sym"]]
                r = fwd_ret(closes, tss, h["ts"], HORIZON_H)
                if r is None:
                    continue
                res.append({**h, "ret": r})
            print("\n" + "=" * 92)
            print("源=%s | 前向口径 %.0fh 原始收盘 | 可评估命中点 %d/%d | 同期基准 %+.3f%%(n=%d)"
                  % (ex, HORIZON_H, len(res), len(folded), bm, len(base)))
            if not res:
                continue
            rets = [x["ret"] for x in res]
            print("  命中点整体：均 %+.3f%%  对基准 %+.3f%%  胜率 %.0f%%"
                  % (st.mean(rets), st.mean(rets) - bm,
                     100.0 * sum(1 for x in rets if x > 0) / len(rets)))
            print("  %-12s %4s %10s %9s %8s" % ("分位档", "n", "均/笔%", "对基准", "胜率"))
            for lo, hi, lab in ((40, 60, "40-60%"), (60, 80, "60-80%"), (80, 101, "≥80%")):
                seg = [x["ret"] for x in res if x["pos"] is not None and lo <= x["pos"] < hi]
                if seg:
                    print("  %-12s %4d %+10.3f %+9.3f %7.0f%%"
                          % (lab, len(seg), st.mean(seg), st.mean(seg) - bm,
                             100.0 * sum(1 for y in seg if y > 0) / len(seg)))
            print("  按 tier：")
            for t in sorted({x["tier"] for x in res}):
                seg = [x["ret"] for x in res if x["tier"] == t]
                print("    %-6s n=%-3d 均 %+.3f%%  对基准 %+.3f%%  胜率 %.0f%%"
                      % (t, len(seg), st.mean(seg), st.mean(seg) - bm,
                         100.0 * sum(1 for y in seg if y > 0) / len(seg)))
            print("  逐笔明细（全部）：")
            for x in sorted(res, key=lambda y: y["ts"]):
                print("    %s %-8s %-5s pos=%-4s %+7.2f%%  %s"
                      % (dt.datetime.fromtimestamp(x["ts"], CST).strftime("%m-%d %H:%M"),
                         x["sym"], x["tier"], x["pos"], x["ret"], x["reason"][:40]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
