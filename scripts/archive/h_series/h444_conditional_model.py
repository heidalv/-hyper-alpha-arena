# -*- coding: utf-8 -*-
"""H444 条件期望模型：E[Δmid(h) | state] 的显式拟合 + OOS 双窗 + 最优持有期 h*。

模型（线性化统一流定律）：
    y_h = Δmid(h)/mid × 1e4  (bp)
    y_h = β0 + β1·OFI + β2·r60 + β3·r300 + β4·σ_norm + β5·spread_bp
              + β6·(OFI × sign(r60)) + β7·|r300| + ε
特征口径（全部只用 t 时刻及之前的信息，无未来函数）：
    OFI     : 15s 桶归一化失衡（market_trades_aggregated）
    r60/r300: 60s/300s 中价收益（bp，分钟采样点）
    σ_norm  : 20 期已实现波动 / 滚动中位基准 − 1
    spread  : 全价差 bp（book ticker）
    sign(r60): 趋势方向；OFI×sign(r60) = 顺势流交互（统一流定律的数学形式）
检验：
    1) 全样本 OLS：系数、t 值、R²；
    2) OOS 双窗：前半拟合 → 后半预测（反之亦然），报 OOS R² 与系数符号一致性；
    3) h ∈ {f30,f60,f120,f300} 分别拟合 ⇒ 得到 E[Δmid(h)|state] 随 h 的曲线；
    4) h* = argmax_h E[Δmid(h) | 代表性状态]（分 OFI 桶 × 趋势桶）。
用法: python scripts/h444_conditional_model.py [--hours 168] [--sample-sec 60]
"""
from __future__ import annotations

import argparse
import bisect
import datetime as dt
import json
import math
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "research_l1" / "out" / "h444_conditional_model.json"
HORIZONS = ((6, "f30"), (12, "f60"), (24, "f120"), (60, "f300"))


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


def _ols(X, y):
    """最小二乘（正规方程 + 高斯消元），返回 (beta, se, t, r2, n)。"""
    n, k = len(y), len(X[0])
    # 正规方程 (X'X)b = X'y
    A = [[sum(X[i][a] * X[i][b] for i in range(n)) for b in range(k)] for a in range(k)]
    b = [sum(X[i][a] * y[i] for i in range(n)) for a in range(k)]
    # 消元
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for col in range(k):
        piv = max(range(col, k), key=lambda r: abs(M[r][col]))
        if abs(M[piv][col]) < 1e-12:
            return None
        M[col], M[piv] = M[piv], M[col]
        for r in range(k):
            if r != col and abs(M[r][col]) > 0:
                f = M[r][col] / M[col][col]
                for cc in range(col, k + 1):
                    M[r][cc] -= f * M[col][cc]
    beta = [M[i][k] / M[i][i] for i in range(k)]
    # 残差与统计量
    resid = [y[i] - sum(X[i][j] * beta[j] for j in range(k)) for i in range(n)]
    ss_res = sum(r * r for r in resid)
    my = sum(y) / n
    ss_tot = sum((v - my) ** 2 for v in y)
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
    dof = max(1, n - k)
    s2 = ss_res / dof
    # (X'X)^-1 对角
    inv_diag = []
    for col in range(k):
        # 解 A z = e_col（复用消元后的 M 不方便，重新解一次）
        A2 = [row[:] + [1.0 if i == col else 0.0] for i, row in enumerate(A)]
        Mk = [r[:] for r in A2]
        ok = True
        for cc in range(k):
            p = max(range(cc, k), key=lambda r: abs(Mk[r][cc]))
            if abs(Mk[p][cc]) < 1e-12:
                ok = False
                break
            Mk[cc], Mk[p] = Mk[p], Mk[cc]
            for r in range(k):
                if r != cc and abs(Mk[r][cc]) > 0:
                    f = Mk[r][cc] / Mk[cc][cc]
                    for c2 in range(cc, k + 1):
                        Mk[r][c2] -= f * Mk[cc][c2]
        inv_diag.append(Mk[col][k] / Mk[col][col] if ok else float("inf"))
    se = [math.sqrt(s2 * d) if d != float("inf") and s2 * d > 0 else float("inf")
          for d in inv_diag]
    t = [beta[i] / se[i] if se[i] not in (0, float("inf")) else 0.0 for i in range(k)]
    return beta, se, t, r2, n


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
    print(f"宇宙（{len(syms)} 币）：{syms}，窗口 {a.hours}h，采样 {a.sample_sec}s", flush=True)

    t1 = int(dt.datetime.now(dt.timezone.utc).timestamp() * 1000)
    t0 = t1 - int(a.hours * 3600 * 1000)
    step_ms = a.sample_sec * 1000

    rows = []   # (ts_s, coin, mid, spread_bp)
    with psycopg.connect(read_env_dsn().replace("/alpha_arena", "/alpha_market"),
                         autocommit=True) as cm:
        with cm.cursor() as cur:
            for sym in syms:
                # 每 sample_sec 取一个 1.5s 窗口内的行（SQL 侧过滤），Python 侧取该窗口最后一行
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
                pts = [got[k] for k in sorted(got)]
                rows.extend((t_s, sym, mid, sp) for t_s, mid, sp in pts)
                print(f"  {sym}: {len(pts)} 采样点", flush=True)
    rows.sort()
    print(f"总采样 {len(rows)} 点", flush=True)

    # OFI 15s 桶
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
                print(f"  {sym} OFI 桶 {sum(1 for k in ofi if k[0]==sym)}", flush=True)

    # 组装特征（每币独立序列）
    by_coin = {}
    for t_s, sym, mid, sp in rows:
        by_coin.setdefault(sym, []).append((t_s, mid, sp))

    feats = []   # (sym, t, ofi, r60, r300, sigma, spread, y_by_h{})
    for sym, seq in by_coin.items():
        n = len(seq)
        if n < 100:
            continue
        mids = [x[1] for x in seq]
        # 已实现波动基准（20 期 ≈ 20 × sample_sec）
        vols = []
        for i in range(20, n):
            seg = mids[i - 20:i + 1]
            if seg[0] > 0:
                vols.append(abs(seg[-1] - seg[0]) / seg[0] * 1e4)
        base = sorted(vols)[len(vols) // 2] if vols else 0.0
        for i in range(60, n - 60):
            t_s, mid, sp = seq[i]
            if mid <= 0 or mids[i - 1] <= 0 or mids[i - 12] <= 0:
                continue
            r60 = (mid - mids[i - 12]) / mids[i - 12] * 1e4
            r300 = ((mid - mids[i - 60]) / mids[i - 60] * 1e4) if i >= 60 else 0.0
            vol = abs(mid - mids[i - 20]) / mids[i - 20] * 1e4
            sigma = (vol / base - 1.0) if base > 0 else 0.0
            f = ofi.get((sym, (t_s // 15) * 15), 0.0)
            ys = {}
            ok = True
            for steps, hname in HORIZONS:
                j = i + steps
                if j >= n or mids[j] <= 0:
                    ok = False
                    break
                ys[hname] = (mids[j] - mid) / mid * 1e4
            if not ok:
                continue
            feats.append((sym, t_s, f, r60, r300, sigma, sp, ys))
    print(f"样本 {len(feats)}", flush=True)

    if len(feats) < 500:
        print("样本不足")
        return 1

    half_s = feats[len(feats) // 2][1]

    def design(sub):
        X, ys = [], {h: [] for _, h in HORIZONS}
        for sym, t_s, f, r60, r300, sg, sp, y in sub:
            sgn = 1.0 if r60 >= 0 else -1.0
            X.append([1.0, f, r60, r300, sg, sp, f * sgn, abs(r300)])
            for _, h in HORIZONS:
                ys[h].append(y[h])
        return X, ys

    names = ["β0", "OFI", "r60", "r300", "σ_norm", "spread", "OFI×sign(r60)", "|r300|"]
    res = {"generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
           "hours": a.hours, "symbols": syms, "n": len(feats), "models": {}}

    print(f"\n===== 全样本 OLS（n={len(feats)}）=====")
    for _, hname in HORIZONS:
        X, ys = design(feats)
        fit = _ols(X, ys[hname])
        if not fit:
            continue
        beta, se, t, r2, n = fit
        print(f"\n-- {hname} --  R²={r2:.4f}")
        for i, nm in enumerate(names):
            star = "**" if abs(t[i]) >= 2 else ("*" if abs(t[i]) >= 1.5 else "  ")
            print(f"   {nm:<16} β={beta[i]:>+9.4f}  t={t[i]:>+6.2f} {star}")
        res["models"][hname] = {"beta": dict(zip(names, [round(x, 6) for x in beta])),
                                "t": dict(zip(names, [round(x, 2) for x in t])),
                                "r2": round(r2, 5), "n": n}

    # OOS 双窗（f60）
    print("\n===== OOS 双窗（f60：前半拟合→后半预测 / 反之）=====")
    h1 = [f for f in feats if f[1] < half_s]
    h2 = [f for f in feats if f[1] >= half_s]
    oos = {}
    for label, tr, te in (("h0→h1", h1, h2), ("h1→h0", h2, h1)):
        Xtr, ytr = design(tr)
        Xte, yte = design(te)
        fit = _ols(Xtr, ytr["f60"])
        if not fit:
            continue
        beta = fit[0]
        pred = [sum(Xte[i][j] * beta[j] for j in range(len(beta))) for i in range(len(Xte))]
        my = sum(yte["f60"]) / len(yte["f60"])
        ss_res = sum((yte["f60"][i] - pred[i]) ** 2 for i in range(len(pred)))
        ss_tot = sum((v - my) ** 2 for v in yte["f60"])
        r2o = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
        oos[label] = {"r2_oos": round(r2o, 5), "n_train": len(tr), "n_test": len(te),
                      "beta": dict(zip(names, [round(x, 6) for x in beta]))}
        print(f"  {label}: R²_oos={r2o:+.5f}  n_train={len(tr)} n_test={len(te)}")
    res["oos_f60"] = oos

    # 状态分桶的最优持有期 h*（分 OFI 桶 × 趋势桶）
    print("\n===== 状态桶 × 时域 期望（bp，用于求 h*）=====")
    buckets = {}
    for sym, t_s, f, r60, r300, sg, sp, y in feats:
        fo = "with" if (f >= 0.3 and r60 >= 0) or (f <= -0.3 and r60 < 0) else \
             ("against" if (f >= 0.3 and r60 < 0) or (f <= -0.3 and r60 >= 0) else "neutral")
        buckets.setdefault(fo, {h: [] for _, h in HORIZONS})
        for _, h in HORIZONS:
            buckets[fo][h].append(y[h])
    hstar = {}
    for fo, d in buckets.items():
        line = []
        means = {}
        for _, h in HORIZONS:
            xs = d[h]
            m = sum(xs) / len(xs) if xs else 0.0
            means[h] = round(m, 3)
            line.append(f"{h}={m:+.2f}({len(xs)})")
        best = max(means, key=lambda k: means[k])
        hstar[fo] = {"means": means, "h_star": best}
        print(f"  {fo:<8} " + "  ".join(line) + f"   ⇒ h*={best}")
    res["h_star"] = hstar

    # ── [h444b] **方向调整后的可交易边际**（决定性口径）─────────────────────
    # 线上方向规则：side_mode=model ⇒ 逆 60s 趋势（d = −sign(r60)，h300 工件 weight<0）；
    # 可交易边际 = E[d × Δmid(h) | state]（原始 Δmid 会把市场漂移混进来 ✗）。
    # 同时按线上 ofi_confirm 的"顺势确认"口径分桶：fo = OFI × d。
    print("\n===== 方向调整边际 E[d×Δmid(h)]（d = 逆 r60）=====")
    dbuckets = {}
    drift_only = {}
    for sym, t_s, f, r60, r300, sg, sp, y in feats:
        if abs(r60) < 1e-9:
            continue
        d = -1.0 if r60 > 0 else 1.0
        fo = f * d
        key = "confirm(顺势)" if fo >= 0.15 else ("against(逆流)" if fo <= -0.15 else "neutral")
        dbuckets.setdefault(key, {h: [] for _, h in HORIZONS})
        drift_only.setdefault(key, {h: [] for _, h in HORIZONS})
        for _, h in HORIZONS:
            dbuckets[key][h].append(d * y[h])
            drift_only[key][h].append(y[h])
    dres = {}
    for key, dd in dbuckets.items():
        line = []
        means = {}
        for _, h in HORIZONS:
            xs = dd[h]
            m = sum(xs) / len(xs) if xs else 0.0
            means[h] = round(m, 3)
            # t 值
            if len(xs) > 2:
                mu = m
                var = sum((x - mu) ** 2 for x in xs) / (len(xs) - 1)
                t = mu / math.sqrt(var / len(xs)) if var > 0 else 0.0
            else:
                t = 0.0
            line.append(f"{h}={m:+.2f}(t={t:+.1f},n={len(xs)})")
        best = max(means, key=lambda k: means[k])
        dres[key] = {"means": means, "h_star": best}
        print(f"  {key:<14} " + "\n                  ".join(line) + f"\n                  ⇒ h*={best}")
    res["direction_adjusted"] = dres

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已存 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
