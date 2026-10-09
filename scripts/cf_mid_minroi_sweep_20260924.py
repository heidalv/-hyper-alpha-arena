# -*- coding: utf-8 -*-
"""目标① 未测过的一条出场杠杆：**mid 的 `min_roi` 时间递减档 + time_limit**。

为什么这条值得测：
  - R4 扫过追踪参数（6 档），但**没扫 `min_roi` / `time_limit`**；
  - 生产 mid 策略实测：`min_roi=((43200, 0.5), (86400, 0.0))`、`time_limit_sec=172800`
    ⇒ **满 12h 若 ROI<+0.5% 强平、满 24h 若 ROI<0 强平**；
  - 而 mid 的中位峰值只有 0.88%（R4）⇒ "在 12/24h 还没走出来的仓"会被这两档**成批砍掉**，
    与 §17 量到的"峰值捕获率 ≈21%"、R5 量到的"mid 峰值中位 0.41~1.06% vs long 3.11~3.43%"
    是同一个机制的不同侧面；
  - 且这是**加法**（让仓位多活一会儿），不是压制开仓，符合用户"不要以怕亏钱为由压制"的口径。

引擎一律调用生产 `ExitPolicy` + `evaluate()`（R3 教训：不得重写出场逻辑）；入场点=真实中线仓。

**预登记验收标准（看结果前写定，事后不得修改）**
  通过 = ①总净额改善 > 0 ②前后半**都**改善 ③最差 3 笔不变差 ④单币贡献 < 50%
        ⑤**两个样本（09-15 后 / 09-19 起）都通过**（防止只对含存量的旧样本有效）
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
FEE_PP = 0.10
VARIANTS = [
    ("基线 min_roi+t48h", ((43200, 0.5), (86400, 0.0)), 172800),
    ("去24h档", ((43200, 0.5),), 172800),
    ("无 min_roi", (), 172800),
    ("放宽(0/-1)", ((43200, 0.0), (86400, -1.0)), 172800),
    ("档位后移(24h/48h)", ((86400, 0.5), (172800, 0.0)), 172800),
    ("基线+持72h", ((43200, 0.5), (86400, 0.0)), 259200),
    ("无min_roi+持72h", (), 259200),
]


def _sum_by_sym(book, vals):
    """按币**求和**（同一币可能有多笔），供单币集中度判据使用。"""
    out: dict = {}
    for b, v in zip(book, vals):
        out[b["sym"]] = out.get(b["sym"], 0.0) + v
    return out


def replay(pol, entry, size, sl0, seg):
    sl = sl0
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
    base = ExitPolicy.for_lane("mid")
    print("生产 mid：min_roi=%s time_limit=%s trailing=(%s,%s) sl_pct=%s"
          % (base.min_roi, base.time_limit_sec, base.trailing_activation_pct,
             base.trailing_callback_pct, base.sl_pct))
    with psycopg.connect(ARENA, autocommit=True) as ac, psycopg.connect(MARKET, autocommit=True) as mc:
        acur = ac.cursor(); acur.execute("SET app.is_admin='on'")
        for label, since in (("09-15 后", "2026-09-15"), ("09-19 起", "2026-09-19")):
            acur.execute(
                """select symbol, entry_price, original_size, sl_price, opened_at
                   from paper_positions
                   where account_id=14 and side='long' and timeframe_tier='mid' and opened_at >= %s
                   order by opened_at""", (since,))
            rows = acur.fetchall()
            mcur = mc.cursor()
            cache: dict = {}
            book = []
            for sym, entry, size, slp, opened in rows:
                if not entry or not size:
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
                e = float(entry); s = float(size)
                sl0 = None
                if slp:
                    try:
                        v = float(slp)
                        if 0 < v < e and (e - v) / e <= 0.15:
                            sl0 = v
                    except (TypeError, ValueError):
                        pass
                if sl0 is None:
                    sl0 = e * (1 - float(base.sl_pct or 6.0) / 100.0)
                book.append({"sym": sym, "e": e, "s": s, "sl0": sl0, "seg": bars[j:]})
            if not book:
                print("\n%s：无样本" % label); continue
            half = len(book) // 2
            print("\n" + "=" * 96)
            print("%s：%d 笔" % (label, len(book)))
            print("  %-20s %10s %10s %10s %10s %10s" % ("变体", "总净额$", "均净$/笔", "前半$", "后半$", "最差3笔$"))
            res = {}
            for name, mroi, tl in VARIANTS:
                pol = dataclasses.replace(base, min_roi=mroi, time_limit_sec=tl)
                vals = [replay(pol, b["e"], b["s"], b["sl0"], b["seg"]) for b in book]
                tot = sum(vals)
                res[name] = {"tot": tot, "h1": sum(vals[:half]), "h2": sum(vals[half:]),
                             "w3": sum(sorted(vals)[:3]),
                             # [R6 修 bug] 旧写法 `{b["sym"]: v for b,v in zip(book, vals)}` 会让
                             # **同一币的多笔只保留最后一笔**，导致"单币集中度"恒≈0%、
                             # 该判据形同虚设（R4/R6 的④都因此失效）。改为**按币求和**。
                             "per": _sum_by_sym(book, vals)}
                print("  %-20s %+10.2f %+10.3f %+10.2f %+10.2f %+10.2f"
                      % (name, tot, tot / len(book), res[name]["h1"], res[name]["h2"], res[name]["w3"]))
            b0 = res["基线 min_roi+t48h"]
            print("\n  相对基线（预登记：Δ总>0 + 双半都改善 + 尾部不变差 + 单币<50%）：")
            for name, _, _ in VARIANTS:
                if name.startswith("基线"):
                    continue
                r = res[name]
                d = r["tot"] - b0["tot"]; d1 = r["h1"] - b0["h1"]; d2 = r["h2"] - b0["h2"]
                per = {k: r["per"][k] - b0["per"].get(k, 0.0) for k in r["per"]}
                top = max(per.items(), key=lambda kv: abs(kv[1]))
                conc = abs(top[1]) / abs(d) * 100 if d else 0.0
                ok = (d > 0 and d1 > 0 and d2 > 0 and r["w3"] >= b0["w3"] and conc < 50)
                print("    %-20s Δ总 %+8.2f | Δ前 %+8.2f | Δ后 %+8.2f | Δ尾 %+8.2f | 单币 %s %.0f%% ⇒ **%s**"
                      % (name, d, d1, d2, r["w3"] - b0["w3"], top[0], conc, "通过" if ok else "未通过"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
