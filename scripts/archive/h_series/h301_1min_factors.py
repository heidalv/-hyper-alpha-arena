# -*- coding: utf-8 -*-
"""H301 1 分钟因子研究：单一因子 IC + 多因子 walk-forward。

# 用户指令：继续研究短期策略——1 分钟因子行不行？多因子？新闻刺激？
# 本脚本回答前两问。数据：asterdex 1m K 线（crypto_klines，7 天 37 万根）。

# 因子库（每根 1m bar 现算）：
    r1      最近 1 分钟收益（bp）         基线（对应 H280 的 60s 信号）
    r5      最近 5 分钟收益（bp）
    r15     最近 15 分钟收益（bp）
    rng1    1 分钟振幅 (high-low)/close×1e4
    volr    量比：本分钟成交量 / 前 20 分钟均值
    vwapd   现价 − 30 分钟 VWAP 偏差（bp）
    rsi14   1m K 线 RSI(14)
    std5    最近 5 根 1m 收益的标准差（波动）
    up5     最近 5 分钟上涨占比（0~1，短动量/情绪）
# 标签：未来 1m / 3m / 5m 收益（bp）。
# 口径：逐币 IC 平均 + 合并 IC；walk-forward 多因子（岭回归，5d 训练 / 2d 验证）。

# 用法

    python scripts/h301_1min_factors.py --days 7
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "research_l1" / "out" / "h301_1min_factors.json"
SYMS = ["SOL", "DOGE", "ETH", "BNB", "BTC"]  # crypto_klines.symbol 是裸符号
FEATS = ["r1", "r5", "r15", "rng1", "volr", "vwapd", "rsi14", "std5", "up5"]


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
        return 0.0, 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    if sxx <= 0 or syy <= 0:
        return 0.0, 0.0
    r = sxy / (sxx * syy) ** 0.5
    t = r * ((n - 2) / (1 - r * r)) ** 0.5 if abs(r) < 1 else float("inf")
    return r, t


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=7.0)
    a = ap.parse_args()

    import psycopg
    import numpy as np

    rows = []
    for sym in SYMS:
        with psycopg.connect(market_dsn()) as c:
            with c.cursor() as cur:
                cur.execute("""
                    SELECT timestamp, open_price, high_price, low_price, close_price, volume
                    FROM crypto_klines
                    WHERE exchange='asterdex' AND symbol=%s AND period='1m'
                      AND timestamp >= extract(epoch from now()) - %s*86400
                    ORDER BY timestamp
                """, (sym, a.days))
                bars = [(int(r[0]), float(r[1]), float(r[2]), float(r[3]),
                         float(r[4]), float(r[5])) for r in cur.fetchall()]
        print(f"  {sym:10} {len(bars)} 根 1m K 线", flush=True)
        closes = [b[4] for b in bars]
        vols = [b[5] for b in bars]
        n = len(bars)
        for i in range(35, n - 5):
            c0 = closes[i]
            if c0 <= 0:
                continue
            r1 = (c0 - closes[i - 1]) / closes[i - 1] * 1e4
            r5 = (c0 - closes[i - 5]) / closes[i - 5] * 1e4
            r15 = (c0 - closes[i - 15]) / closes[i - 15] * 1e4
            rng1 = (bars[i][2] - bars[i][3]) / c0 * 1e4
            v20 = sum(vols[i - 20:i]) / 20.0
            volr = vols[i] / v20 if v20 > 0 else 1.0
            vwap_num = sum(closes[j] * vols[j] for j in range(i - 30, i + 1))
            vwap_den = sum(vols[i - 30:i + 1])
            vwap = vwap_num / vwap_den if vwap_den > 0 else c0
            vwapd = (c0 - vwap) / vwap * 1e4
            seg = [closes[i - 14 + k] for k in range(15)]
            gains = sum(max(seg[k] - seg[k - 1], 0.0) for k in range(1, 15))
            losses = sum(max(seg[k - 1] - seg[k], 0.0) for k in range(1, 15))
            rsi = 100.0 * gains / (gains + losses) if (gains + losses) > 0 else 50.0
            r5s = [(closes[i - k] - closes[i - k - 1]) / closes[i - k - 1] * 1e4
                   for k in range(5)]
            std5 = float(np.std(r5s)) if r5s else 0.0
            up5 = sum(1 for x in r5s if x > 0) / 5.0
            # 标签：未来 1m/3m/5m
            y1 = (closes[i + 1] - c0) / c0 * 1e4
            y3 = (closes[i + 3] - c0) / c0 * 1e4
            y5 = (closes[i + 5] - c0) / c0 * 1e4
            rows.append({"sym": sym, "ts": bars[i][0],
                         "r1": r1, "r5": r5, "r15": r15, "rng1": rng1,
                         "volr": volr, "vwapd": vwapd, "rsi14": rsi,
                         "std5": std5, "up5": up5,
                         "y1": y1, "y3": y3, "y5": y5})

    print(f"\n  样本数: {len(rows)}")
    if not rows:
        print("  ✗ 无样本（检查 crypto_klines 的 symbol 是裸符号、period='1m'）")
        return 1
    print(f"\n  单因子 IC（fwd 1m / 3m / 5m，合并五币）:")
    out = {"n": len(rows), "ic": {}}
    for f in FEATS:
        for yk in ("y1", "y3", "y5"):
            xs = [r[f] for r in rows]
            ys = [r[yk] for r in rows]
            r_, t_ = pearson(xs, ys)
            out["ic"][f"{f}|{yk}"] = {"ic": round(r_, 5), "t": round(t_, 2)}
            print(f"    {f:8s} {yk}: IC {r_:+.5f}  t {t_:+.1f}")

    # walk-forward 多因子：前 5 天训练 → 后 2 天验证（标签 y1）
    rows.sort(key=lambda r: r["ts"])
    X = np.array([[r[f] for f in FEATS] for r in rows])
    Y = np.array([r["y1"] for r in rows])
    cut = int(len(rows) * 5.0 / 7.0)
    mu, sd = X[:cut].mean(0), X[:cut].std(0) + 1e-9
    Xtr = (X[:cut] - mu) / sd
    Xva = (X[cut:] - mu) / sd
    for lam in (0.1, 1.0, 10.0):
        beta = np.linalg.solve(Xtr.T @ Xtr + lam * np.eye(len(FEATS)), Xtr.T @ Y[:cut])
        ic_tr = pearson(list(Xtr @ beta), list(Y[:cut]))[0]
        ic_va = pearson(list(Xva @ beta), list(Y[cut:]))[0]
        print(f"\n  岭回归 λ={lam}: 训练 IC {ic_tr:+.5f}  验证 IC {ic_va:+.5f}"
              f"  (基线 r1 验证 {pearson(Xva[:,0], list(Y[cut:]))[0]:+.5f})")
        out[f"ridge_l{lam}"] = {"ic_train": round(ic_tr, 5), "ic_val": round(ic_va, 5),
                                "weights": {f: round(float(w), 4)
                                            for f, w in zip(FEATS, beta)}}
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  已存 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
