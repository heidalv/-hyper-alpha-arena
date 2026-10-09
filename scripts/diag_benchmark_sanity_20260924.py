# -*- coding: utf-8 -*-
"""[R1 诊断] 上一轮 cf_confirm_vs_benchmark 的越界样本与选择性偏差体检。

红旗：E 变体（做空）出现 ACE n=1 −22.78%，但 SL3%+追踪下空头最大亏损约 −2.76%。
本脚本：
  1) 用**同一份 sim 逻辑**（带探针：记录 exit 分支/entry/peak/sl/bar 数/时间）
     复现 E 变体，打印所有越界样本（空头 ret < −4% 或 多头 ret > 60%）；
  2) 打印样本池 09-15→now 各币涨幅，量化"按成交额选池"的选择性偏差；
  3) 检查 1m 数据质量（bar 数、时间跨度、high<low 的坏行）。
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
MARKET = "postgresql://laobao:alpha_pass@localhost:5432/alpha_market"
CST = dt.timezone(dt.timedelta(hours=8))
SINCE = int(dt.datetime(2026, 9, 15, 0, 0).replace(tzinfo=CST).timestamp())
FEE_PP, PEN_PP = 0.10, 0.237
SL_CAP, TRAIL_ACT, TRAIL_CB, MAX_HOLD_H = 3.0, 5.0, 2.5, 48.0


def ema(v, p):
    k = 2.0 / (p + 1.0)
    e = v[0]
    out = []
    for x in v:
        e = x * k + e * (1 - k)
        out.append(e)
    return out


def sim_probe(entry, bars, is_short):
    sign = -1.0 if is_short else 1.0
    sl = entry * (1 - sign * SL_CAP / 100.0)
    peak, reason, exit_ts = 0.0, "max_hold_end", None
    dead = bars[0][0] + int(MAX_HOLD_H * 3600)
    exit_px = None
    for ts, hi, lo, cl in bars:
        if ts > dead:
            exit_px, reason, exit_ts = cl, "past_deadline_close", ts
            break
        if peak >= TRAIL_ACT:
            cand = entry * (1 + sign * (peak - TRAIL_CB) / 100.0)
            sl = min(sl, cand) if is_short else max(sl, cand)
        adverse = hi if is_short else lo
        if (adverse >= sl) if is_short else (adverse <= sl):
            exit_px, reason, exit_ts = sl * (1 + sign * PEN_PP / 100.0), "stop", ts
            break
        ext = min(lo, cl) if is_short else max(hi, cl)
        r = ((entry - ext) / entry * 100.0) if is_short else ((ext - entry) / entry * 100.0)
        peak = max(peak, r)
    if exit_px is None:
        exit_px, exit_ts = bars[-1][3], bars[-1][0]
    ret = ((entry - exit_px) / entry * 100.0) if is_short else ((exit_px - entry) / entry * 100.0)
    return ret - FEE_PP, reason, entry, exit_px, exit_ts, peak, sl, len(bars)


def main() -> int:
    with psycopg.connect(MARKET, autocommit=True) as mc:
        cur = mc.cursor()
        cur.execute(
            """select symbol, sum(volume*close_price) amt from crypto_klines
               where exchange='binance' and period='4h' and environment='mainnet'
                 and timestamp >= %s group by symbol having count(*)>=50
               order by amt desc nulls last limit 40""", (SINCE - 40 * 86400,))
        syms = [r[0] for r in cur.fetchall()]
        print("池（%d）：%s" % (len(syms), ",".join(syms)))
        print("\n[1] 各币 09-15→now 涨幅（4h 收盘）——用于暴露选择偏差：")
        gains = []
        for s in syms:
            cur.execute(
                """select close_price from crypto_klines where symbol=%s and exchange='binance'
                   and period='4h' and environment='mainnet' and timestamp >= %s order by timestamp""",
                (s, SINCE))
            rows = cur.fetchall()
            if len(rows) < 20:
                continue
            gains.append((s, (float(rows[-1][0]) / float(rows[0][0]) - 1.0) * 100, len(rows)))
        for s, g, n in sorted(gains, key=lambda x: -x[1]):
            print("  %-10s %+9.2f%%  (%d bars)" % (s, g, n))
        if gains:
            print("  → 中位 %+.2f%%  均值 %+.2f%%  n=%d（池子是按成交额事后挑的，涨幅天然偏高）"
                  % (st.median([g for _, g, _ in gains]), st.mean([g for _, g, _ in gains]), len(gains)))

        print("\n[2] E 变体复现 + 越界样本（空头 ret < -4%）：")
        bad = 0
        for s in syms:
            cur.execute(
                """select timestamp, open_price, high_price, low_price, close_price, volume
                   from crypto_klines where symbol=%s and exchange='binance' and period='4h'
                     and environment='mainnet' order by timestamp""", (s,))
            bars = [{"ts": int(r[0]), "o": float(r[1]), "h": float(r[2]), "l": float(r[3]),
                     "c": float(r[4]), "v": float(r[5] or 0)} for r in cur.fetchall()]
            if len(bars) < 60:
                continue
            e50 = ema([b["c"] for b in bars], 50)
            for i, b in enumerate(bars):
                b["ema50"] = e50[i]
            idx = [i for i, b in enumerate(bars) if b["ts"] >= SINCE]
            if not idx:
                continue
            cur.execute(
                """select timestamp, high_price, low_price, close_price from crypto_klines
                   where symbol=%s and exchange='binance' and period='1m' and environment='mainnet'
                     and timestamp between %s and %s order by timestamp""",
                (s, bars[idx[0]]["ts"], bars[idx[-1]]["ts"] + int(MAX_HOLD_H * 3600) + 7200))
            p = [(int(r[0]), float(r[1]), float(r[2]), float(r[3])) for r in cur.fetchall()]
            if len(p) < 100:
                continue
            holes = sum(1 for a, b2 in zip(p, p[1:]) if b2[0] - a[0] > 120)
            swapped = sum(1 for x in p if x[1] < x[2])
            span_h = (p[-1][0] - p[0][0]) / 3600.0
            if holes > 5 or swapped > 0:
                print("  [数据] %-10s bars=%5d span=%.1fh 断档>2min=%d high<low坏行=%d"
                      % (s, len(p), span_h, holes, swapped))
            tss = [x[0] for x in p]
            last = -1e18
            for i, b in enumerate(bars):
                if b["ts"] < SINCE or i < 25 or b["ts"] - last < 6 * 3600:
                    continue
                hit = False
                for k in range(1, 5):
                    if i - k < 0:
                        break
                    if bars[i - k]["c"] < bars[i - k]["ema50"]:
                        hit = b["c"] < bars[i - 1]["l"]
                        break
                if not hit:
                    continue
                last = b["ts"]
                j = bisect.bisect_left(tss, b["ts"])
                k2 = bisect.bisect_left(tss, b["ts"] + int(MAX_HOLD_H * 3600))
                if k2 - j < 30:
                    continue
                seg = p[j:k2 + 1]
                ret, reason, entry, exit_px, exit_ts, peak, sl, nb = sim_probe(entry=seg[0][3], bars=seg, is_short=True)
                if ret < -4.0:
                    bad += 1
                    print("  [越界] %-8s 开 %s entry=%.6f → 平 %s exit=%.6f  ret=%+.2f%% reason=%s "
                          "peak=%.2f sl=%.6f bars=%d"
                          % (s, dt.datetime.fromtimestamp(b["ts"], CST).strftime("%m-%d %H:%M"), entry,
                             dt.datetime.fromtimestamp(exit_ts, CST).strftime("%m-%d %H:%M"), exit_px,
                             ret, reason, peak, sl, nb))
        if bad == 0:
            print("  未复现：说明上一轮 ACE −22.78% 不是 E 空头路径产生的（需查变体/方向串线）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
