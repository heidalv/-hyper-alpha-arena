# -*- coding: utf-8 -*-
"""H445 数学层第二/三项：
  A. 非重叠窗口复核 confirm 态最优持有期（严格 t 值；f120/f300 按步长子采样）
  B. 价差-波动耦合 spread(σ) 的系数拟合（检验 A-S 形式 spread = γ·σ 还是 γ·σ²）
  C. 库存消融的可辨识形式初探（用账本持仓时长 vs 之后的价格路径）

数据：book_ticker 分钟采样 + OFI 15s 桶（168h，当前宇宙）。
用法: python scripts/h445_horizon_spread.py [--hours 168]
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
OUT = ROOT / "research_l1" / "out" / "h445_horizon_spread.json"
HORIZONS = ((6, "f30", 1), (12, "f60", 1), (24, "f120", 2), (60, "f300", 5))


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


def _stats(xs):
    n = len(xs)
    if n < 5:
        return None
    m = sum(xs) / n
    var = sum((x - m) ** 2 for x in xs) / (n - 1)
    if var <= 0:
        return {"n": n, "mean": round(m, 3), "t": 0.0}
    return {"n": n, "mean": round(m, 3), "t": round(m / math.sqrt(var / n), 2)}


def _ols1(xs, ys):
    """一元线性回归 y = a + b x（返回 a,b,t_b,r2,n）。"""
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    if sxx <= 0:
        return None
    b = sxy / sxx
    a = my - b * mx
    resid = sum((y - a - b * x) ** 2 for x, y in zip(xs, ys))
    syy = sum((y - my) ** 2 for y in ys)
    r2 = 1 - resid / syy if syy > 0 else 0.0
    se = math.sqrt(resid / (n - 2) / sxx) if n > 2 else float("inf")
    t = b / se if se not in (0, float("inf")) else 0.0
    return {"a": round(a, 4), "b": round(b, 4), "t_b": round(t, 2),
            "r2": round(r2, 4), "n": n}


def main() -> int:
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=168.0)
    ap.add_argument("--sample-sec", type=int, default=60)
    a = ap.parse_args()

    import psycopg
    with psycopg.connect(read_env_dsn(), autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json FROM lane_registry WHERE lane_id='mm_asterdex'")
            m = cur.fetchone()[0]
    syms = [str(s) for s in (m.get("symbols") or []) if str(s)]
    print(f"宇宙 {syms}，{a.hours}h，{a.sample_sec}s 采样", flush=True)

    t1 = int(dt.datetime.now(dt.timezone.utc).timestamp() * 1000)
    t0 = t1 - int(a.hours * 3600 * 1000)
    step_ms = a.sample_sec * 1000
    series = {}
    with psycopg.connect(read_env_dsn().replace("/alpha_arena", "/alpha_market"),
                         autocommit=True) as cm:
        with cm.cursor() as cur:
            for sym in syms:
                cur.execute("""
                    SELECT (event_ts_ms / 1000) AS t_s,
                           (bid_px + ask_px) / 2 AS mid,
                           (ask_px - bid_px) / ((bid_px + ask_px) / 2) * 1e4 AS sp_bp
                    FROM asterdex_book_ticker
                    WHERE symbol=%s AND event_ts_ms >= %s AND event_ts_ms <= %s
                      AND bid_px > 0 AND ask_px > bid_px
                      AND (event_ts_ms %% %s) < 1500
                    ORDER BY event_ts_ms
                """, (sym + "USDT", t0, t1, step_ms))
                got = {}
                for t_s, mid, sp in cur.fetchall():
                    got[int(t_s) // a.sample_sec] = (int(t_s), float(mid), float(sp or 0.0))
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

    # ── A. 非重叠窗口的 confirm 态边际 ────────────────────────────────
    print("\n===== A. confirm 态 E[d×Δmid(h)]（非重叠子采样）=====")
    res = {"horizons": {}}
    for steps, hname, stride in HORIZONS:
        edge = []
        for sym in syms:
            seq = series[sym]
            n = len(seq)
            if n < 40:
                continue
            for i in range(20, n - steps, stride):     # 步长=horizon ⇒ 不重叠
                t_s, mid, _sp = seq[i]
                if mid <= 0 or seq[i - 12][1] <= 0 or seq[i + steps][1] <= 0:
                    continue
                r60 = (mid - seq[i - 12][1]) / seq[i - 12][1] * 1e4
                if abs(r60) < 1e-9:
                    continue
                d = -1.0 if r60 > 0 else 1.0
                f = ofi.get((sym, (t_s // 15) * 15), 0.0)
                if f * d < 0.15:                        # 只看顺势确认桶
                    continue
                edge.append(d * (seq[i + steps][1] - mid) / mid * 1e4)
        st = _stats(edge)
        res["horizons"][hname] = st
        print(f"  {hname}: n={st['n'] if st else 0}  "
              f"mean={st['mean'] if st else 0:+.2f}bp  t={st['t'] if st else 0:+.2f}")

    # ── B. spread(σ) 耦合 ────────────────────────────────────────────
    print("\n===== B. 价差-波动耦合 spread = a + b·σ（及 σ² 形式）=====")
    xs_lin, xs_sq, ys = [], [], []
    for sym in syms:
        seq = series[sym]
        n = len(seq)
        if n < 40:
            continue
        vols = []
        for i in range(20, n):
            seg = [p[1] for p in seq[i - 20:i + 1]]
            if seg[0] > 0:
                vols.append(abs(seg[-1] - seg[0]) / seg[0] * 1e4)
        base = sorted(vols)[len(vols) // 2] if vols else 0.0
        if base <= 0:
            continue
        for i in range(20, n):
            seg = [p[1] for p in seq[i - 20:i + 1]]
            vol = abs(seg[-1] - seg[0]) / seg[0] * 1e4
            sg = vol / base - 1.0
            sp = seq[i][2]
            if sp <= 0:
                continue
            xs_lin.append(sg)
            xs_sq.append(sg * sg)
            ys.append(sp)
    fit_lin = _ols1(xs_lin, ys)
    fit_sq = _ols1(xs_sq, ys)
    print(f"  线性  spread = {fit_lin['a']} + {fit_lin['b']}·σ     "
          f"t={fit_lin['t_b']} R²={fit_lin['r2']} n={fit_lin['n']}")
    print(f"  平方  spread = {fit_sq['a']} + {fit_sq['b']}·σ²    "
          f"t={fit_sq['t_b']} R²={fit_sq['r2']} n={fit_sq['n']}")
    res["spread_sigma"] = {"linear": fit_lin, "square": fit_sq}

    # ── C. 库存消融初探：持仓时长 vs 之后的回归路径 ────────────────────
    print("\n===== C. 库存消融初探（账本：持仓时长分布与出场路径）=====")
    with psycopg.connect(read_env_dsn(), autocommit=True) as cc:
        with cc.cursor() as cur:
            cur.execute("""
                SELECT COALESCE(NULLIF(meta_json->>'exit_path',''),'(round)'), COUNT(*),
                       ROUND(AVG(net_bp)::numeric,2)
                FROM lane_ledger
                WHERE event='fill' AND ts >= now() - interval '24 hours'
                GROUP BY 1 ORDER BY 2 DESC
            """)
            paths = [[r[0], r[1], float(r[2] or 0)] for r in cur.fetchall()]
    for p in paths:
        print(f"  {p[0]:<24} n={p[1]:>5} 均={p[2]:>+7}bp")
    res["exit_paths_24h"] = paths

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已存 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
