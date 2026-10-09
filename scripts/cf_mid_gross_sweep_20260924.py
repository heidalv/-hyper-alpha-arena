# -*- coding: utf-8 -*-
"""目标① 的"抬单笔毛利"路径：用**生产出场引擎**在真实中线入场点上扫追踪参数。

依据（R4）：
  - 中线修正后（09-19 起，factor_route 已关）69 笔：毛 **+7.03**、费 **18.75**、净 **−11.71**
    ⇒ 缺口 100% 是手续费，单笔毛利要从 +$0.10 抬到 > +$0.27 才转正；
  - 入场侧三条路（regime 闸 / 位置闸放宽 / 波动闸）全部已被否证 ⇒ 只剩"出场侧放大毛利"。
  - 引擎一律调用生产 `ExitPolicy.for_lane()` + `evaluate()`（R3 教训：不得重写出场逻辑）。

扫描：mid 追踪 (激活, 回撤) ∈ {基线(5.0,2.5), (8,4), (10,5), (12,6), (3,1.5)} × (None,None)=关追踪
      sl_pct 固定为生产值；硬 SL 用仓位上记录的 sl_price（±15% 内可用）。

**预登记验收标准（看结果前写定，事后不得修改）**
  通过 = ①总净额相对基线改善 > 0
        ②前后半样本**都**改善
        ③最差 3 笔不变差
        ④改善不由单币主导（最大单币贡献 < 50%）
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
VARIANTS = [
    ("基线(5.0,2.5)", 5.0, 2.5),
    ("放宽(8,4)", 8.0, 4.0),
    ("放宽(10,5)", 10.0, 5.0),
    ("放宽(12,6)", 12.0, 6.0),
    ("收紧(3,1.5)", 3.0, 1.5),
    ("关追踪(None)", None, None),
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
    base_pol = ExitPolicy.for_lane("mid")
    print("生产 mid 策略：act=%s cb=%s sl_pct=%s time_limit=%s min_roi=%s"
          % (base_pol.trailing_activation_pct, base_pol.trailing_callback_pct,
             base_pol.sl_pct, base_pol.time_limit_sec, base_pol.min_roi))
    with psycopg.connect(ARENA, autocommit=True) as ac, psycopg.connect(MARKET, autocommit=True) as mc:
        acur = ac.cursor(); acur.execute("SET app.is_admin='on'")
        for label, since in (("09-15 后全样本", "2026-09-15"), ("09-19 起（修正后）", "2026-09-19")):
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
                    sl0 = e * (1 - float(base_pol.sl_pct or 6.0) / 100.0)
                book.append({"sym": sym, "t": t0, "e": e, "s": s, "sl0": sl0, "seg": bars[j:]})
            if not book:
                print("\n%s：无样本" % label); continue
            print("\n" + "=" * 92)
            print("%s：%d 笔" % (label, len(book)))
            print("  %-16s %10s %10s %10s %10s %10s" % ("变体", "总净额$", "均净$/笔", "前半$", "后半$", "最差3笔$"))
            res = {}
            half = len(book) // 2
            for name, act, cb in VARIANTS:
                pol = dataclasses.replace(base_pol, trailing_activation_pct=act, trailing_callback_pct=cb)
                vals = [replay(pol, b["e"], b["s"], b["sl0"], b["seg"]) for b in book]
                tot = sum(vals)
                h1 = sum(vals[:half]); h2 = sum(vals[half:])
                w3 = sum(sorted(vals)[:3])
                res[name] = {"tot": tot, "h1": h1, "h2": h2, "w3": w3,
                             # [R6 修 bug] 旧写法按 sym 建字典会让**同一币的多笔只剩最后一笔**，
                             # 使"单币集中度"恒≈0%、判据④形同虚设。改为按币求和。
                             "per": _sum_by_sym(book, vals)}
                print("  %-16s %+10.2f %+10.3f %+10.2f %+10.2f %+10.2f"
                      % (name, tot, tot / len(book), h1, h2, w3))
            base = res["基线(5.0,2.5)"]
            print("\n  相对基线的判定（预登记：改善>0 + 双半都改善 + 尾部不变差 + 单币<50%）：")
            for name, _, _ in VARIANTS:
                if name == "基线(5.0,2.5)":
                    continue
                r = res[name]
                d = r["tot"] - base["tot"]
                d1 = r["h1"] - base["h1"]; d2 = r["h2"] - base["h2"]
                per = {k: r["per"][k] - base["per"].get(k, 0.0) for k in r["per"]}
                top = max(per.items(), key=lambda kv: abs(kv[1]))
                conc = abs(top[1]) / abs(d) * 100 if d else 0.0
                ok = (d > 0 and d1 > 0 and d2 > 0 and r["w3"] >= base["w3"] and conc < 50)
                print("    %-16s Δ总 %+8.2f | Δ前半 %+8.2f | Δ后半 %+8.2f | Δ尾部 %+8.2f | 单币 %s %.0f%% ⇒ **%s**"
                      % (name, d, d1, d2, r["w3"] - base["w3"], top[0], conc,
                         "通过" if ok else "未通过"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
