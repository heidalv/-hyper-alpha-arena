# -*- coding: utf-8 -*-
"""目标① 可落地候选：**重验 `MIDLONG_SL_MAX_PCT_LONG`（long 层 SL 距离上限 = 3%）**。

为什么该重验：
  - `.env` L2108-2109 的依据写的是 **`[§88 执行 2026-09-11]`**
    （"§7 审计：SL 6.52% vs MAE 1.41%"）⇒ **属于用户「09-15 之前一律作废」的证据**；
  - 而 mid 层同一个键已在**第5轮用 09-15+ 双价源样本重验过**（1.5%→3.0%，SL×2.0 最优）；
  - 日志实测该上限正在**主动夹掉更宽的结构止损**：
    `SL 下限让位于层上限: tier=long floor 6.50%→3.00%`、`BNB tier=long sl 9.85%→3.00%`；
  - 且 long 是中唯一赚钱的车道（09-19 起 +1.040/笔 vs mid −0.159/笔），
    而它的**峰值中位 3.11% ≈ 3% 上限**，gross giveback 又主要发生在出场（R7：83%）。

做法：真实 long 入场点（09-15 后 18 笔），用**生产 long 出场策略**（`ExitPolicy.for_lane("long")`）
+ 硬止损 = 入场价 ×(1−X%) 扫 X ∈ {3,4.5,6,8}；同一批入场只改止损宽度。
mid 作为**对照**（它的 3% 是刚重验过的，若 mid 在 3% 最优则说明本方法可信）。

**预登记验收（看结果前写定，事后不得修改）**
  通过 = ①总净额改善 > 0 ②前后半**都不劣** ③最差 3 笔不劣 ④单币贡献 < 50%
        ⑤**两个样本（09-15 后 / 09-19 起）都通过**
  任一不满足 → 不落地。
只读。
"""
from __future__ import annotations

import bisect
import dataclasses
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


def _sum_by_sym(book, vals):
    out: dict = {}
    for b, v in zip(book, vals):
        out[b["sym"]] = out.get(b["sym"], 0.0) + v
    return out


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
    pm = ExitPolicy.for_lane("mid")
    pl = ExitPolicy.for_lane("long")
    print("生产策略：mid sl_pct=%s act=%s cb=%s tl=%s ｜ long sl_pct=%s act=%s cb=%s tl=%s"
          % (pm.sl_pct, pm.trailing_activation_pct, pm.trailing_callback_pct, pm.time_limit_sec,
             pl.sl_pct, pl.trailing_activation_pct, pl.trailing_callback_pct, pl.time_limit_sec))
    with psycopg.connect(ARENA, autocommit=True) as ac, psycopg.connect(MARKET, autocommit=True) as mc:
        acur = ac.cursor(); acur.execute("SET app.is_admin='on'")
        for label, since in (("09-15 后", "2026-09-15"), ("09-19 起", "2026-09-19")):
            acur.execute(
                """select symbol, timeframe_tier, entry_price, original_size, opened_at
                   from paper_positions
                   where account_id=14 and side='long' and timeframe_tier in ('mid','long')
                     and opened_at >= %s order by opened_at""", (since,))
            rows = acur.fetchall()
            mcur = mc.cursor()
            cache: dict = {}
            for tier in ("long", "mid"):
                book = []
                for sym, t, entry, size, opened in rows:
                    if (t or "").lower() != tier or not entry or not size:
                        continue
                    if sym not in cache:
                        mcur.execute(
                            """select timestamp, high_price, low_price, close_price from crypto_klines
                               where symbol=%s and exchange='binance' and period='1h' and environment='mainnet'
                               order by timestamp""", (sym,))
                        b = [(int(r[0]), float(r[1]), float(r[2]), float(r[3])) for r in mcur.fetchall()]
                        cache[sym] = (b, [x[0] for x in b])
                    bars, tss = cache[sym]
                    t0 = int(opened.replace(tzinfo=CST).timestamp()) if opened.tzinfo is None else int(opened.timestamp())
                    j = bisect.bisect_left(tss, t0)
                    if j >= len(tss) - 24:
                        continue
                    book.append({"sym": sym, "e": float(entry), "s": float(size), "seg": bars[j:]})
                if len(book) < 5:
                    print("\n%s ｜ %s：样本 %d 笔，过少，跳过" % (label, tier, len(book)))
                    continue
                pol = pl if tier == "long" else pm
                half = len(book) // 2
                print("\n" + "=" * 96)
                print("%s ｜ %s 层：%d 笔（对照：它的 3%% 是第5轮刚用 09-15+ 样本重验过的）"
                      % (label, tier, len(book)))
                print("  %-10s %10s %11s %11s %11s %11s" % ("止损宽度", "总净额$", "均净$/笔", "前半$", "后半$", "最差3笔$"))
                res = {}
                for w in WIDTHS:
                    vals = []
                    for b in book:
                        sl_px = b["e"] * (1 - w / 100.0)
                        vals.append(replay(pol, b["e"], b["s"], sl_px, b["seg"]))
                    tot = sum(vals)
                    res[w] = {"tot": tot, "h1": sum(vals[:half]), "h2": sum(vals[half:]),
                              "w3": sum(sorted(vals)[:3]), "per": _sum_by_sym(book, vals)}
                    print("  %-10s %+10.2f %+11.3f %+11.2f %+11.2f %+11.2f"
                          % ("%s%%" % w, tot, tot / len(book), res[w]["h1"], res[w]["h2"], res[w]["w3"]))
                b0 = res[3.0]
                print("  相对 3%%（现行）的判定：")
                for w in WIDTHS:
                    if w == 3.0:
                        continue
                    r = res[w]
                    d = r["tot"] - b0["tot"]; d1 = r["h1"] - b0["h1"]; d2 = r["h2"] - b0["h2"]
                    per = {k: r["per"][k] - b0["per"].get(k, 0.0) for k in r["per"]}
                    top = max(per.items(), key=lambda kv: abs(kv[1])) if per else ("-", 0.0)
                    conc = abs(top[1]) / abs(d) * 100 if d else 0.0
                    ok = (d > 0 and d1 >= 0 and d2 >= 0 and r["w3"] >= b0["w3"] and conc < 50)
                    print("    %-6s Δ总 %+8.2f ｜ Δ前 %+8.2f ｜ Δ后 %+8.2f ｜ Δ尾 %+8.2f ｜ 单币 %s %.0f%% ⇒ **%s**"
                          % ("%s%%" % w, d, d1, d2, r["w3"] - b0["w3"], top[0], conc,
                             "通过" if ok else "未通过"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
