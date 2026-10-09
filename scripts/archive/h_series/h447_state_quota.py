# -*- coding: utf-8 -*-
"""H447 状态配额约束优化：max Σ(边际×腿数) s.t. 腿速 ≥ 60/h。

方法（全部来自 168h 实测数据）：
  1. 分钟采样 + OFI 15s ⇒ 每个样本的状态 = OFI×d（d=逆 r60，线上方向规则）；
  2. 对入场阈 θ 网格（0~0.8）：通过率 p(θ)（∝ 可成交机会/腿速）、
     通过样本的 f60 边际 E[d×Δmid(60s)]（毛），减去实测成本 ⇒ 净；
  3. 腿速换算：以线上实测腿速（同一市场条件下）为单位 1.0，按 p(θ)/p(0.15) 缩放；
  4. 目标 = 净边际/腿 × 腿速；约束 腿速 ≥ 60/h ⇒ 求最优 θ*。
用法: python scripts/h447_state_quota.py [--hours 168] [--live-legs 160]
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
OUT = ROOT / "research_l1" / "out" / "h447_state_quota.json"
CACHE = ROOT / "research_l1" / "out" / "h447_samples_cache.json"
GRID = (0.0, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50, 0.60, 0.80)


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


def load_samples(hours: float):
    """返回 [(fo, y60)]，fo = OFI×d，y60 = d×Δmid(60s)（bp）。带缓存。"""
    if CACHE.exists():
        j = json.loads(CACHE.read_text(encoding="utf-8"))
        if j.get("hours") == hours:
            print(f"用缓存 {CACHE.name}（{len(j['s'])} 样本）", flush=True)
            return [(a, b) for a, b in j["s"]]
    import psycopg
    with psycopg.connect(read_env_dsn(), autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json FROM lane_registry WHERE lane_id='mm_asterdex'")
            m = cur.fetchone()[0]
    syms = [str(s) for s in (m.get("symbols") or []) if str(s)]
    t1 = int(dt.datetime.now(dt.timezone.utc).timestamp() * 1000)
    t0 = t1 - int(hours * 3600 * 1000)
    step = 60000
    series = {}
    with psycopg.connect(read_env_dsn().replace("/alpha_arena", "/alpha_market"),
                         autocommit=True) as cm:
        with cm.cursor() as cur:
            for sym in syms:
                cur.execute("""
                    SELECT (event_ts_ms/1000) AS t_s, (bid_px+ask_px)/2 AS mid
                    FROM asterdex_book_ticker
                    WHERE symbol=%s AND event_ts_ms >= %s AND event_ts_ms <= %s
                      AND bid_px>0 AND ask_px>bid_px AND (event_ts_ms %% %s) < 1500
                    ORDER BY event_ts_ms
                """, (sym + "USDT", t0, t1, step))
                got = {}
                for t_s, mid in cur.fetchall():
                    got[int(t_s) // 60] = (int(t_s), float(mid))
                series[sym] = [got[k] for k in sorted(got)]
                print(f"  {sym}: {len(series[sym])} 点", flush=True)
    ofi = {}
    with psycopg.connect(read_env_dsn().replace("/alpha_arena", "/alpha_market"),
                         autocommit=True) as cm:
        with cm.cursor() as cur:
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
    samples = []
    for sym in syms:
        seq = series[sym]
        n = len(seq)
        for i in range(12, n - 1):
            t_s, mid = seq[i]
            if mid <= 0 or seq[i - 12][1] <= 0 or seq[i + 1][1] <= 0:
                continue
            r60 = (mid - seq[i - 12][1]) / seq[i - 12][1] * 1e4
            if abs(r60) < 1e-9:
                continue
            d = -1.0 if r60 > 0 else 1.0
            f = ofi.get((sym, (t_s // 15) * 15), 0.0)
            y60 = d * (seq[i + 1][1] - mid) / mid * 1e4
            samples.append((f * d, y60))
    print(f"样本 {len(samples)}", flush=True)
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps({"hours": hours, "s": samples}), encoding="utf-8")
    return samples


def main() -> int:
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=168.0)
    ap.add_argument("--live-legs", type=float, default=160.0, help="当前市场条件下的实测腿速")
    ap.add_argument("--cost-bp", type=float, default=2.3, help="每腿成本（毛利→净利差，实测）")
    # [h482 2026-09-29] 基准 θ 可配：原实现把基准写死 0.15（当时的线上值），
    # 而线上现已是 0.5 ⇒ 用 0.15 当基准会把"通过率缩放"算错（腿速被高估 3 倍以上）。
    ap.add_argument("--base-theta", type=float, default=0.15,
                    help="通过率缩放的基准 θ（= 线上现值）")
    a = ap.parse_args()

    s = load_samples(a.hours)
    if len(s) < 1000:
        print("样本不足")
        return 1

    base_p = sum(1 for fo, _ in s if fo >= a.base_theta) / len(s)
    print(f"\n基准：θ={a.base_theta} 通过率 {base_p:.3f}（实测腿速 {a.live_legs}/h 对应）")
    print(f"成本假设 {a.cost_bp}bp/腿（由实测毛/净差估）\n")
    print(f"{'θ':>6}{'通过率':>9}{'腿速/h':>9}{'边际@60s':>11}{'t':>7}{'净/腿':>9}{'总/h':>10}")
    rows = []
    for th in GRID:
        xs = [y for fo, y in s if fo >= th]
        n = len(xs)
        if n < 30:
            continue
        m = sum(xs) / n
        var = sum((x - m) ** 2 for x in xs) / (n - 1)
        t = m / math.sqrt(var / n) if var > 0 else 0.0
        p = n / len(s)
        legs_h = a.live_legs * (p / base_p) if base_p > 0 else 0.0
        net = m - a.cost_bp
        total = net * legs_h
        rows.append({"theta": th, "pass_rate": round(p, 4), "legs_h": round(legs_h, 1),
                     "edge_bp": round(m, 3), "t": round(t, 2),
                     "net_bp": round(net, 3), "total_per_h": round(total, 2)})
        flag = ""
        if legs_h < 60:
            flag = " ← 破频率地板"
        print(f"{th:>6.2f}{p:>9.3f}{legs_h:>9.1f}{m:>+11.2f}{t:>+7.1f}{net:>+9.2f}"
              f"{total:>+10.2f}{flag}")

    # 约束最优：腿速 ≥60 且 净/腿 > 0 中总/h 最大
    feas = [r for r in rows if r["legs_h"] >= 60.0]
    best_all = max(rows, key=lambda r: r["total_per_h"]) if rows else None
    best_feas = max(feas, key=lambda r: r["total_per_h"]) if feas else None
    print(f"\n无约束最优 θ = {best_all['theta']}（总/h {best_all['total_per_h']:+.2f}，"
          f"腿速 {best_all['legs_h']}/h）" if best_all else "")
    print(f"≥60 腿/h 约束下最优 θ = {best_feas['theta']}（总/h {best_feas['total_per_h']:+.2f}，"
          f"腿速 {best_feas['legs_h']}/h，净/腿 {best_feas['net_bp']:+.2f}bp）"
          if best_feas else "无可行解（全部破频率地板）")
    # [h482] "现状"行跟随 --base-theta（原写死 0.15）
    cur_row = min(rows, key=lambda r: abs(r["theta"] - a.base_theta)) if rows else None
    if cur_row:
        print(f"现状 θ={cur_row['theta']}：总/h {cur_row['total_per_h']:+.2f}"
              f"（净/腿 {cur_row['net_bp']:+.2f}bp，腿速 {cur_row['legs_h']}/h）")
        if best_feas:
            print(f"⇒ 提升空间 {best_feas['total_per_h'] - cur_row['total_per_h']:+.2f} bp·腿/h")
    # [h482] 直接给出 **θ=0.5 → 0.7** 的边际对比（本次 ③ 的决策量）：
    #   成本常数在两边相同 ⇒ 该差值**不依赖** cost_bp 的标定，是最稳的读数。
    r50 = min(rows, key=lambda r: abs(r["theta"] - 0.50)) if rows else None
    r70 = min(rows, key=lambda r: abs(r["theta"] - 0.70)) if rows else None
    if r50 and r70 and r50 is not r70:
        print("\n── [③ 决策量] θ 0.5 → 0.7 边际 ──")
        print(f"  毛边际/腿 : {r50['edge_bp']:+.2f} → {r70['edge_bp']:+.2f}bp "
              f"（Δ {r70['edge_bp']-r50['edge_bp']:+.2f}）")
        print(f"  腿速      : {r50['legs_h']:.1f} → {r70['legs_h']:.1f}/h "
              f"（Δ {r70['legs_h']-r50['legs_h']:+.1f}）")
        print(f"  总/h      : {r50['total_per_h']:+.2f} → {r70['total_per_h']:+.2f} "
              f"（Δ {r70['total_per_h']-r50['total_per_h']:+.2f} bp·腿/h）")
        print(f"  θ=0.7 腿速是否仍 ≥60/h 硬约束："
              f"{'是 ✓' if r70['legs_h'] >= 60 else '否 ✗（破约束，不可部署）'}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"hours": a.hours, "live_legs": a.live_legs,
                               "cost_bp": a.cost_bp, "grid": rows,
                               "best_unconstrained": best_all,
                               "best_feasible": best_feas},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已存 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
