# -*- coding: utf-8 -*-
"""H303 预测器 v2：1 分钟多因子（15s 口径，与 worker mid_hist 同构）。

# 依据（H301）：r15 / vwapd / rsi14 / std5 在 fwd 3~5m 的 IC 显著（t −3~−5），
# 多因子岭回归验证 IC +0.027。本脚本用 15s 中价序列重训（worker 的 mid_hist 口径），
# 保证训练特征与线上计算同构（H301 用的 1m K 线是近似口径）。

# 特征（15s 序列，t 为当前期）：
    r15    15 分钟趋势 = 60 期净移动（bp）
    vwapd  现价 − 120 期均值 偏差（bp，30 分钟）
    rsi14  14 根 1 分钟 bar（每根 = 4 期均值）的 RSI
    std5   最近 5 根 1 分钟 bar 收益的标准差
# 标签：fwd 3 分钟 = 12 期收益（bp）。
# 验证：窗口1（最近 48h，5d 训练/2d 验证）+ 窗口2（2~4 天前，交叉验证符号稳定）。
# 发布：两窗验证 corr 均 ≥ 0.02 且权重符号一致 → lane_registry.meta.ai_model_v2。

# 用法

    python scripts/h303_v2_learner.py --hours 48           # 训练+双窗验证（只读）
    python scripts/h303_v2_learner.py --hours 48 --apply   # 通过则发布
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "research_l1" / "out" / "h303_v2_learner.json"
LANE = "mm_asterdex"
CUR = ["SOLUSDT", "DOGEUSDT", "ETHUSDT", "BNBUSDT"]
FEATS = ["r15", "vwapd", "rsi14", "std5"]


def market_dsn() -> str:
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    base = env["DATABASE_URL"]
    for j in ("+psycopg2", "+psycopg", "+asyncpg"):
        base = base.replace(j, "")
    return base.rsplit("/", 1)[0] + "/alpha_market"


def pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return sxy / (sxx * syy) ** 0.5 if sxx > 0 and syy > 0 else 0.0


def build_features(mids):
    """15s 序列 → 特征/标签（60s 去重叠）。返回 rows。"""
    rows = []
    n = len(mids)
    last = -1e18
    for t in range(130, n - 13):
        if t - last < 4:  # 60s 去重叠（4 期 = 60s）
            continue
        if mids[t] <= 0:
            continue
        r15 = (mids[t] - mids[t - 60]) / mids[t - 60] * 1e4
        mean120 = sum(mids[t - 120:t + 1]) / 121.0
        vwapd = (mids[t] - mean120) / mean120 * 1e4
        # 1 分钟 bar = 4 期均值；14 根
        bars = [sum(mids[t - 4 * (k + 1) + 1:t - 4 * k + 1]) / 4.0
                for k in range(14)]
        gains = sum(max(bars[k] - bars[k - 1], 0.0) for k in range(1, 14))
        losses = sum(max(bars[k - 1] - bars[k], 0.0) for k in range(1, 14))
        rsi14 = 100.0 * gains / (gains + losses) if (gains + losses) > 0 else 50.0
        rets = [(bars[k] - bars[k - 1]) / bars[k - 1] * 1e4 for k in range(9, 14)]
        std5 = math.sqrt(sum(x * x for x in rets) / 5.0) if rets else 0.0
        y = (mids[t + 12] - mids[t]) / mids[t] * 1e4
        last = t
        rows.append([r15, vwapd, rsi14, std5, y])
    return rows


def load_window(hours: float, ago: float):
    import psycopg
    rows = []
    for sym in CUR:
        with psycopg.connect(market_dsn()) as c:
            with c.cursor() as cur:
                cur.execute("""
                    SELECT DISTINCT ON (bucket) bucket, bid_px, ask_px
                    FROM (SELECT (event_ts_ms/1000) AS bucket, bid_px, ask_px
                          FROM asterdex_book_ticker
                          WHERE event_ts_ms >= (extract(epoch from now())*1000 - (%s+%s)*3600*1000)::bigint
                            AND event_ts_ms <  (extract(epoch from now())*1000 - %s*3600*1000)::bigint
                            AND symbol = %s AND bid_px>0 AND ask_px>bid_px) t
                    ORDER BY bucket, bid_px
                """, (hours, ago, ago, sym))
                recs = cur.fetchall()
        d = {int(b): ((float(x) + float(y)) / 2.0) for b, x, y in recs}
        ks = sorted(d)
        # 15s 重采样：取每 15s 桶最后一个 mid
        mids = []
        bucket = None
        for k in ks:
            b = k // 15
            if b != bucket:
                bucket = b
                mids.append(d[k])
            else:
                mids[-1] = d[k]
        rows.extend(build_features(mids))
        print(f"    {sym:10} {len(mids)} 个 15s 点", flush=True)
    return rows


def fit_ridge(rows, lam=1.0):
    import numpy as np
    X = np.array([r[:4] for r in rows])
    Y = np.array([r[4] for r in rows])
    cut = int(len(rows) * 5.0 / 7.0)
    mu, sd = X[:cut].mean(0), X[:cut].std(0) + 1e-9
    Xs = (X - mu) / sd
    beta = np.linalg.solve(Xs[:cut].T @ Xs[:cut] + lam * np.eye(4), Xs[:cut].T @ Y[:cut])
    ic_tr = pearson(list(Xs[:cut] @ beta), list(Y[:cut]))
    ic_va = pearson(list(Xs[cut:] @ beta), list(Y[cut:]))
    # 单因子边际 IC（跨窗符号稳定性判定用）
    marg = []
    for i in range(4):
        xs = [r[i] for r in rows]
        marg.append(round(pearson(xs, list(Y)), 5))
    return {"ic_train": float(round(ic_tr, 5)), "ic_val": float(round(ic_va, 5)),
            "n": len(rows), "weights": [float(round(float(b), 4)) for b in beta],
            "mu": [float(round(float(m), 3)) for m in mu],
            "sd": [float(round(float(s), 3)) for s in sd],
            "marginal_ic": {f: m for f, m in zip(FEATS, marg)}}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=48.0)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    print("=" * 96)
    print("H303  预测器 v2（1 分钟多因子，15s 口径）")
    print("=" * 96)
    print(f"\n  窗口1（最近 {a.hours}h）:")
    rows1 = load_window(a.hours, 0.0)
    r1 = fit_ridge(rows1)
    print(f"    n={r1['n']}  训练 IC {r1['ic_train']}  验证 IC {r1['ic_val']}")
    print(f"    权重 { {f: w for f, w in zip(FEATS, r1['weights'])} }")
    print(f"\n  窗口2（{a.hours}h 窗口前移 48h，交叉验证）:")
    rows2 = load_window(a.hours, 48.0)
    r2 = fit_ridge(rows2)
    print(f"    n={r2['n']}  训练 IC {r2['ic_train']}  验证 IC {r2['ic_val']}")
    print(f"    权重 { {f: w for f, w in zip(FEATS, r2['weights'])} }")

    sign_ok = all((r1["weights"][i] > 0) == (r2["weights"][i] > 0) for i in range(4))
    val_ok = r1["ic_val"] >= 0.02 and r2["ic_val"] >= 0.02
    # 单特征 r15 的跨窗稳定性（多因子权重不稳时的稳健替代）
    r15_w1 = r1["marginal_ic"].get("r15", 0.0)
    r15_w2 = r2["marginal_ic"].get("r15", 0.0)
    r15_stable = bool((r15_w1 < 0) == (r15_w2 < 0)) and abs(r15_w1) >= 0.015 and abs(r15_w2) >= 0.015
    print(f"\n  符号一致: {bool(sign_ok)}  双窗验证 ≥0.02: {bool(val_ok)}")
    print(f"  单特征 r15 边际 IC: 窗1 {r15_w1}  窗2 {r15_w2}  稳定: {r15_stable}")
    result = {"window1": r1, "window2": r2, "features": FEATS,
              "sign_stable": bool(sign_ok), "val_ok": bool(val_ok),
              "r15_stable": r15_stable}
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2,
                              default=lambda o: (o.item() if hasattr(o, "item") else str(o))),
                   encoding="utf-8")
    print(f"  已存 {OUT}")

    if a.apply and (sign_ok and val_ok):
        import psycopg
        import datetime as dt
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
        artifact = {"features": FEATS, "weights": {f: w for f, w in zip(FEATS, r1["weights"])},
                    "mu": {f: m for f, m in zip(FEATS, r1["mu"])},
                    "sd": {f: s for f, s in zip(FEATS, r1["sd"])},
                    "ic_val_w1": r1["ic_val"], "ic_val_w2": r2["ic_val"],
                    "horizon": "3min", "as_of": dt.datetime.now(dt.timezone.utc).isoformat()}
        with psycopg.connect(url) as c:
            with c.cursor() as cur:
                cur.execute(
                    "UPDATE lane_registry SET meta_json = jsonb_set(meta_json,"
                    " '{ai_model_v2}', %s::jsonb, true), updated_at=now() WHERE lane_id=%s",
                    (json.dumps(artifact, ensure_ascii=False), LANE))
            c.commit()
        print(f"  ✓ 已发布 lane_registry.meta.ai_model_v2")
        return 0
    if a.apply:
        print("  ✗ 未通过双窗验证（多因子权重符号不稳），不发布")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
