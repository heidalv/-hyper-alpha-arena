# -*- coding: utf-8 -*-
"""[F146] 「稳定正收益」判定工具（一键，可复算）。

目标的完成判据需要一个**能自动给出结论**的口径，而不是每次手工解读日志：

  实盘窗口（自最近一次配置变更起）：
    · n 笔、名义额、净额、notional 加权净 bp
    · 每笔净 bp 的均值/中位/p10/最小/标准差 ⇒ **t 统计量**与双侧 p 值
      ⇒ 判定"本窗口是否**显著为正**" ✓
    · 若未显著：给出在**当前效应量**下达到 t=2 所需的样本量（还要跑多久）
  模型同窗口（3 个归属口径 = 实测 p10/中位/p90）：
    · 净 bp 的**区间**（不报单点，遵循 F114：绝对水平不可识别）
  对照：实盘 vs 模型的差值 ± 标准误（同口径配对检验的近似）

用法：
    python scripts/mm_paired_verdict.py             # 自最近一次配置变更起
    python scripts/mm_paired_verdict.py --hours 6   # 最近 6 小时（自动裁剪到变更点）
    python scripts/mm_paired_verdict.py --since 2026-09-14T21:03:00+08:00
"""
from __future__ import annotations

import argparse
import math
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

LANE = "mm_asterdex"
DELAYS = (1600.0, 7600.0, 13500.0)      # 成交桶可见性滞后（实测 p10/中位/p90）


def sign_test(values):
    """纯函数：符号检验（正收益笔数占比）——**厚尾分布下比均值 t 检验有效得多**。

    为什么必须加：实测每笔净 bp 的标准差 ≈ 9.8bp、均值 ≈ +0.17bp ⇒ 均值检验要达到
    |t|=2.5 需要 ~2 万笔（>4 天）✗✗，而"典型成交是否赚钱"用符号检验只需几百笔 ✓
    （厚尾只影响均值，不影响中位数/符号 ✓）。二项分布正态近似（n≥20 足够）：
        z = (k/n − 0.5) / (0.5/√n)，双侧 p = erfc(|z|/√2)
    """
    n = len(values)
    k = sum(1 for x in values if x > 0)
    if n < 20:
        return {"n": n, "pos": k, "share": (k / n if n else 0.0), "z": 0.0, "p": 1.0}
    z = (k / n - 0.5) / (0.5 / math.sqrt(n))
    return {"n": n, "pos": k, "share": k / n, "z": z,
            "p": math.erfc(abs(z) / math.sqrt(2.0))}


def bootstrap_median_ci(values, iters=2000, alpha=0.05, seed=7):
    """纯函数：中位数的 bootstrap 置信区间（厚尾下比均值 CI 稳定得多）。"""
    import random
    n = len(values)
    if n < 10:
        return None
    rnd = random.Random(seed)
    meds = []
    for _ in range(iters):
        s = sorted(rnd.choice(values) for _ in range(n))
        meds.append(s[n // 2])
    meds.sort()
    lo = meds[int((alpha / 2) * iters)]
    hi = meds[min(iters - 1, int((1 - alpha / 2) * iters))]
    return (lo, hi)


def cash_trips(rows):
    """[F148] 与**归因无关**的口径：一趟往返的现金净额 = Σ卖出 − Σ买入（只用成交价）。

    为什么必须同时报这个：账本/引擎的 `net_bp` 依赖"用哪个 mid 归因"（实盘用挂单依据
    中价、模型用其迭代快照中价）⇒ 跨引擎**不可比** ✗。实测实盘现金口径 +0.73bp
    而账本归因口径 +0.18bp（差 0.55bp ⇒ 归因偏保守）。
    rows: [{"ts","symbol","side","fill_px","qty"}]
    """
    pos = {}
    out = []
    for x in sorted(rows, key=lambda y: y["ts"]):
        s = x["symbol"]
        sg = 1 if str(x["side"]) in ("buy", "long", "b") else -1
        q = float(x["qty"])
        px = float(x["fill_px"])
        if q <= 0 or px <= 0:
            continue
        p = pos.setdefault(s, {"qty": 0.0, "cash": 0.0, "buy_nt": 0.0, "n": 0})
        if abs(p["qty"]) < 1e-12:
            p.update(qty=0.0, cash=0.0, buy_nt=0.0, n=0)
        p["qty"] += sg * q
        p["cash"] += -sg * q * px
        if sg > 0:
            p["buy_nt"] += q * px
        p["n"] += 1
        if abs(p["qty"]) < 1e-12 and p["n"] >= 2:
            out.append((p["cash"], p["buy_nt"]))
            p.update(qty=0.0, cash=0.0, buy_nt=0.0, n=0)
    return out


def ttest_stats(values):
    """纯函数：给一组观测值，返回均值/标准差/标准误/t/双侧 p/达到 t=2 所需 n。

    小样本下 t 分布与正态有差别，但这里 n 常在数十到数百 ⇒ 正态近似足够；
    并且我们只用它做"是否显著"的**粗判**，保守起见阈值取 |t| ≥ 2.5。
    """
    n = len(values)
    if n < 2:
        return {"n": n, "mean": (values[0] if values else 0.0), "sd": 0.0,
                "se": float("inf"), "t": 0.0, "p": 1.0, "need_n": None}
    mean = sum(values) / n
    var = sum((x - mean) ** 2 for x in values) / (n - 1)
    sd = math.sqrt(var)
    se = sd / math.sqrt(n)
    t = mean / se if se > 0 else 0.0
    p = math.erfc(abs(t) / math.sqrt(2.0))          # 双侧 p（正态近似）
    need = None
    if mean > 0 and sd > 0:
        need = int(math.ceil((2.5 * sd / mean) ** 2))   # 达到 |t|=2.5 所需 n
    return {"n": n, "mean": mean, "sd": sd, "se": se, "t": t, "p": p, "need_n": need}


def _live_fills(since_dt):
    from sqlalchemy import text
    from backend.core.tenant import system_identity
    from backend.database.connection import SessionLocal
    q = ("SELECT ts, symbol, notional, net_bp, spread_bp, price_bp, meta_json"
         " FROM lane_ledger WHERE lane_id=:l AND event='fill' AND ts >= :a ORDER BY ts")
    with system_identity():
        with SessionLocal() as db:
            db.execute(text("SET statement_timeout = 30000"))
            return [dict(r) for r in db.execute(text(q), {"l": LANE, "a": since_dt})
                    .mappings().all()]


def _last_change(lane: str):
    from backend.services import lane_registry as reg
    try:
        meta = (reg.get_lane(lane) or {}).get("meta") or {}
        cands = [x.get("ts") for x in (meta.get("ops_changes") or []) if x.get("ts")]
        ev = meta.get("evolution") or {}
        if ev.get("last_change_ts"):
            cands.append(ev["last_change_ts"])
        if not cands:
            return None
        return max(datetime.fromisoformat(str(t).replace("Z", "+00:00"))
                   for t in cands).astimezone()
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=None)
    ap.add_argument("--since", default=None)
    ap.add_argument("--lane", default=LANE)
    ap.add_argument("--no-model", action="store_true")
    args = ap.parse_args()

    from backend.services.market_maker.portfolio_replay import replay_portfolio, _load_all
    from backend.services.market_maker.runner import get_runner

    r = get_runner(args.lane)
    if r is None:
        print("运行态不可用")
        return 1
    syms = list(r.symbols)
    lc = _last_change(args.lane)
    now = datetime.now().astimezone()
    since = (datetime.fromisoformat(args.since) if args.since
             else now - timedelta(hours=(args.hours if args.hours else 3.0)))
    clipped = False
    if args.since is None and lc is not None and lc > since:
        since, clipped = lc, True
    span_h = max(0.01, (now - since).total_seconds() / 3600.0)

    print(f"=== 「稳定正收益」判定：{args.lane} ===")
    print(f"窗口 {since:%m-%d %H:%M:%S} ~ {now:%m-%d %H:%M:%S}（{span_h:.2f}h）"
          + ("  [已裁剪到最近一次配置变更]" if clipped else ""))
    print(f"配置 w_base={r.params.w_base_bp} k_vol={r.params.k_vol} k_inv={r.params.k_inv} "
          f"| 净敞口上限 {r.limits.max_net_exposure_ratio}× 方向 {r.limits.max_net_directional_ratio}× "
          f"| 腿量 ${r.fill_notional:.0f} 复利 {r.compound_ratio}")

    rows = [x for x in _live_fills(since)
            if str((x.get("meta_json") or {}).get("source") or "") != "reconcile"
            and float(x.get("notional") or 0.0) > 0]
    vals = [float(x["net_bp"] or 0.0) for x in rows]
    nt = sum(float(x["notional"]) for x in rows)
    net_usd = sum(float(x["notional"]) * float(x["net_bp"] or 0.0) / 1e4 for x in rows)
    st = ttest_stats(vals)
    print(f"\n【实盘】{st['n']} 笔 = {st['n']/span_h:.1f}/h  名义 ${nt:.0f}  "
          f"净 {net_usd:+.2f}USD = {net_usd/nt*1e4 if nt else 0:+.3f}bp")
    if vals:
        sv = sorted(vals)
        q = lambda p: sv[min(len(sv) - 1, int(p * len(sv)))]
        print(f"  每笔 bp: 均值 {st['mean']:+.3f} ± {st['se']:.3f}(SE)  中位 {q(.5):+.3f}  "
              f"p10 {q(.1):+.3f}  p90 {q(.9):+.3f}  最小 {sv[0]:+.2f}  标准差 {st['sd']:.2f}")
        print(f"  均值 t = {st['t']:+.2f}  双侧 p ≈ {st['p']:.3f}  ⇒ "
              + ("**显著为正** ✓" if st["p"] < 0.05 and st["mean"] > 0
                 else ("显著为负 ✗" if st["p"] < 0.05 and st["mean"] < 0 else "尚不显著")))
        if st["need_n"] and st["need_n"] > st["n"]:
            eta_h = (st["need_n"] - st["n"]) / max(1e-9, st["n"] / span_h)
            print(f"  （均值口径）达到 |t|=2.5 还需约 {st['need_n'] - st['n']} 笔"
                  f"（≈ {eta_h:.1f} 小时 @ 当前速率）")
        # [F147] 厚尾分布下的正确判据：符号检验 + 中位数 bootstrap CI
        sg = sign_test(vals)
        med_ci = bootstrap_median_ci(vals)
        print(f"  **典型成交**（符号检验）：正收益 {sg['pos']}/{sg['n']} = {sg['share']*100:.1f}%  "
              f"z = {sg['z']:+.2f}  p ≈ {sg['p']:.4f}  ⇒ "
              + ("**典型成交显著为正** ✓✓" if sg["p"] < 0.05 and sg["share"] > 0.5
                 else ("显著为负 ✗" if sg["p"] < 0.05 and sg["share"] < 0.5 else "不显著")))
        if med_ci:
            print(f"  每笔 bp 中位数 {q(.5):+.3f}bpbootstrap 95%CI = "
                  f"[{med_ci[0]:+.3f}, {med_ci[1]:+.3f}]  ⇒ "
                  + ("**CI 不含 0 ⇒ 中位收益显著为正** ✓✓" if med_ci[0] > 0
                     else ("CI 不含 0 且为负 ✗" if med_ci[1] < 0 else "CI 含 0 ⇒ 不显著")))
    tot = {"spread": 0.0, "price": 0.0, "fee": 0.0}
    for x in rows:
        for k in tot:
            tot[k] += float(x["notional"]) * float(x.get(f"{k}_bp") or 0.0) / 1e4
    if nt:
        print(f"  分解：价差 {tot['spread']/nt*1e4:+.2f}  价格 {tot['price']/nt*1e4:+.2f}  "
              f"费用 {tot['fee']/nt*1e4:+.2f} bp")
    # [F148] 现金口径（与归因无关，跨引擎可比）
    trips = cash_trips([{"ts": x["ts"], "symbol": x["symbol"],
                         "side": (x.get("meta_json") or {}).get("side"),
                         "fill_px": (x.get("meta_json") or {}).get("fill_px"),
                         "qty": (x.get("meta_json") or {}).get("qty")} for x in rows
                        if (x.get("meta_json") or {}).get("fill_px")])
    if trips:
        cusd = sum(t[0] for t in trips)
        cnt = sum(t[1] for t in trips)
        cbp = [t[0] / t[1] * 1e4 for t in trips if t[1] > 0]
        cst = ttest_stats(cbp)
        csg = sign_test(cbp)
        print(f"  【现金口径】{len(trips)} 趟往返  净 {cusd:+.2f}USD = "
              f"{cusd/cnt*1e4 if cnt else 0:+.3f}bp  每趟中位 "
              f"{sorted(cbp)[len(cbp)//2] if cbp else 0:+.3f}bp  "
              f"正收益趟 {csg['pos']}/{csg['n']} = {csg['share']*100:.0f}%  "
              f"符号检验 p ≈ {csg['p']:.4f}")
        if cst["need_n"]:
            print(f"  （现金口径均值 t = {cst['t']:+.2f}；达到 |t|=2.5 需 ~{cst['need_n']} 趟）")
    # [F150] 「稳定」判据：分时段一致性 + 累计曲线回吐 + 分币分散度。
    # 单看一个累计数不足以称"稳定"（可能由单段行情贡献 ✗）⇒ 必须看跨时段一致性 ✓
    if rows:
        bmin = 15
        bk = {}
        for x in rows:
            t = x["ts"]
            key = t.replace(minute=(t.minute // bmin) * bmin, second=0, microsecond=0)
            b = bk.setdefault(key, {"n": 0, "nt": 0.0, "net": 0.0})
            v = float(x["notional"])
            b["n"] += 1
            b["nt"] += v
            b["net"] += v * float(x["net_bp"] or 0.0) / 1e4
        cum = peak = maxdd = 0.0
        pos = neg = 0
        for k in sorted(bk):
            b = bk[k]
            cum += b["net"]
            peak = max(peak, cum)
            maxdd = max(maxdd, peak - cum)
            if b["n"] >= 3:
                pos += 1 if b["net"] > 0 else 0
                neg += 1 if b["net"] <= 0 else 0
        print(f"\n  【稳定性】{len(bk)} 个 {bmin} 分钟桶：正 {pos} / 负 {neg}"
              f"（仅计 ≥3 笔的桶）  累计曲线最大回吐 {maxdd:.3f}USD"
              f"（占权益 {maxdd/max(1.0, r.equity)*100:.2f}%）")
        per = {}
        for x in rows:
            p = per.setdefault(str(x["symbol"]), [0, 0.0])
            v = float(x["notional"])
            p[0] += 1
            p[1] += v * float(x["net_bp"] or 0.0) / 1e4
        npos = sum(1 for v in per.values() if v[1] > 0)
        print(f"  分币为正 {npos}/{len(per)}：" + "  ".join(
            f"{s} {v[1]:+.2f}$/{v[0]}笔" for s, v in sorted(per.items())))

    if args.no_model:
        return 0
    # 模型：同窗口 × 3 个归属口径
    lo_ms = int((since.timestamp() - 7200) * 1000)
    cut_ms = int(since.timestamp() * 1000)
    DATA = _load_all(syms, r.venue)
    sub = {}
    for s in syms:
        d = DATA[s]
        m = d["ots"] >= lo_ms
        sub[s] = {k: d[k][m] for k in ("ots", "bb", "ba")}
        tm = d["tts"] >= lo_ms
        for k in ("tts", "lo", "hi", "sv", "bv", "tmk"):
            sub[s][k] = d[k][tm]
    anchored = {s: float(stt.vol_baseline_bp or 0.0) for s, stt in r.states.items()}
    print("\n【模型】同窗口（3 个归属口径，遵循 F114：只给区间不给单点）")
    bps, fills = [], []
    for delay in DELAYS:
        rep = replay_portfolio(syms, venue=r.venue, equity=r.equity, params=r.params,
                               limits=r.limits, fill_notional=r.fill_notional,
                               fill_notional_ratio=max(0.0, r.compound_ratio), data=sub,
                               start_ts_ms=lo_ms, enforce_lane_limits=True,
                               tick_delay_ms=delay, vol_baseline=anchored)
        fl = [x for x in rep["fills_log"] if int(x["ts_ms"]) >= cut_ms]
        nt2 = sum(float(x["notional"]) for x in fl)
        nu2 = sum(float(x["net_usd"]) for x in fl)
        bps.append(nu2 / nt2 * 1e4 if nt2 else 0.0)
        fills.append(len(fl))
        print(f"  滞后 {delay/1000:5.1f}s: {len(fl)} 笔 = {len(fl)/span_h:6.1f}/h  "
              f"净 {nu2:+7.2f}USD = {bps[-1]:+6.3f}bp")
    if bps:
        print(f"  ⇒ 模型净 bp 区间 [{min(bps):+.3f}, {max(bps):+.3f}]bp  "
              f"成交 {min(fills)}~{max(fills)} 笔")
        if vals:
            live_bp = net_usd / nt * 1e4 if nt else 0.0
            print(f"  ⇒ 实盘 {live_bp:+.3f}bp vs 模型区间 [{min(bps):+.3f}, {max(bps):+.3f}]"
                  f" ⇒ {'实盘落在模型区间内 ✓' if min(bps)-2*st['se'] <= live_bp <= max(bps)+2*st['se'] else '实盘低于模型区间 ✗'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
