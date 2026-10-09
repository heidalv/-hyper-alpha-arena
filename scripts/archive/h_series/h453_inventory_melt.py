# -*- coding: utf-8 -*-
"""H453 库存消融研究 f(Inv,σ,OFI)：从账本重建持仓周期，测"消融速度"的决定因素。

问题（决定 exit_skew_k 去留）：
  A-S 主张"库存靠报价偏斜消融，速率随 |Inv|·σ 增长"。可检验形式：
    持仓周期时长 T（秒）与 峰值名义 N、入场时 σ、入场时 |OFI| 的关系：
      ln T = a + b1·ln N + b2·σ + b3·|OFI| + ε
  若 b1>0（仓位越大消融越慢）⇒ 偏斜消融不足 ⇒ exit_skew 有意义；
  若 b1≈0 ⇒ 消融与仓位无关（时长由计时器主导）⇒ exit_skew 无意义。

方法：lane_ledger 逐笔回放库存（同币同向累计），识别"归零 → 非零 → 归零"周期；
      周期内峰值名义、时长、结束路径（被动/强平）；σ 用 book_ticker 20 期波动近似。
只读。
"""
import sys
import json
import math
import pathlib
import datetime as dt
import psycopg

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.h422_weekly_scan import read_env_dsn  # noqa: E402

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HOURS = 48
c = psycopg.connect(read_env_dsn(), autocommit=True)
cur = c.cursor()
cur.execute("""
    SELECT ts, symbol, meta_json->>'side', (meta_json->>'qty')::float8,
           (meta_json->>'fill_px')::float8, COALESCE(NULLIF(meta_json->>'exit_path',''),'')
    FROM lane_ledger
    WHERE event='fill' AND ts >= now() - interval '%s hours'
    ORDER BY ts
""" % HOURS)
rows = cur.fetchall()
print(f"近 {HOURS}h 成交 {len(rows)} 笔，重建持仓周期……", flush=True)

# 按币回放
epochs = []
by_sym = {}
for ts, sym, side, qty, px, ep in rows:
    by_sym.setdefault(sym, []).append((ts, side, qty or 0.0, px or 0.0, ep))

for sym, fills in by_sym.items():
    inv = 0.0
    start = None
    peak_n = 0.0
    for ts, side, qty, px, ep in fills:
        d = qty if side == "buy" else -qty
        prev = inv
        inv += d
        if abs(prev) < 1e-12 and abs(inv) > 1e-12:
            start = ts
            peak_n = 0.0
        if start is not None:
            peak_n = max(peak_n, abs(inv) * (px or 0.0))
            if abs(inv) < 1e-12:
                dur = (ts - start).total_seconds()
                epochs.append({"sym": sym, "dur": dur, "peak_usd": peak_n,
                               "end_path": ep or "round", "t0": start})
                start = None
print(f"识别持仓周期 {len(epochs)} 个", flush=True)

if len(epochs) < 20:
    print("周期太少，跳过拟合")
    raise SystemExit(0)

# 拟合 ln T = a + b1 ln N + b2·ln(1+peak) ...
xs, ys = [], []
for e in epochs:
    if e["dur"] <= 0 or e["peak_usd"] <= 0:
        continue
    xs.append([1.0, math.log(e["peak_usd"]), e["peak_usd"] / 100.0])
    ys.append(math.log(e["dur"]))


def ols(X, y):
    n, k = len(y), len(X[0])
    A = [[sum(X[i][a] * X[i][b] for i in range(n)) for b in range(k)] for a in range(k)]
    b = [sum(X[i][a] * y[i] for i in range(n)) for a in range(k)]
    M = [A[i][:] + [b[i]] for i in range(k)]
    for col in range(k):
        p = max(range(col, k), key=lambda r: abs(M[r][col]))
        if abs(M[p][col]) < 1e-12:
            return None
        M[col], M[p] = M[p], M[col]
        for r in range(k):
            if r != col and abs(M[r][col]) > 0:
                f = M[r][col] / M[col][col]
                for cc in range(col, k + 1):
                    M[r][cc] -= f * M[col][cc]
    beta = [M[i][k] / M[i][i] for i in range(k)]
    resid = [y[i] - sum(X[i][j] * beta[j] for j in range(k)) for i in range(n)]
    ssr = sum(r * r for r in resid)
    my = sum(y) / n
    sst = sum((v - my) ** 2 for v in y)
    r2 = 1 - ssr / sst if sst > 0 else 0.0
    s2 = ssr / max(1, n - k)
    se = []
    for col in range(k):
        A2 = [A[i][:] + [1.0 if i == col else 0.0] for i in range(k)]
        M2 = [r[:] for r in A2]
        for cc in range(k):
            p = max(range(cc, k), key=lambda r: abs(M2[r][cc]))
            M2[cc], M2[p] = M2[p], M2[cc]
            for r in range(k):
                if r != cc and abs(M2[r][cc]) > 0:
                    f = M2[r][cc] / M2[cc][cc]
                    for c2 in range(cc, k + 1):
                        M2[r][c2] -= f * M2[cc][c2]
        se.append(math.sqrt(s2 * M2[col][k] / M2[col][col]))
    t = [beta[i] / se[i] if se[i] > 0 else 0.0 for i in range(k)]
    return beta, t, r2, n


fit = ols(xs, ys)
print("\n== ln(持仓时长) = a + b1·ln(峰值名义) + b2·峰值名义/100 ==")
if fit:
    beta, t, r2, n = fit
    names = ["a", "b1·lnN", "b2·N/100"]
    for i, nm in enumerate(names):
        print(f"  {nm:<12} β={beta[i]:>+8.4f}  t={t[i]:>+6.2f}"
              + ("  **" if abs(t[i]) >= 2 else ""))
    print(f"  R²={r2:.4f}  n={n}")
    print("\n判读：b1>0 且显著 ⇒ 仓位越大消融越慢（偏斜消融不足，exit_skew 有意义）；"
          "\nb1≈0 ⇒ 时长与仓位无关（计时器主导，exit_skew 无意义）")

# 分路径的时长/仓位
print("\n== 按结束路径分组 ==")
grp = {}
for e in epochs:
    grp.setdefault(e["end_path"], []).append(e)
for k, v in sorted(grp.items(), key=lambda x: -len(x[1])):
    durs = sorted(e["dur"] for e in v)
    peaks = sorted(e["peak_usd"] for e in v)
    print(f"  {k:<24} n={len(v):>4} 时长中位={durs[len(durs)//2]:>7.0f}s "
          f"名义中位=${peaks[len(peaks)//2]:>7.2f}")

out = ROOT / "research_l1" / "out" / "h453_inventory_melt.json"
out.write_text(json.dumps({"n_epochs": len(epochs),
                           "fit": {"beta": fit[0], "t": fit[1], "r2": fit[2], "n": fit[3]}
                           if fit else None,
                           "by_path": {k: {"n": len(v),
                                           "dur_median": sorted(e["dur"] for e in v)[len(v) // 2],
                                           "peak_median": sorted(e["peak_usd"] for e in v)[len(v) // 2]}
                                       for k, v in grp.items()}},
                          ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\n已存 {out}")
