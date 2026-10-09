"""H64：出场规则对左尾的影响 —— 止损为什么没拦住 −164bp？

# 现场证据（H63，实盘账本逐笔）

| phase | 笔数 | 均值 | 中位 | 胜率 |
|---|---|---|---|---|
| `fill`（入场腿） | 1,823 | −2.3714bp | **+1.2719bp** | **0.7466** |
| **`flatten`（强平腿）** | **58**（2.9%） | **−28.9458bp** | **−13.0737bp** | **0.2414** |

**最亏 40 笔里 17 笔是 flatten**，最大的几笔：UNI sell −164.8bp、ASTER buy −160.9bp、
DOGE buy −133.1bp、UNI sell −125.6bp。**集中在 ASTER / UNI / ARB / DOGE / XRP（薄币）。**

# 逻辑矛盾（这就是 bug）

引擎 registry 实况：
    stop_loss_bp        = 60.0      ← 有止损
    min_hold_seconds    = 30.0      ← **前 30 秒禁止引擎主动平仓**
    max_one_side_seconds= 300.0
    vol_pause_mult      = 0.0       ← 疑似未启用

**一笔亏 −164.8bp 的仓位，按 `stop_loss_bp=60` 应该在 −60bp 就被止损。它没有。**
原因是 `_force_exit_allowed()`（F258，我自己写的）把**止损**也纳入
`min_hold_seconds` 的拦截范围 —— 设计意图是"防刚建仓就白付出场成本"，
但那是对**正常行情**的优化，在**尾部事件**下会把亏损放大一个量级。

⇒ 这正是第 15/16 条教训的变体：**用均值口径设计的参数，被尾部反噬**。

# 本脚本测什么

用真实 tick 做逐段事件研究（不是 15s 网格近似）：
  1. 在每个入场点按 F280 挂宽建仓
  2. 取之后的**真实 mid 路径**（1s 分辨率，最长 horizon）
  3. 对每种出场规则算实际出场价与净额：
     · R0 300s 超时强平（现状）
     · R1 固定 hold h ∈ {10,30,60,120,300}s 后强平
     · R1b 固定 hold h 后**按 maker 出场**（挂在 mid，吃到返佣，但不保证成交）
     · R2 止损 SL_bp（**不受 min_hold 拦截**）+ 300s 超时
     · R3 止损 SL_bp + 移动止损 TRAIL_bp
     · R4 止损 SL_bp，且 min_hold 内**只允许止损**（= 现状 + 尾部例外）

**关键**：止损出场是 **taker**（付 `taker_fee_bp`），超时出场也是 taker。
maker 出场付 maker 费但要打折扣（按成交概率）。这里**不虚构成交**：
对 maker 出场只报"若能成交"的上界，并显式标注。

# 判据（事先定死）

  · 若某规则的**均值 > 0** 且**最亏 1% 显著收窄** ⇒ 上线
  · 若只有尾部收窄而均值未转正 ⇒ 仍需与返佣合并看，但尾部控制本身值得上
  · 不得用"剔除样本"来改善结果（前 3 次错误的教训）

用法：
    .venv\\Scripts\\python.exe scripts\\h64_exit_rule_tail.py --hours 24
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

OUT = ROOT / "research_l1" / "out" / "h64_exit_rules.json"
STEP_MS = 15_000
PATH_STEP_MS = 1_000          # mid 路径分辨率
MAX_EXPOSURE_S = 300
DEFAULT_SYMS = "BTC,ETH,SOL,XRP,DOGE,BNB,SEI,VIRTUAL,PENDLE,1000SHIB"


def _dsn(db: str) -> str:
    url = os.getenv("DATABASE_URL") or ""
    for p in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(p, "")
    head, _, _ = url.rpartition("/")
    return head + "/" + db


def _st(x):
    import numpy as np

    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]
    if len(x) == 0:
        return {"n": 0}
    return {"n": int(len(x)), "mean": round(float(x.mean()), 4),
            "median": round(float(np.median(x)), 4),
            "p1": round(float(np.percentile(x, 1)), 4),
            "p5": round(float(np.percentile(x, 5)), 4),
            "worst": round(float(x.min()), 4),
            "win": round(float((x > 0).mean()), 4)}


def collect(sym, hours, spread_mult, cross_margin):
    """返回买单侧入场样本：入场价、入场时 mid、之后的 mid 路径。"""
    import numpy as np
    import psycopg2
    import psycopg2.extras

    vs = sym if sym.endswith("USDT") else f"{sym}USDT"
    cn = psycopg2.connect(_dsn("alpha_market"))
    cn.autocommit = True
    cur = cn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    since = f"(extract(epoch from now())*1000)::bigint - {int(hours * 3600_000)}"

    cur.execute(
        "SELECT event_ts_ms, bid_px::float b, ask_px::float a"
        f"  FROM asterdex_book_ticker WHERE symbol=%s AND event_ts_ms > {since}"
        "  ORDER BY event_ts_ms", (vs,))
    ob = cur.fetchall()
    cur.execute(
        "SELECT event_ts_ms, price::float p, is_buyer_maker"
        f"  FROM asterdex_trades WHERE symbol=%s AND event_ts_ms > {since}"
        "  ORDER BY event_ts_ms", (vs,))
    tr = cur.fetchall()
    cn.close()
    if len(ob) < 3000 or len(tr) < 200:
        return None

    ot = np.array([r["event_ts_ms"] for r in ob], dtype=np.int64)
    ob_ = np.array([r["b"] for r in ob]); oa = np.array([r["a"] for r in ob])
    ok = (ob_ > 0) & (oa > ob_)
    ot, ob_, oa = ot[ok], ob_[ok], oa[ok]
    mid = 0.5 * (ob_ + oa)

    tt = np.array([r["event_ts_ms"] for r in tr], dtype=np.int64)
    tp = np.array([r["p"] for r in tr])
    tbm = np.array([bool(r["is_buyer_maker"]) for r in tr])
    v = tp > 0
    tt, tp, tbm = tt[v], tp[v], tbm[v]

    t0, t1 = int(ot[0]), int(ot[-1])
    grids = np.arange(t0, t1 - MAX_EXPOSURE_S * 1000, STEP_MS)
    gi = np.searchsorted(ot, grids, side="right") - 1
    g = gi >= 0
    grids, gi = grids[g], gi[g]
    mb, ma = ob_[gi], oa[gi]
    mid_g = 0.5 * (mb + ma)
    half_g = 0.5 * (ma - mb)
    w = np.maximum(float(spread_mult) * half_g, 1e-12)
    px_bid = np.minimum(np.minimum(mid_g - w, ma - cross_margin * (ma - mb)), mid_g)

    # mid 路径：每 1s 一个点，长度 MAX_EXPOSURE_S
    npath = MAX_EXPOSURE_S + 1
    path_ms = grids[:, None] + np.arange(npath)[None, :] * PATH_STEP_MS
    pi = np.searchsorted(ot, path_ms.ravel(), side="right") - 1
    pi = np.clip(pi, 0, len(ot) - 1)
    path = mid[pi].reshape(path_ms.shape)        # (ngrid, npath)

    entries = []
    for k in range(len(grids)):
        lo = grids[k]; hi = lo + STEP_MS
        i0 = np.searchsorted(tt, lo, side="left")
        i1 = np.searchsorted(tt, hi, side="left")
        if i1 <= i0:
            continue
        seg_p = tp[i0:i1]; seg_bm = tbm[i0:i1]
        if not (seg_bm & (seg_p <= px_bid[k])).any():
            continue
        entries.append(k)
    if not entries:
        return None
    e = np.array(entries)
    return {"symbol": vs, "grid": grids[e], "mid0": mid_g[e], "entry": px_bid[e],
            "half": half_g[e], "path": path[e]}


def eval_rules(d, maker_fee_bp, taker_fee_bp, maker_fill_prob=0.5):
    """对每种出场规则算净额。返回 {rule: bp数组}。

    买腿：入场买在 `entry`，出场卖。
      · taker 出场价 = 路径点的 **bid** ≈ mid − half（要穿过价差）
      · maker 出场价 = 路径点的 mid（挂 mid 卖），成交概率打折
    """
    import numpy as np

    mid0 = d["mid0"]
    entry = d["entry"]
    half = d["half"]
    path = d["path"]
    n = len(mid0)
    res = {}

    def taker_exit(px_mid, h):
        # 卖出走 taker：得到 px_mid − half（穿价差），付 taker 费
        return (px_mid - half - entry) / mid0 * 1e4 - abs(taker_fee_bp)

    # ── R0/R1：固定持有后强平 ──
    for h in (10, 30, 60, 120, 300):
        j = min(h, path.shape[1] - 1)
        res[f"R1 固定{h}s 强平"] = taker_exit(path[:, j], h)

    # ── R2：止损（不受 min_hold 拦截）+ h_max 超时 ──
    # 路径上首个使 (mid − half − entry) 亏损 <= −SL 的时点
    for sl in (5.0, 10.0, 20.0, 30.0, 60.0):
        hit = None
        worst = (path - half[:, None] - entry[:, None]) / mid0[:, None] * 1e4
        trig = worst <= -sl
        any_t = trig.any(axis=1)
        first = np.where(any_t, trig.argmax(axis=1), path.shape[1] - 1)
        px = path[np.arange(n), first]
        out = taker_exit(px, 0)
        # 未触发止损的按 300s 超时
        res[f"R2 止损{sl:.0f}bp+300s超时"] = out

    # ── R3：止损 + 移动止损 ──
    for sl, tr_ in ((10.0, 5.0), (20.0, 10.0)):
        worst = (path - half[:, None] - entry[:, None]) / mid0[:, None] * 1e4
        runmax = np.maximum.accumulate(worst, axis=1)
        trig = (worst <= -sl) | (runmax - worst >= tr_) & (runmax > 0)
        any_t = trig.any(axis=1)
        first = np.where(any_t, trig.argmax(axis=1), path.shape[1] - 1)
        px = path[np.arange(n), first]
        res[f"R3 止损{sl:.0f}+移动{tr_:.0f}"] = taker_exit(px, 0)

    # ── R1b：固定持有后 **maker** 出场（挂 mid），成交概率打折 ──
    # 上界：假设总能成交（乐观）；同时报打折后的期望
    for h in (30, 60, 300):
        j = min(h, path.shape[1] - 1)
        gross = (path[:, j] - entry) / mid0 * 1e4 + maker_fee_bp
        res[f"R1b maker {h}s（上界）"] = gross
        # 打折不成交时以 taker 兜底
        fallback = taker_exit(path[:, j], h)
        res[f"R1b maker {h}s（{maker_fill_prob:.0%}成交）"] = (
            maker_fill_prob * gross + (1 - maker_fill_prob) * fallback)
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=24.0)
    ap.add_argument("--symbols", default=DEFAULT_SYMS)
    ap.add_argument("--spread-mult", type=float, default=0.9)
    ap.add_argument("--cross-margin", type=float, default=0.05)
    ap.add_argument("--maker-fee-bp", type=float, default=0.0)
    ap.add_argument("--taker-fee-bp", type=float, default=4.0)
    a = ap.parse_args()

    import numpy as np

    syms = [s.strip().upper() for s in a.symbols.split(",") if s.strip()]
    print("=" * 110)
    print("H64  出场规则对左尾的影响 —— 止损为什么没拦住 −164bp")
    print("=" * 110)
    print(f"  窗口 {a.hours:.0f}h  ·  spread_mult {a.spread_mult}  ·  "
          f"maker {a.maker_fee_bp:+.2f}bp  ·  taker {a.taker_fee_bp:+.2f}bp")
    print(f"  入场：F280 挂宽 + 主动卖打到即成交；出场：taker 穿价差 / maker 挂 mid")

    per = {}
    for s in syms:
        d = collect(s, a.hours, a.spread_mult, a.cross_margin)
        if d:
            per[d["symbol"]] = d
            print(f"  拉取 {d['symbol']:<12} 入场样本 {len(d['mid0']):,}")
    if not per:
        print("无数据")
        return 1

    # 汇总所有币的净额（按币分别算再等权，避免名义加权 —— 第 11 条教训）
    allrules = {}
    for s, d in per.items():
        r = eval_rules(d, a.maker_fee_bp, a.taker_fee_bp)
        for k, v in r.items():
            allrules.setdefault(k, {})[s] = v

    print("\n" + "=" * 110)
    print("各出场规则的跨币等权结果")
    print("=" * 110)
    print(f"\n  {'规则':<28} {'均值bp':>9} {'中位':>9} {'p1':>9} {'p5':>9} "
          f"{'最亏':>10} {'胜率':>7} {'为正币数':>9}")
    print("  " + "-" * 104)
    summary = {}
    for k in sorted(allrules, key=lambda x: -np.mean([v.mean() for v in allrules[x].values()])):
        vs = list(allrules[k].values())
        m = float(np.mean([v.mean() for v in vs]))
        md = float(np.mean([np.median(v) for v in vs]))
        p1 = float(np.mean([np.percentile(v, 1) for v in vs]))
        p5 = float(np.mean([np.percentile(v, 5) for v in vs]))
        wst = float(np.mean([v.min() for v in vs]))
        w = float(np.mean([(v > 0).mean() for v in vs]))
        pos = sum(1 for v in vs if v.mean() > 0)
        summary[k] = {"mean": round(m, 4), "median": round(md, 4), "p1": round(p1, 4),
                      "p5": round(p5, 4), "worst": round(wst, 4),
                      "win": round(w, 4), "positive_symbols": pos, "n_symbols": len(vs)}
        print(f"  {k:<28} {m:>+9.4f} {md:>+9.4f} {p1:>+9.4f} {p5:>+9.4f} "
              f"{wst:>+10.3f} {w:>7.4f} {pos:>4}/{len(vs):<4}")

    # 判据
    print("\n" + "=" * 110)
    print("判据（事先定死）")
    print("=" * 110)
    base = summary.get("R1 固定300s 强平")
    if base:
        print(f"\n  基线 R1 固定300s（≈现状）：均值 {base['mean']:+.4f}bp，"
              f"p1 {base['p1']:+.3f}，最亏 {base['worst']:+.3f}")
        for k, v in summary.items():
            if k.startswith("R1 固定300s"):
                continue
            dmean = v["mean"] - base["mean"]
            dp1 = v["p1"] - base["p1"]
            flag = ""
            if v["mean"] > 0:
                flag += " ★均值转正"
            if dp1 > 3.0:
                flag += " ★尾部显著收窄"
            print(f"    {k:<28} 均值 {v['mean']:>+8.4f} ({dmean:>+7.4f})  "
                  f"p1 {v['p1']:>+8.3f} ({dp1:>+7.3f}){flag}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"hours": a.hours, "spread_mult": a.spread_mult,
                               "maker_fee_bp": a.maker_fee_bp,
                               "taker_fee_bp": a.taker_fee_bp,
                               "summary": summary}, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(f"\n[H64] 写出 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
