# -*- coding: utf-8 -*-
"""H449 regime 维度的约束优化：σ 与 |r300| 的分位门槛是否值得加（频率代价 vs 边际增益）。

样本（168h，分钟采样，特征只用 t 时刻及之前）：
    fo    = OFI × d          （d = 逆 r60，线上方向）
    y60   = d × Δmid(60s)    （bp，可交易边际）
    sigma = 20 期已实现波动 / 滚动中位 − 1
    ar300 = |r300|（bp）
输出：
    1) 各维度的分位桶边际（毛）与通过率；
    2) 联合规则搜索：fo≥θ 且 σ 区间 且 ar300 区间 ⇒ 净/腿、腿速、总/h；
    3) 与现行闸门（trend_pause_bp=15、vol_pause_sigma=1.5）的对照。
用法: python scripts/h449_regime_quota.py [--hours 168] [--live-legs 160] [--cost-bp 2.3]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "research_l1" / "out" / "h449_regime_quota.json"
CACHE = ROOT / "research_l1" / "out" / "h449_features_cache.json"


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


def load(hours: float):
    if CACHE.exists():
        j = json.loads(CACHE.read_text(encoding="utf-8"))
        if j.get("hours") == hours:
            print(f"用缓存（{len(j['s'])} 样本）", flush=True)
            return j["s"]
    import psycopg
    with psycopg.connect(read_env_dsn(), autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json FROM lane_registry WHERE lane_id='mm_asterdex'")
            m = cur.fetchone()[0]
    syms = [str(s) for s in (m.get("symbols") or []) if str(s)]
    t1 = int(dt.datetime.now(dt.timezone.utc).timestamp() * 1000)
    t0 = t1 - int(hours * 3600 * 1000)
    series, ofi = {}, {}
    with psycopg.connect(read_env_dsn().replace("/alpha_arena", "/alpha_market"),
                         autocommit=True) as cm:
        with cm.cursor() as cur:
            for sym in syms:
                cur.execute("""
                    SELECT (event_ts_ms/1000) AS t_s, (bid_px+ask_px)/2 AS mid
                    FROM asterdex_book_ticker
                    WHERE symbol=%s AND event_ts_ms >= %s AND event_ts_ms <= %s
                      AND bid_px>0 AND ask_px>bid_px AND (event_ts_ms %% 60000) < 1500
                    ORDER BY event_ts_ms
                """, (sym + "USDT", t0, t1))
                got = {}
                for t_s, mid in cur.fetchall():
                    got[int(t_s) // 60] = (int(t_s), float(mid))
                series[sym] = [got[k] for k in sorted(got)]
                print(f"  {sym}: {len(series[sym])} 点", flush=True)
            for sym in syms:
                cur.execute("""
                    SELECT timestamp, COALESCE(SUM(taker_buy_notional),0),
                           COALESCE(SUM(taker_sell_notional),0)
                    FROM market_trades_aggregated
                    WHERE exchange='asterdex' AND symbol=%s
                      AND timestamp >= %s AND timestamp <= %s
                    GROUP BY timestamp ORDER BY timestamp
                """, (sym, t0, t1))
                for ts_ms, bv, sv in cur.fetchall():
                    tot = float(bv) + float(sv)
                    if tot > 0:
                        ofi[(sym, int(ts_ms) // 1000)] = (float(bv) - float(sv)) / tot
    s = []
    for sym in syms:
        seq = series[sym]
        n = len(seq)
        mids = [p[1] for p in seq]
        vols = []
        for i in range(20, n):
            if mids[i - 20] > 0:
                vols.append(abs(mids[i] - mids[i - 20]) / mids[i - 20] * 1e4)
        base = sorted(vols)[len(vols) // 2] if vols else 0.0
        if base <= 0:
            continue
        for i in range(60, n - 1):
            t_s, mid = seq[i]
            if mid <= 0 or mids[i - 12] <= 0 or mids[i - 60] <= 0 or mids[i + 1] <= 0:
                continue
            r60 = (mid - mids[i - 12]) / mids[i - 12] * 1e4
            if abs(r60) < 1e-9:
                continue
            d = -1.0 if r60 > 0 else 1.0
            f = ofi.get((sym, (t_s // 15) * 15), 0.0)
            sigma = (abs(mid - mids[i - 20]) / mids[i - 20] * 1e4) / base - 1.0
            ar300 = abs((mid - mids[i - 60]) / mids[i - 60] * 1e4)
            y60 = d * (mids[i + 1] - mid) / mid * 1e4
            s.append([round(f * d, 4), round(sigma, 4), round(ar300, 3), round(y60, 4)])
    print(f"样本 {len(s)}", flush=True)
    CACHE.write_text(json.dumps({"hours": hours, "s": s}), encoding="utf-8")
    return s


def _st(xs):
    n = len(xs)
    if n < 20:
        return None
    m = sum(xs) / n
    var = sum((x - m) ** 2 for x in xs) / (n - 1)
    return {"n": n, "mean": round(m, 3),
            "t": round(m / math.sqrt(var / n), 2) if var > 0 else 0.0}


def main() -> int:
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=168.0)
    ap.add_argument("--live-legs", type=float, default=160.0)
    ap.add_argument("--cost-bp", type=float, default=2.3)
    a = ap.parse_args()

    s = load(a.hours)
    if len(s) < 1000:
        print("样本不足")
        return 1
    base_p = sum(1 for r in s if r[0] >= 0.5) / len(s)   # h448 后的现行基准 θ=0.5
    print(f"\n基准 θ=0.5 通过率 {base_p:.3f}（腿速基准 {a.live_legs}/h）")
    res = {"hours": a.hours, "base_pass": round(base_p, 4), "dims": {}}

    # 1) σ 分位桶（在 θ=0.5 通过的样本内）
    sub = [r for r in s if r[0] >= 0.5]
    print(f"\n== σ 桶（θ≥0.5 内，n={len(sub)}）==")
    sgs = sorted(r[1] for r in sub)
    qs = [sgs[int(len(sgs) * q)] for q in (0.25, 0.5, 0.75)]
    buckets = [("σ<q25", lambda x: x < qs[0]), ("q25-50", lambda x: qs[0] <= x < qs[1]),
               ("q50-75", lambda x: qs[1] <= x < qs[2]), ("σ>q75", lambda x: x >= qs[2])]
    dims_sigma = {}
    for nm, fn in buckets:
        xs = [r[3] for r in sub if fn(r[1])]
        st = _st(xs)
        share = len(xs) / max(1, len(sub))
        dims_sigma[nm] = {"n": st["n"], "mean": st["mean"], "t": st["t"],
                          "share": round(share, 3)}
        print(f"  {nm:<8} n={st['n']:>5} share={share:5.2f} 边际={st['mean']:+.2f}bp t={st['t']:+.1f}")
    res["dims"]["sigma"] = {"q": [round(q, 4) for q in qs], "buckets": dims_sigma}

    # 2) |r300| 分位桶
    print(f"\n== |r300| 桶（θ≥0.5 内）==")
    ars = sorted(r[2] for r in sub)
    qa = [ars[int(len(ars) * q)] for q in (0.25, 0.5, 0.75)]
    buckets_a = [("|r300|<q25", lambda x: x < qa[0]), ("q25-50", lambda x: qa[0] <= x < qa[1]),
                 ("q50-75", lambda x: qa[1] <= x < qa[2]), ("|r300|>q75", lambda x: x >= qa[2])]
    dims_ar = {}
    for nm, fn in buckets_a:
        xs = [r[3] for r in sub if fn(r[2])]
        st = _st(xs)
        share = len(xs) / max(1, len(sub))
        dims_ar[nm] = {"n": st["n"], "mean": st["mean"], "t": st["t"], "share": round(share, 3)}
        print(f"  {nm:<12} n={st['n']:>5} share={share:5.2f} 边际={st['mean']:+.2f}bp t={st['t']:+.1f}")
    res["dims"]["absr300"] = {"q": [round(q, 3) for q in qa], "buckets": dims_ar}

    # 3) 联合规则搜索（θ 固定 0.5，加 σ 上界 / |r300| 上界）
    print(f"\n== 联合规则（θ≥0.5 + 上界）==")
    print(f"{'规则':<26}{'通过率':>9}{'腿速/h':>9}{'毛边际':>10}{'t':>7}{'净/腿':>9}{'总/h':>9}")
    rules = []
    for sig_cap in (None, 2.0, 1.5, 1.0, 0.5):
        for ar_cap in (None, 20.0, 15.0, 10.0):
            xs = [r[3] for r in s if r[0] >= 0.5
                  and (sig_cap is None or r[1] <= sig_cap)
                  and (ar_cap is None or r[2] <= ar_cap)]
            st = _st(xs)
            if not st:
                continue
            p = st["n"] / len(s)
            legs = a.live_legs * (p / base_p)
            net = st["mean"] - a.cost_bp
            rules.append({"sig_cap": sig_cap, "ar_cap": ar_cap, "pass": round(p, 4),
                          "legs_h": round(legs, 1), "edge": st["mean"], "t": st["t"],
                          "net_bp": round(net, 3), "total": round(net * legs, 2)})
    for r in rules:
        nm = f"θ0.5 σ≤{r['sig_cap']} |r300|≤{r['ar_cap']}"
        flag = " ←破60" if r["legs_h"] < 60 else ""
        print(f"{nm:<26}{r['pass']:>9.3f}{r['legs_h']:>9.1f}{r['edge']:>+10.2f}"
              f"{r['t']:>+7.1f}{r['net_bp']:>+9.2f}{r['total']:>+9.1f}{flag}")
    feas = [r for r in rules if r["legs_h"] >= 60]
    best = max(feas, key=lambda r: r["total"]) if feas else None
    cur = next((r for r in rules if r["sig_cap"] is None and r["ar_cap"] is None), None)
    if best and cur:
        print(f"\n最优可行：σ≤{best['sig_cap']} |r300|≤{best['ar_cap']} ⇒ "
              f"净 {best['net_bp']:+.2f}bp/腿、腿速 {best['legs_h']}/h、总 {best['total']:+.1f}")
        print(f"对照现状（无 regime 上界）：净 {cur['net_bp']:+.2f}bp/腿、腿速 {cur['legs_h']}/h、"
              f"总 {cur['total']:+.1f} ⇒ 提升 {best['total']-cur['total']:+.1f}")
    res["rules"] = rules
    res["best_feasible"] = best
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已存 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
