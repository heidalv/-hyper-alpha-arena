"""h505：**挂宽 ↔ 腿速的弹性**（零部署测量，为 h474 加宽实验定频代价）。

问题：h474（报价几何）是目前唯一没被测过的大杠杆。障碍是频率：
     引擎的挂宽 = `spread_mult × 市场半价差`（实测半宽 ≈0.9bp，`spread_mult=0.5`），
     加宽会让成交率下降——但**降多少没人量过**。
本脚本用**已有的自然变化**来量，不需要部署任何东西：
     市场半价差本身随时间波动 ⇒ 我们的挂宽也随之波动 ⇒ 逐小时对照
     "市场半价差" 与 "引擎腿数/成交捕获"，即可估计弹性。

口径（只读）：
  · 市场半价差：`asterdex_book_ticker`，每小时抽样 1 分钟（5 币，
    `avg((ask−bid)/2/mid)*1e4`），避免全表聚合；
  · 引擎腿数：`lane_ledger` 逐小时计数；另给"入场腿"数；
  · 市场活跃度：`asterdex_trades` 逐小时笔数（控制变量，避免把"市场变冷"
    误当成"价差变宽"的效应）；
  · 回归：`log(腿数) = a + β·log(半价差) + γ·log(成交笔数)`（OLS，双侧 t）。

判读：β 是**腿数对挂宽的弹性**。
  · β ≈ 0  ⇒ 加宽几乎不损失腿速 ⇒ h474 值得做（毛利上去、腿速不动）；
  · β ≈ −1 ⇒ 腿数与挂宽成反比 ⇒ 挂宽翻倍腿速减半 ⇒ 必然破 60/h 硬约束 ⇒
    加宽只能做**小幅**（如 0.5→0.7，预计腿速 ×1.4^β）；
  · β < −1 ⇒ 比反比还陡 ⇒ 加宽极不划算，优先从入场信号侧解决。

用法：python scripts/h505_width_elasticity.py [--hours 168]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import pathlib
import statistics as st
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

import psycopg  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
OUT = ROOT / "research_l1" / "out" / "h505_width_elasticity.json"


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


def ols(y, xs):
    """最小二乘 y = b0 + Σ bi·xi（含截距），返回系数与各自 t。"""
    n = len(y)
    k = len(xs) + 1
    X = [[1.0] + [xs[j][i] for j in range(len(xs))] for i in range(n)]
    # 正规方程 (X'X)b = X'y
    XtX = [[sum(X[i][a] * X[i][b] for i in range(n)) for b in range(k)] for a in range(k)]
    Xty = [sum(X[i][a] * y[i] for i in range(n)) for a in range(k)]
    # 高斯消元
    A = [row[:] + [Xty[i]] for i, row in enumerate(XtX)]
    for c in range(k):
        p = max(range(c, k), key=lambda r: abs(A[r][c]))
        A[c], A[p] = A[p], A[c]
        if abs(A[c][c]) < 1e-12:
            return None
        for r in range(k):
            if r != c:
                f = A[r][c] / A[c][c]
                for cc in range(c, k + 1):
                    A[r][cc] -= f * A[c][cc]
    beta = [A[i][k] / A[i][i] for i in range(k)]
    resid = [y[i] - sum(beta[j] * X[i][j] for j in range(k)) for i in range(n)]
    s2 = sum(r * r for r in resid) / max(n - k, 1)
    # 对角逆（用于 se）
    se = []
    for j in range(k):
        # 解 (X'X) v = e_j
        e = [1.0 if i == j else 0.0 for i in range(k)]
        B = [row[:] + [e[i]] for i, row in enumerate(XtX)]
        for c in range(k):
            piv = max(range(c, k), key=lambda r: abs(B[r][c]))
            B[c], B[piv] = B[piv], B[c]
            if abs(B[c][c]) < 1e-12:
                se.append(float("nan"))
                break
            for r in range(k):
                if r != c:
                    f = B[r][c] / B[c][c]
                    for cc in range(c, k + 1):
                        B[r][cc] -= f * B[c][cc]
        else:
            v = [B[i][k] / B[i][i] for i in range(k)]
            se.append(math.sqrt(max(s2 * v[j], 0.0)))
    t = [beta[j] / se[j] if se[j] and se[j] == se[j] and se[j] > 0 else float("nan")
         for j in range(k)]
    ybar = sum(y) / n
    sst = sum((v - ybar) ** 2 for v in y)
    sse = sum(r * r for r in resid)
    r2 = 1 - sse / sst if sst > 0 else float("nan")
    return {"beta": beta, "se": se, "t": t, "r2": r2, "n": n}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=168.0)
    a = ap.parse_args()
    dsn = read_env_dsn()
    with psycopg.connect(dsn, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json->'symbols' FROM lane_registry WHERE lane_id=%s",
                        (LANE,))
            r = cur.fetchone()
            syms = [str(s) for s in (r[0] if r and isinstance(r[0], list) else [])]
            cur.execute(
                "SELECT date_trunc('hour', ts) AS h, count(*), "
                " count(*) FILTER (WHERE COALESCE(meta_json->>'exit_path','')='') "
                "FROM lane_ledger WHERE lane_id=%s "
                "AND ts > now() - make_interval(hours => %s::int) GROUP BY 1 ORDER BY 1",
                (LANE, int(a.hours)))
            eng = {int(h.timestamp()): (int(n), int(ne)) for h, n, ne in cur.fetchall()}
    if not eng:
        print("无引擎数据")
        return 1
    mk = dsn.replace("/alpha_arena", "/alpha_market")
    hours = sorted(eng)
    print(f"近 {a.hours:.0f}h：{len(hours)} 个小时桶，在役币 {syms}")
    rows = []
    t_start = time.time()
    with psycopg.connect(mk, autocommit=True) as cm:
        with cm.cursor() as cur:
            for hts in hours:
                t0 = hts * 1000 + 30 * 60 * 1000     # 每小时取中间 60s
                cur.execute(
                    "SELECT AVG((ask_px-bid_px)/2.0/NULLIF((ask_px+bid_px)/2.0,0))*1e4 "
                    "FROM asterdex_book_ticker WHERE symbol = ANY(%s) "
                    "AND event_ts_ms > %s AND event_ts_ms <= %s "
                    "AND bid_px > 0 AND ask_px > bid_px",
                    ([s + "USDT" for s in syms], t0, t0 + 60000))
                sp = cur.fetchone()[0]
                cur.execute(
                    "SELECT count(*) FROM asterdex_trades WHERE symbol = ANY(%s) "
                    "AND event_ts_ms > %s AND event_ts_ms <= %s",
                    ([s + "USDT" for s in syms], t0 - 60 * 1000, t0 + 60 * 60 * 1000))
                tr = int(cur.fetchone()[0] or 0)
                if sp is None or float(sp) <= 0:
                    continue
                n, ne = eng[hts]
                rows.append({"hour": hts, "half_spread_bp": float(sp),
                             "mkt_trades": tr, "legs": n, "entries": ne,
                             "legs_per_1000_trades": (n / tr * 1000) if tr else None})
    print(f"（取数用时 {time.time()-t_start:.0f}s，有效小时 {len(rows)}）")
    print("=" * 96)
    print(f"{'小时(本地)':>13s} {'市场半价差bp':>12s} {'市场笔/h':>9s} {'引擎腿':>7s} "
          f"{'入场腿':>7s} {'腿/千笔':>8s}")
    for r in rows[-28:]:
        print(f"{dt.datetime.fromtimestamp(r['hour']).strftime('%m-%d %H:%M'):>13s} "
              f"{r['half_spread_bp']:12.2f} {r['mkt_trades']:9d} {r['legs']:7d} "
              f"{r['entries']:7d} "
              f"{(r['legs_per_1000_trades'] or float('nan')):8.2f}")
    # 回归
    use = [r for r in rows if r["legs"] > 0 and r["mkt_trades"] > 0]
    if len(use) < 20:
        print("有效小时不足 20，跳过回归")
        return 0
    y = [math.log(r["legs"]) for r in use]
    x1 = [math.log(r["half_spread_bp"]) for r in use]
    x2 = [math.log(r["mkt_trades"]) for r in use]
    fit = ols(y, [x1, x2])
    print("=" * 96)
    if not fit:
        print("回归失败（矩阵奇异）")
        return 1
    names = ["截距", "log(半价差) β", "log(市场笔数) γ"]
    for i, nm in enumerate(names):
        print(f"  {nm:16s} 系数={fit['beta'][i]:+8.3f}  se={fit['se'][i]:.3f}  "
              f"t={fit['t'][i]:+6.2f}")
    print(f"  R² = {fit['r2']:.3f}   n = {fit['n']}")
    beta = fit["beta"][1]
    print(f"\n⇒ **腿数对挂宽的弹性 β = {beta:+.2f}**"
          f"（控制市场活跃度后）")
    for mult, lab in ((1.2, "0.5→0.6"), (1.4, "0.5→0.7"), (2.0, "0.5→1.0"), (2.4, "0.5→1.2")):
        print(f"   若挂宽 ×{mult}（spread_mult {lab}）⇒ 预计腿速 ×{mult**beta:.2f}"
              f"（当前 70/h ⇒ {70*mult**beta:.0f}/h）")
    if beta > -0.3:
        verdict = (f"β={beta:+.2f} 接近 0 ⇒ **加宽几乎不损失腿速** ⇒ h474 应直接做，"
                   f"优先试较大幅度（如 0.5→1.0）")
    elif beta > -0.9:
        verdict = (f"β={beta:+.2f} 明显为负但不极端 ⇒ 加宽有代价，"
                   f"建议**小步**（0.5→0.7，预计腿速 ×{1.4**beta:.2f}）并盯 ≥60/h")
    else:
        verdict = (f"β={beta:+.2f} 比反比还陡 ⇒ 加宽会破频率硬约束 ⇒ "
                   f"优先从**入场信号侧**（③ 方向）要质量，别动挂宽")
    print("\n⇒ 裁决:", verdict)
    OUT.write_text(json.dumps(
        {"hours": len(rows), "beta": round(beta, 3), "gamma": round(fit["beta"][2], 3),
         "t_beta": round(fit["t"][1], 2), "r2": round(fit["r2"], 3),
         "rows": rows[-48:], "verdict": verdict}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print("已写:", OUT.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
