# -*- coding: utf-8 -*-
"""H456 报价几何（修正版）：触及用窗口内路径、结果用到期值 —— 两者分离。

修正 H455 的循环论证（触及条件与结果同为 y60 ⇒ 机械为负 ✗）。
口径（5s 采样，48h）：
  · t 时刻状态：OFI×d（d=逆 r60，15s 桶）、|r300| 分位；
  · 挂单：距中价 δ bp（逆势方向 d）；
  · **触及** = 未来 60s 内 5s 中价的最小不利偏移 ≥ δ（路径量，与到期值分离）；
  · **结果** = d×(mid_{t+60s} − mid_t)（到期量）；
  · EV(δ,s) = P(触及|s) × [ δ + E(结果 | 触及, s) ]
用法: python scripts/h456_quote_geometry_v2.py [--hours 48]
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
OUT = ROOT / "research_l1" / "out" / "h456_quote_geometry_v2.json"
CACHE = ROOT / "research_l1" / "out" / "h456_grid5_cache.json"
DELTAS = (1.0, 2.0, 3.0, 5.0, 8.0, 12.0, 20.0)


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
            print(f"用缓存（{sum(len(v) for v in j['grids'].values())} 点）", flush=True)
            return j["grids"], j["ofi"]
    import psycopg
    with psycopg.connect(read_env_dsn(), autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json FROM lane_registry WHERE lane_id='mm_asterdex'")
            syms = [str(s) for s in (cur.fetchone()[0].get("symbols") or []) if str(s)]
    t1 = int(dt.datetime.now(dt.timezone.utc).timestamp() * 1000)
    t0 = t1 - int(hours * 3600 * 1000)
    grids, ofi = {}, {}
    with psycopg.connect(read_env_dsn().replace("/alpha_arena", "/alpha_market"),
                         autocommit=True) as cm:
        with cm.cursor() as cur:
            for sym in syms:
                cur.execute("""
                    SELECT (event_ts_ms/1000) AS t_s, (bid_px+ask_px)/2 AS mid
                    FROM asterdex_book_ticker
                    WHERE symbol=%s AND event_ts_ms >= %s AND event_ts_ms <= %s
                      AND bid_px>0 AND ask_px>bid_px AND (event_ts_ms %% 5000) < 400
                    ORDER BY event_ts_ms
                """, (sym + "USDT", t0, t1))
                g = {}
                for t_s, mid in cur.fetchall():
                    g[int(t_s) // 5 * 5] = float(mid)
                grids[sym] = {k: g[k] for k in sorted(g)}
                print(f"  {sym}: {len(g)} 点（5s 网格）", flush=True)
            for sym in syms:
                cur.execute("""
                    SELECT timestamp, COALESCE(SUM(taker_buy_notional),0),
                           COALESCE(SUM(taker_sell_notional),0)
                    FROM market_trades_aggregated
                    WHERE exchange='asterdex' AND symbol=%s
                      AND timestamp >= %s AND timestamp <= %s
                    GROUP BY timestamp ORDER BY timestamp
                """, (sym, t0, t1))
                d = {}
                for ts_ms, bv, sv in cur.fetchall():
                    tot = float(bv) + float(sv)
                    if tot > 0:
                        d[int(ts_ms) // 1000] = (float(bv) - float(sv)) / tot
                ofi[sym] = d
    CACHE.write_text(json.dumps({"hours": hours, "grids": grids, "ofi": ofi}),
                     encoding="utf-8")
    return grids, ofi


def main() -> int:
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=48.0)
    a = ap.parse_args()

    grids, ofi = load(a.hours)
    samples = []   # (state_key, d, path_min_adv_bp, y60_bp)
    for sym, g in grids.items():
        ts = sorted(g)
        if len(ts) < 200:
            continue
        idx = {t: i for i, t in enumerate(ts)}
        for i in range(60, len(ts) - 12):
            t = ts[i]
            mid = g[t]
            m60 = g.get(ts[i - 12])
            if not m60 or m60 <= 0 or mid <= 0:
                continue
            r60 = (mid - m60) / m60 * 1e4
            if abs(r60) < 1e-9:
                continue
            d = -1.0 if r60 > 0 else 1.0
            f = ofi.get(sym, {}).get((t // 15) * 15, 0.0)
            fo = f * d
            # 未来 60s（12 步）路径的最小不利偏移与到期值
            adv = 0.0
            for k in range(1, 13):
                mk = g.get(ts[i + k])
                if mk:
                    adv = min(adv, d * (mk - mid) / mid * 1e4)
            y60 = d * (g[ts[i + 12]] - mid) / mid * 1e4
            samples.append((fo, adv, y60))
    print(f"样本 {len(samples)}", flush=True)
    if len(samples) < 500:
        print("样本不足")
        return 1

    def stat(name, sel):
        n = len(sel)
        if n < 30:
            return None
        m = sum(sel) / n
        var = sum((x - m) ** 2 for x in sel) / (n - 1)
        return {"n": n, "mean": round(m, 3),
                "t": round(m / math.sqrt(var / n), 2) if var > 0 else 0.0}

    res = {"hours": a.hours, "n": len(samples), "buckets": {}}
    print(f"\n== EV(δ, state)：触及=路径最小不利偏移≥δ；结果=60s 到期值 ==")
    for conf_lo, label in ((0.5, "confirm(θ≥0.5)"), (0.0, "弱确认"), (-1.1, "逆流")):
        if label == "弱确认":
            sel = [s for s in samples if 0.0 <= s[0] < 0.5]
        elif label == "逆流":
            sel = [s for s in samples if s[0] < 0.0]
        else:
            sel = [s for s in samples if s[0] >= conf_lo]
        print(f"\n-- {label} (n={len(sel)}) --")
        evs = {}
        for d in DELTAS:
            touched = [s[1] for s in sel if s[1] <= -d]        # 路径触及
            outs = [s[2] for s in sel if s[1] <= -d]           # 到期结果（分离）
            if len(touched) < 30:
                continue
            p = len(touched) / len(sel)
            cond = sum(outs) / len(outs)
            ev = p * (d + cond)
            evs[d] = round(ev, 4)
            print(f"   δ={d:>5.0f}bp  P(触及)={p:5.2f}  E(到期|触及)={cond:+7.2f}bp  "
                  f"EV={ev:+7.3f}bp/次")
        best = max(evs, key=lambda k: evs[k]) if evs else None
        print(f"   ⇒ δ* = {best}bp（EV={evs.get(best)}）")
        res["buckets"][label] = {"n": len(sel), "ev": evs, "delta_star": best}
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已存 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
