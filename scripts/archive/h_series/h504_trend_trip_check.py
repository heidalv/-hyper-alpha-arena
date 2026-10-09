"""h504：趋势闸的**往返级**复核——把"入场腿捕获"换成"整趟往返的盈亏"。

为什么必须重做（h503 的局限）：
  h503 用**入场腿的 net_bp** 比较顺势/逆势，结论是"逆势腿 +1.04bp (t=+4.94) 优于顺势腿
  −0.08bp"。但入场腿的 `net_bp` 只是**填单那一刻的价差捕获**（h496 实测 OPEN +0.31bp、
  ADD −0.25bp），**不等于这趟往返赚不赚**——顺势入场可能捕获小、但后续价格继续走，
  往返反而更好。所以判据必须换成**往返级**。

口径：
  · 往返用 h494 的**带符号累计归零**重建（不依赖 position_id）；
  · 每趟往返的 P&L = Σ(该往返所有腿的 net_bp × notional)/1e4（美元）与
    Σ(net_bp × notional)/Σ(notional)（bp，加权）；
  · 分类依据 = **首腿（OPEN）入场时刻**的 5min 趋势（真实逐笔，按 h488 的 45s 延迟校正），
    与首腿方向比：同向 = 顺势往返，反向 = 逆势往返；
  · 另给出"|趋势| 分档"下的往返表现（检验"越强越好"是否还成立）。

判读：
  · 顺势往返显著更好 ⇒ 闸门正确，频率地板在当前市况下应让步（用户取舍）；
  · 逆势往返更好或不显著 ⇒ 闸门**已过期**，应放宽并按其自身的 ≥60/h 判据试跑。

用法：python scripts/h504_trend_trip_check.py [--hours 48]
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import math
import pathlib
import statistics as st
import sys

sys.stdout.reconfigure(encoding="utf-8")

import psycopg  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
OUT = ROOT / "research_l1" / "out" / "h504_trend_trip.json"
LAG_S = 45


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


def trend_bp(cur, sym, t_ms, lookback_s=300):
    cur.execute(
        "SELECT (ARRAY_AGG(price ORDER BY event_ts_ms ASC))[1]::float8, "
        "(ARRAY_AGG(price ORDER BY event_ts_ms DESC))[1]::float8, count(*) "
        "FROM asterdex_trades WHERE symbol=%s AND event_ts_ms > %s AND event_ts_ms <= %s",
        (sym + "USDT", t_ms - lookback_s * 1000, t_ms))
    r = cur.fetchone()
    if not r or not r[0] or not r[1] or int(r[2] or 0) < 2:
        return None
    return (float(r[1]) - float(r[0])) / float(r[0]) * 1e4


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=48.0)
    a = ap.parse_args()
    with psycopg.connect(read_env_dsn(), autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT ts, symbol, COALESCE(meta_json->>'side',''), "
                "ABS(COALESCE((meta_json->>'qty')::float8,0))::float8, "
                "COALESCE(meta_json->>'exit_path',''), COALESCE(net_bp,0)::float8, "
                "COALESCE(notional,0)::float8 "
                "FROM lane_ledger WHERE lane_id=%s "
                "AND ts > now() - make_interval(hours => %s::int) ORDER BY symbol, ts",
                (LANE, int(a.hours)))
            rows = cur.fetchall()
    by_sym = collections.OrderedDict()
    for r in rows:
        by_sym.setdefault(r[1], []).append(r)
    trips = []
    for sym, rs in by_sym.items():
        cum = 0.0
        peak = 0.0
        t = None
        for ts, _s, side, qty, ep, net, noti in rs:
            signed = abs(qty) if str(side).lower() == "buy" else -abs(qty)
            cum += signed
            peak = max(peak, abs(cum))
            if t is None:
                t = {"sym": sym, "open_ts": ts, "side": str(side).lower(),
                     "usd": 0.0, "noti": 0.0, "legs": 0,
                     "first_side": str(side).lower()}
            t["usd"] += float(net or 0.0) * float(noti or 0.0) / 1e4
            t["noti"] += float(noti or 0.0)
            t["legs"] += 1
            if abs(cum) <= max(1e-6, 1e-4 * max(peak, 1e-9)):
                t["bp_weighted"] = (t["usd"] / t["noti"] * 1e4) if t["noti"] else 0.0
                t["hold"] = (ts - t["open_ts"]).total_seconds()
                trips.append(t)
                t = None
                peak = 0.0
    print(f"近 {a.hours:.0f}h：往返 {len(trips)} 个")
    mk = read_env_dsn().replace("/alpha_arena", "/alpha_market")
    aligned, against, unknown = [], [], []
    with psycopg.connect(mk, autocommit=True) as cm:
        with cm.cursor() as cur:
            for t in trips:
                t_ms = int((t["open_ts"] - dt.timedelta(seconds=LAG_S)).timestamp() * 1000)
                r3 = trend_bp(cur, t["sym"], t_ms)
                if r3 is None:
                    unknown.append(t)
                    continue
                t["r300"] = r3
                sgn = 1.0 if t["first_side"] == "buy" else -1.0
                (aligned if sgn * r3 > 0 else against).append(t)

    def rep(name, xs):
        if len(xs) < 5:
            print(f"  {name:12s} n={len(xs)}（样本不足）")
            return {}
        bp = [x["bp_weighted"] for x in xs]
        usd = [x["usd"] for x in xs]
        m = st.mean(bp)
        sd = st.stdev(bp) if len(bp) > 1 else 0.0
        t_ = m / (sd / math.sqrt(len(bp))) if sd > 0 else 0.0
        print(f"  {name:12s} n={len(xs):4d} 往返bp(加权)={m:+7.2f} (t={t_:+5.2f}) | "
              f"美元/往返={st.mean(usd):+6.3f}$ 合计={sum(usd):+7.2f}$ | "
              f"持仓中位={st.median([x['hold'] for x in xs]):5.0f}s | "
              f"|r300|中位={st.median([abs(x['r300']) for x in xs]):5.1f}bp")
        return {"n": len(xs), "bp": round(m, 3), "t": round(t_, 2),
                "usd_mean": round(st.mean(usd), 4), "usd_total": round(sum(usd), 3),
                "hold_median": round(st.median([x["hold"] for x in xs]), 1)}
    print("=" * 100)
    ra = rep("顺势往返", aligned)
    rg = rep("逆势往返", against)
    ru = rep("无趋势数据", unknown)
    print("=" * 100)
    print("按 |r300| 分档（两侧合并，检验「越强越好」是否成立）：")
    bucket_stats = {}
    both = aligned + against
    for lo, hi in ((0, 10), (10, 20), (20, 40), (40, 1e9)):
        xs = [x for x in both if lo <= abs(x["r300"]) < hi]
        if len(xs) >= 5:
            bp_ = [x["bp_weighted"] for x in xs]
            m = st.mean(bp_)
            sd_ = st.stdev(bp_) if len(bp_) > 1 else 0.0
            t_ = m / (sd_ / math.sqrt(len(bp_))) if sd_ > 0 else 0.0
            lab = f"{lo}-{hi}bp" if hi < 1e8 else f">={lo}bp"
            usd = sum(x["usd"] for x in xs)
            print(f"  |r300| {lab:>9s} n={len(xs):4d} 往返bp={m:+7.2f} (t={t_:+5.2f}) "
                  f"美元/往返={st.mean(x['usd'] for x in xs):+6.3f}$ 合计={usd:+7.2f}$")
            bucket_stats[lab] = {"n": len(xs), "bp": round(m, 3), "t": round(t_, 2),
                                 "usd_total": round(usd, 3)}
    # 单调性判读：最强档 vs 最弱档
    ks = list(bucket_stats)
    mono = None
    if len(ks) >= 2:
        weakest = bucket_stats[ks[0]]
        strongest = bucket_stats[ks[-1]]
        mono = strongest["bp"] - weakest["bp"]
        if mono < -1.0:
            print(f"\n★ **单调反向**：最强档比最弱档差 {mono:+.2f}bp "
                  f"（{strongest['bp']:+.2f} vs {weakest['bp']:+.2f}）"
                  f"⇒ 趋势闸「只在强趋势做」的前提在**本窗口内不成立**")
    if ra and rg:
        d = ra["bp"] - rg["bp"]
        if d > 0.5 and ra["t"] > 1.0:
            verdict = (f"**闸门正确**：顺势往返 {ra['bp']:+.2f}bp 优于逆势 {rg['bp']:+.2f}bp"
                       f"（差 {d:+.2f}bp）⇒ 频率地板在当前市况下应让步（用户取舍点）")
        elif d < -0.5 and rg["t"] > 1.0:
            verdict = (f"**闸门已过期**：逆势往返 {rg['bp']:+.2f}bp（t={rg['t']:+.2f}）"
                       f"显著优于顺势 {ra['bp']:+.2f}bp ⇒ 方向反了，应放宽趋势闸并试跑")
        else:
            verdict = (f"两侧往返差异不显著（{d:+.2f}bp）⇒ 趋势闸的收益在当前市况下"
                       f"无法确认，可考虑放宽以恢复频率（按试跑纪律走）")
    else:
        verdict = "样本不足，无法裁决"
    print("\n⇒ 裁决:", verdict)
    OUT.write_text(json.dumps(
        {"hours": a.hours, "trips": len(trips), "aligned": ra, "against": rg,
         "unknown": ru, "verdict": verdict}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print("已写:", OUT.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
