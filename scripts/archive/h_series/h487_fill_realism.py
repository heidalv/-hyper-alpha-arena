"""h487：**纸面成交真实性审计**（用真实逐笔 `asterdex_trades` 检验引擎成交）。

为什么必须做（代码内 H28 已记录该机制的隐患）：
  `core.fill_side` 判成交用的是 **15 秒桶内的极值**（`seg_low < quote_bid` 且桶内有主动卖量），
  而**成交价记作我们的挂单价**；`runner._persist_fill_basis` 的注释直接写明：
  「『桶内极值顺带穿过挂单价』与『真有成交发生在我们的价位上』被当成同一件事，
  但它们的经济含义完全不同」，并实测过 47.7% 的引擎成交在 ±20s/±2bp 内
  **找不到对应的真实逐笔**（偏差中位 5.26bp）。

本脚本用**真实逐笔**对每笔引擎成交做独立判定（`logs/mm_fill_basis.jsonl` vs `asterdex_trades`）：

  · 买单成交（side=buy）成立的条件：窗口内存在**主动卖**（`is_buyer_maker = true`）
    且成交价 ≤ 我们的买价（否则卖方没打到我们的价位 ⇒ 这笔"成交"在真实市场上不存在）；
  · 卖单成交（side=sell）对称：存在**主动买**（`is_buyer_maker = false`）且价 ≥ 我们的卖价；
  · 窗口 = [成交ts − **90s**, +5s]。
    ⚠️ **这个宽度是必须的，第一版用 −30s 得出"39% 幽灵"是错的**：
    引擎有 **F176/F178 延迟判定**（`judge_lag_buckets` 默认 3、`SEG_BUCKET_MS` 15s）
    ⇒ 成交是拿**45 秒前那张挂单**判的，`ts` 只是**检测时刻**；
    h488 实测"不支持"的样本 **17/17** 都能在成交 ts **之前 35–70s**（p50 49s）
    找到真实穿越 ⇒ 结论是**归因时刻错位**，不是成交模型乐观。
  · 无逐笔数据 ⇒ 单列 "no_tape"，不计入任何一侧。

输出四件事：
  1. 真实存在的比例（总体 / 分买卖 / 分入场与平仓）；
  2. **"幽灵成交"的名义额与 edge 贡献**（纸面 P&L 里有多少来自真实市场不支持的成交）；
  3. 真实成交里"打穿我们价位的深度"分布（bp）——队列位置的代理；
  4. 打穿我们的主动量 / 我们的 qty（>1 表示我们大概率排得到队）。

用法：python scripts/h487_fill_realism.py [--hours 3] [--tol-bp 0]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import statistics as st
import sys

sys.stdout.reconfigure(encoding="utf-8")

import psycopg  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
BASIS = ROOT / "logs" / "mm_fill_basis.jsonl"
OUT = ROOT / "research_l1" / "out" / "h487_fill_realism.json"


def read_env_dsn() -> str:
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    url = env["DATABASE_URL"]
    for j in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(j, "")
    return url


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=3.0)
    ap.add_argument("--tol-bp", type=float, default=0.0,
                    help="价格容差（bp）：真实成交价可略高于我们的买价/略低于卖价仍算打到")
    a = ap.parse_args()
    import time as _t
    cutoff = _t.time() - a.hours * 3600
    fills = []
    for ln in BASIS.read_text(encoding="utf-8", errors="replace").splitlines():
        if not ln.strip():
            continue
        try:
            j = json.loads(ln)
        except Exception:  # noqa: BLE001
            continue
        if float(j.get("ts") or 0) >= cutoff:
            fills.append(j)
    print(f"近 {a.hours:.0f}h 引擎成交 {len(fills)} 笔（来源 {BASIS.name}）")
    if not fills:
        return 1
    mk = read_env_dsn().replace("/alpha_arena", "/alpha_market")
    # [h488 修正] 延迟判定：`judge_lag_buckets`(3) × `SEG_BUCKET_MS`(15s) = 45s，
    # 再留一个桶的余量 ⇒ 审计窗口取 90s（用 −30s 会把正常成交误判成"幽灵"）
    LAG_MS = 90000
    rows = []
    with psycopg.connect(mk, autocommit=True) as c:
        with c.cursor() as cur:
            for f in fills:
                sym = str(f["symbol"]) + "USDT"
                t0 = int(float(f["ts"]) * 1000)
                px_ref = float(f["fill_px"])
                cur.execute(
                    "SELECT count(*), "
                    " count(*) FILTER (WHERE is_buyer_maker), "
                    " min(price)::float8, max(price)::float8, "
                    " COALESCE(sum(qty) FILTER (WHERE is_buyer_maker),0)::float8, "
                    " COALESCE(sum(qty) FILTER (WHERE NOT is_buyer_maker),0)::float8, "
                    " max(event_ts_ms) FILTER ("
                    "   WHERE (is_buyer_maker AND price <= %s) OR "
                    "         (NOT is_buyer_maker AND price >= %s)) "
                    "FROM asterdex_trades WHERE symbol=%s "
                    "AND event_ts_ms >= %s AND event_ts_ms <= %s",
                    (px_ref, px_ref, sym, t0 - LAG_MS, t0 + 5000))
                n, n_sell_aggr, lo, hi, vol_sell, vol_buy, hit_ms = cur.fetchone()
                rows.append({"f": f, "n": int(n or 0), "n_sell_aggr": int(n_sell_aggr or 0),
                             "lo": lo, "hi": hi, "vol_sell": float(vol_sell or 0),
                             "vol_buy": float(vol_buy or 0),
                             "lag_s": (round((t0 - int(hit_ms)) / 1000.0, 1)
                                       if hit_ms else None)})
    tol = a.tol_bp / 1e4
    recs = []
    for r in rows:
        f = r["f"]
        px = float(f["fill_px"])
        qty = abs(float(f["qty"]))
        rec = {"sym": f["symbol"], "side": f["side"], "flat": bool(f.get("flatten")),
               "px": px, "qty": qty, "notional": px * qty,
               "edge_bp": float(f.get("edge_bp") or 0.0),
               "seg_low": float(f.get("seg_low") or 0.0),
               "seg_high": float(f.get("seg_high") or 0.0),
               "n_tape": r["n"], "tape_lo": r["lo"], "tape_hi": r["hi"]}
        if r["n"] == 0:
            rec["cat"] = "no_tape"
        elif f["side"] == "buy":
            ok = r["n_sell_aggr"] > 0 and r["lo"] is not None and \
                float(r["lo"]) <= px * (1 + tol)
            rec["cat"] = "true" if ok else "phantom"
            if ok:
                rec["depth_bp"] = (px - float(r["lo"])) / px * 1e4
                rec["aggr_vol"] = r["vol_sell"]
        else:
            ok = (r["n"] - r["n_sell_aggr"]) > 0 and r["hi"] is not None and \
                float(r["hi"]) >= px * (1 - tol)
            rec["cat"] = "true" if ok else "phantom"
            if ok:
                rec["depth_bp"] = (float(r["hi"]) - px) / px * 1e4
                rec["aggr_vol"] = r["vol_buy"]
        recs.append(rec)

    def edge_bp_mean(xs):
        return sum(x["edge_bp"] for x in xs) / len(xs)

    def summarize(name, xs):
        if not xs:
            print(f"  {name:12s} n=0")
            return {}
        usd = sum(x["notional"] for x in xs)
        edge_usd = sum(x["edge_bp"] * x["notional"] / 1e4 for x in xs)
        print(f"  {name:12s} n={len(xs):4d}  名义={usd:9.1f}$  "
              f"edge 贡献={edge_usd:+7.3f}$  均 edge={edge_bp_mean(xs):+.2f}bp")
        return {"n": len(xs), "notional_usd": round(usd, 2),
                "edge_usd": round(edge_usd, 4), "edge_bp_mean": round(edge_bp_mean(xs), 3)}

    cats = {k: [x for x in recs if x["cat"] == k] for k in ("true", "phantom", "no_tape")}
    print("=" * 88)
    print("按**真实逐笔可验证性**分类：")
    t = summarize("真实存在", cats["true"])
    p = summarize("幽灵成交", cats["phantom"])
    nt = summarize("无逐笔数据", cats["no_tape"])
    tot = len(recs)
    print("=" * 88)
    print(f"真实存在占比 = {100.0*len(cats['true'])/tot:.1f}%  "
          f"幽灵 = {100.0*len(cats['phantom'])/tot:.1f}%  "
          f"无数据 = {100.0*len(cats['no_tape'])/tot:.1f}%")
    # 分入场/平仓
    for label, pred in (("入场腿", lambda x: not x["flat"]),
                        ("平仓腿", lambda x: x["flat"])):
        sub = [x for x in recs if pred(x)]
        if not sub:
            continue
        ph = sum(1 for x in sub if x["cat"] == "phantom")
        print(f"  {label}: n={len(sub)}  幽灵 {ph} ({100.0*ph/len(sub):.1f}%)")
    # ── 乐观偏差来自哪一层？ ──────────────────────────────────────────────
    # 引擎判据是「桶内极值越过挂单价」（`seg_low < quote_bid`）。若幽灵成交里
    # `seg_low` 本来就**低于真实逐笔的最低价**，问题出在**分段数据源**；
    # 若 `seg_low` 并不越界，则是**成交判定**的问题。
    seg_cross = seg_nocross = seg_missing = 0
    seg_vs_tape = []
    for r in cats["phantom"]:
        sl, sh, px = r["seg_low"], r["seg_high"], r["px"]
        if sl <= 0 and sh <= 0:
            seg_missing += 1
            continue
        crossed = (sl <= px) if r["side"] == "buy" else (sh >= px)
        if crossed:
            seg_cross += 1
            if r["tape_lo"] is not None and sh > 0:
                seg_vs_tape.append((sl - float(r["tape_lo"])) / px * 1e4
                                   if r["side"] == "buy"
                                   else (float(r["tape_hi"]) - sh) / px * 1e4)
        else:
            seg_nocross += 1
    print(f"\n幽灵成交的分层归因：分段极值确实越过挂单价 = {seg_cross}，"
          f"未越过 = {seg_nocross}，无分段数据 = {seg_missing}")
    if seg_vs_tape:
        sv = sorted(seg_vs_tape)
        print(f"  『分段极值比真实逐笔极值更远』的幅度(bp): p50={sv[len(sv)//2]:+.2f} "
              f"p90={sv[int(.9*(len(sv)-1))]:+.2f} ⇒ >0 = **分段数据比真实成交更乐观**")
    # 打穿深度与主动量
    depths = [x["depth_bp"] for x in cats["true"] if "depth_bp" in x]
    ratios = [x["aggr_vol"] / max(x["qty"], 1e-12) for x in cats["true"]
              if x.get("aggr_vol") and x.get("qty")]
    if depths:
        d = sorted(depths)
        print(f"\n真实成交里『打穿我们价位的深度』: p10/p50/p90 = "
              f"{d[int(.1*(len(d)-1))]:.2f}/{d[len(d)//2]:.2f}/{d[int(.9*(len(d)-1))]:.2f} bp"
              f"  （>0 = 极值确实越过我们的价位）")
    if ratios:
        rr = sorted(ratios)
        print(f"主动量/我们的 qty（队列代理）: p10/p50/p90 = "
              f"{rr[int(.1*(len(rr)-1))]:.1f}/{rr[len(rr)//2]:.1f}/{rr[int(.9*(len(rr)-1))]:.1f}"
              f"  （≥1 表示打到我们价位的主动量足够覆盖我们的量）")
    # 延迟分布（成交 ts 到真实穿越时刻的差）
    lags = sorted(x["lag_s"] for x in recs if x.get("lag_s") is not None)
    if lags:
        print(f"\n**成交时刻滞后（检测 ts − 真实穿越时刻）= {len(lags)} 笔有值**："
              f"p10/p50/p90 = {lags[int(.1*(len(lags)-1))]:.0f}/"
              f"{lags[len(lags)//2]:.0f}/{lags[int(.9*(len(lags)-1))]:.0f} s"
              f"  ⇒ 一切以 `ts` 为锚的时间分析都要按此平移")
    ph_share = 100.0 * len(cats["phantom"]) / tot
    # 口径修正：**笔数份额 ≠ 影响份额**。幽灵成交的 edge 贡献很小（均 +0.28bp），
    # 若按笔数就说"纸面 P&L 主要由乐观偏差构成"是夸大的（本项目忌这种结论）。
    _edge_all = sum(x["edge_bp"] * x["notional"] / 1e4 for x in recs)
    _edge_ph = sum(x["edge_bp"] * x["notional"] / 1e4 for x in cats["phantom"])
    edge_share = 100.0 * _edge_ph / _edge_all if _edge_all else 0.0
    inflation = tot / max(len(cats["true"]), 1)
    print(f"\n幽灵成交的 **edge 份额 = {edge_share:.1f}%**（笔数份额 {ph_share:.1f}%）"
          f"；成交笔数乐观倍数 = {inflation:.2f}×")
    if ph_share < 10:
        verdict = "纸面成交基本能被真实逐笔支持（幽灵 <10%）⇒ 纸面 P&L 可用作方向性证据"
    elif edge_share < 15:
        verdict = (f"幽灵**笔数** {ph_share:.0f}%（≈{inflation:.1f}× 乐观），但它们的 "
                   f"**edge 份额只有 {edge_share:.0f}%** ⇒ "
                   f"① 总体 P&L 结论（手续费主导、止损出血）不受实质影响；"
                   f"② **但「腿速」类指标被系统性放大 ≈{inflation:.1f} 倍** ⇒ "
                   f"用户 ≥60 腿/h 的硬约束在**真实可成交口径**下可能不达标，"
                   f"必须按「真实笔数/总笔数」折算后再判")
    else:
        verdict = (f"幽灵笔数 {ph_share:.0f}%、edge 份额 {edge_share:.0f}% ⇒ "
                   f"纸面 P&L 与腿速都**显著偏乐观**，任何基于它的参数结论都必须先修"
                   f"成交判定（或加「真实逐笔确认」开关后重测）")
    print("\n⇒ 裁决:", verdict)
    OUT.write_text(json.dumps(
        {"hours": a.hours, "tol_bp": a.tol_bp, "n_fills": tot,
         "true": t, "phantom": p, "no_tape": nt,
         "phantom_share_pct": round(ph_share, 2),
         "phantom_edge_share_pct": round(edge_share, 2),
         "fill_count_inflation": round(inflation, 3),
         "seg_layer": {"crossed": seg_cross, "not_crossed": seg_nocross,
                       "missing": seg_missing,
                       "seg_minus_tape_bp_p50": (sv[len(sv)//2] if seg_vs_tape else None),
                       "seg_minus_tape_bp_p90": (sv[int(.9*(len(sv)-1))]
                                                 if seg_vs_tape else None)},
         "depth_bp": {"p10": depths[int(.1*(len(depths)-1))] if depths else None,
                      "p50": st.median(depths) if depths else None,
                      "p90": depths[int(.9*(len(depths)-1))] if depths else None},
         "aggr_vol_ratio": {"p50": st.median(ratios) if ratios else None},
         "verdict": verdict,
         "sample_phantom": cats["phantom"][:40]}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print("已写:", OUT.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
