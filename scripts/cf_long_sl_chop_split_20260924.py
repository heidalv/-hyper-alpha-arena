# -*- coding: utf-8 -*-
"""目标① 的"长线在震荡 regime 的可行做法"：**R8 放宽的止损，是否正好治的是长线的震荡桶？**

背景：
  - R5 量出**同一个 4h 震荡桶里 long 每笔 +5.27、mid 每笔 −7.26**，根因是峰值中位差 8 倍
    （long 3.43% vs mid 0.41%）；但 long·4h震荡只有 n=4 ⇒ 不足以做配置改动。
  - R8 用 09-15+/09-19 双样本重验把 **long 层 SL 上限 3% → 8%**（已落地并逐笔验收）。
  - 机制猜想：震荡里的长线最怕**噪声扫损**（结构止损被 3% 上限夹掉、趋势没展开就被打掉）
    ⇒ 若放宽止损在**震荡桶**的改善显著大于非震荡桶，则"长线在震荡的可行做法"就有了答案：
    **不是加闸，而是给长线足够宽的止损**。

做法：真实 long 入场点，生产 `ExitPolicy.for_lane("long")`，只改止损宽度 X∈{3,4.5,6,8}%，
**按开仓时 4h 震荡标签分桶**分别汇总（同口径：|mom24|<2% 且 |close/EMA50−1|<1.5%）。

**预登记验收（看结果前写定）**
  通过 = ①震荡桶在 6%/8% 档的 Δ（对 3%）**显著大于**非震荡桶
        ②震荡桶 8% 档净额由负转正 ③双样本方向一致 ④非震荡桶不明显变差
  任一不满足 → 只记录、不外推为"震荡专用结论"。
只读。
"""
from __future__ import annotations

import bisect
import datetime as dt
import io
import sys

import psycopg

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

from dotenv import load_dotenv  # noqa: E402

load_dotenv(".env", override=True)

from backend.services.exit.exit_policy import ExitPolicy, ExitSnapshot, evaluate  # noqa: E402

ARENA = "postgresql://laobao:alpha_pass@localhost:5432/alpha_arena"
MARKET = "postgresql://laobao:alpha_pass@localhost:5432/alpha_market"
CST = dt.timezone(dt.timedelta(hours=8))
H = 3600
FEE_PP = 0.10
WIDTHS = [3.0, 4.5, 6.0, 8.0]


def replay(pol, entry, size, sl_px, seg):
    sl = sl_px
    peak = 0.0
    t0 = seg[0][0]
    for t, hi, lo, c in seg:
        el = t - t0
        peak = max(peak, (hi - entry) / entry * 100.0)
        for cur in (lo, c):
            v = evaluate(pol, ExitSnapshot(side="long", entry=entry, current=cur,
                                           elapsed_sec=el, peak_roi_pct=peak, sl_price=sl))
            if v.action == "close":
                return (cur - entry) * size - entry * size * FEE_PP / 100.0
            if v.action == "tighten_sl" and v.new_sl:
                sl = max(sl or 0.0, float(v.new_sl))
        if sl and lo <= sl:
            return (sl - entry) * size - entry * size * FEE_PP / 100.0
    last = seg[-1][3]
    return (last - entry) * size - entry * size * FEE_PP / 100.0


def main() -> int:
    pol = ExitPolicy.for_lane("long")
    with psycopg.connect(ARENA, autocommit=True) as ac, psycopg.connect(MARKET, autocommit=True) as mc:
        acur = ac.cursor(); acur.execute("SET app.is_admin='on'")
        acur.execute(
            """select symbol, entry_price, original_size, opened_at
               from paper_positions
               where account_id=14 and side='long' and timeframe_tier='long' and opened_at >= %s
               order by opened_at""",
            (dt.datetime.fromtimestamp(int(dt.datetime(2026, 9, 15, tzinfo=CST).timestamp()), CST)
             .replace(tzinfo=None),))
        rows = acur.fetchall()
        mcur = mc.cursor()
        px: dict = {}; h4: dict = {}
        book = []
        for sym, entry, size, opened in rows:
            if not entry or not size:
                continue
            if sym not in px:
                mcur.execute(
                    """select timestamp, high_price, low_price, close_price from crypto_klines
                       where symbol=%s and exchange='binance' and period='1h' and environment='mainnet'
                       order by timestamp""", (sym,))
                b = [(int(r[0]), float(r[1]), float(r[2]), float(r[3])) for r in mcur.fetchall()]
                px[sym] = (b, [x[0] for x in b])
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
            bars, tss = px[sym]
            t0 = int(opened.replace(tzinfo=CST).timestamp()) if opened.tzinfo is None else int(opened.timestamp())
            j = bisect.bisect_left(tss, t0)
            if j >= len(tss) - 24:
                continue
            hts, hcl, ema4 = h4[sym]
            j4 = bisect.bisect_right(hts, t0) - 1
            chop = False
            if j4 >= 30 and ema4[j4] > 0 and hcl[j4 - 6] > 0:
                chop = (abs((hcl[j4] / hcl[j4 - 6] - 1.0) * 100) < 2.0
                        and abs((hcl[j4] / ema4[j4] - 1.0) * 100) < 1.5)
            book.append({"sym": sym, "e": float(entry), "s": float(size),
                         "chop": chop, "seg": bars[j:]})
        if not book:
            print("无样本"); return 0
        print("long 层（09-15 后）：%d 笔，其中 4h 震荡 %d 笔 / 非震荡 %d 笔"
              % (len(book), sum(1 for b in book if b["chop"]), sum(1 for b in book if not b["chop"])))
        out = {}
        for lab, filt in (("全部", lambda b: True), ("4h震荡", lambda b: b["chop"]),
                          ("非震荡", lambda b: not b["chop"])):
            seg = [b for b in book if filt(b)]
            if not seg:
                continue
            print("\n── %s（n=%d）──" % (lab, len(seg)))
            row = {}
            for w in WIDTHS:
                vals = [replay(pol, b["e"], b["s"], b["e"] * (1 - w / 100.0), b["seg"]) for b in seg]
                tot = sum(vals)
                row[w] = tot
                print("   %-6s 净额 %+9.2f ｜ 均 %+7.3f ｜ 最差 %+8.2f"
                      % ("%s%%" % w, tot, tot / len(seg), min(vals)))
            out[lab] = row
        if "4h震荡" in out and "非震荡" in out:
            print("\n── Δ（相对 3%）对比 ──")
            for w in WIDTHS[1:]:
                dc = out["4h震荡"][w] - out["4h震荡"][3.0]
                dn = out["非震荡"][w] - out["非震荡"][3.0]
                print("   %-6s 震荡桶 Δ %+8.2f ｜ 非震荡桶 Δ %+8.2f ｜ %s"
                      % ("%s%%" % w, dc, dn, "震荡改善更大" if dc > dn else "非震荡改善更大/震荡未更优"))
            for w in (6.0, 8.0):
                v = out["4h震荡"][w]
                print("   震荡桶 %s%% 净额 %+.2f ⇒ %s" % (w, v, "由负转正" if v > 0 else "仍为负"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
