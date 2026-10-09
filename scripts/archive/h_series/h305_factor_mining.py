# -*- coding: utf-8 -*-
"""H305 多时域因子挖掘：1m + 5m 因子库 → IC 排名 → 前向逐步多因子选择。

# 用户指令：1 分钟因子挖掘 + 5 分钟因子 + 时间配置 + 多因子策略。
# 数据：asterdex 1m / 5m K 线（crypto_klines，7 天，5 币）。

# 因子库（全部现算）：
#  1m 系：r{l} lags{1,3,5,10,15,30,60}、RSI{7,14,21}、VWAPdev{15,30,60,120}、
#         std{r5,10,20}、upfrac{5,10,20}、量比 z{5,20,60}、range 比、高低位、
#         连涨连跌 streak、小时 sin/cos
#  5m 系：R{l} lags{1,3,6,12}（=5/15/30/60min）、RSI14@5m、VWAPdev@1h/4h、
#         std@3/6bar、量比、range
# 标签：fwd 1m/3m/5m/15m（1m bar 计）。输出每因子×时域 IC/t 排名。
# 多因子：前向逐步（ridge，每次加"使验证 IC 增益最大"的因子，增益 <0.005 停），
#   窗口1（5d 训练/2d 验证）选因子；窗口2（前 7 天）验证所选因子符号稳定性。

# 用法

    python scripts/h305_factor_mining.py --days 7
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "research_l1" / "out" / "h305_factor_mining.json"
SYMS = ["SOL", "DOGE", "ETH", "BNB", "BTC"]


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


def rsi(vals, p):
    gains = losses = 0.0
    for k in range(1, len(vals)):
        d = vals[k] - vals[k - 1]
        if d > 0:
            gains += d
        else:
            losses -= d
    return 100.0 * gains / (gains + losses) if (gains + losses) > 0 else 50.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=7.0)
    a = ap.parse_args()

    import psycopg

    bars1, bars5 = {}, {}
    for sym in SYMS:
        for period, dst in (("1m", bars1), ("5m", bars5)):
            with psycopg.connect(market_dsn()) as c:
                with c.cursor() as cur:
                    cur.execute("""
                        SELECT timestamp, open_price, high_price, low_price,
                               close_price, volume FROM crypto_klines
                        WHERE exchange='asterdex' AND symbol=%s AND period=%s
                          AND timestamp >= extract(epoch from now()) - %s*86400
                        ORDER BY timestamp
                    """, (sym, period, a.days))
                    dst[sym] = [(int(r[0]), float(r[1]), float(r[2]), float(r[3]),
                                 float(r[4]), float(r[5])) for r in cur.fetchall()]
    print(f"  1m: { {s: len(v) for s, v in bars1.items()} }")
    print(f"  5m: { {s: len(v) for s, v in bars5.items()} }")

    def align5(sym, ts):
        """最近的 5m bar 索引（ts 为 1m bar 时间戳）"""
        b5 = bars5[sym]
        lo, hi = 0, len(b5) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if b5[mid][0] <= ts:
                lo = mid
            else:
                hi = mid - 1
        return lo

    # 因子构建（逐币逐 1m bar）
    import numpy as np

    feats_all = []  # list of dict per sample
    for sym in SYMS:
        B = bars1[sym]
        C = [b[4] for b in B]
        V = [b[5] for b in B]
        n = len(B)
        j5 = 0
        for i in range(130, n - 16):
            c0 = C[i]
            if c0 <= 0:
                continue
            f = {}
            for lag in (1, 3, 5, 10, 15, 30, 60):
                if C[i - lag] > 0:
                    f[f"r1m_{lag}"] = (c0 - C[i - lag]) / C[i - lag] * 1e4
            for p, name in ((7, "rsi7"), (14, "rsi14"), (21, "rsi21")):
                f[name] = rsi(C[i - p:i + 1], p)
            for w, name in ((15, "vwapd15"), (30, "vwapd30"), (60, "vwapd60"), (120, "vwapd120")):
                seg = C[i - w:i + 1]
                m = sum(seg) / len(seg)
                f[name] = (c0 - m) / m * 1e4
            for w, name in ((5, "std5"), (10, "std10"), (20, "std20")):
                r = [(C[i - k] - C[i - k - 1]) / C[i - k - 1] * 1e4 for k in range(w)]
                f[name] = float(np.std(r)) if r else 0.0
            for w, name in ((5, "up5"), (10, "up10"), (20, "up20")):
                r = [(C[i - k] - C[i - k - 1]) for k in range(w)]
                f[name] = sum(1 for x in r if x > 0) / w
            for w, name in ((5, "vz5"), (20, "vz20"), (60, "vz60")):
                m = sum(V[i - w:i]) / w
                f[name] = V[i] / m if m > 0 else 1.0
            f["range1"] = (B[i][2] - B[i][3]) / c0 * 1e4
            f["hlpos"] = (c0 - B[i][3]) / (B[i][2] - B[i][3]) if B[i][2] > B[i][3] else 0.5
            streak = 0
            k = i
            while k > 0 and (C[k] - C[k - 1]) >= 0 == (C[i] - C[i - 1] >= 0):
                streak += 1
                k -= 1
            f["streak"] = float(streak)
            # [h305b] 5m K 线表存在 42% 缺口 + 时间戳为开盘时刻（最后一根含当前价），
            # 用它对齐 1m 会产生不规则回看 → 出现 +0.15 的伪动量。
            # 改用 1m 序列直接算 5/15/30/60 分钟因子（与 r1m_* 同口径、无缺口问题）。
            for lag, name in ((5, "R5m_1"), (15, "R15m_1"), (30, "R30m_1"), (60, "R60m_1")):
                if C[i - lag] > 0:
                    f[name] = (c0 - C[i - lag]) / C[i - lag] * 1e4
            # 标签
            f["y1"] = (C[i + 1] - c0) / c0 * 1e4
            f["y3"] = (C[i + 3] - c0) / c0 * 1e4
            f["y5"] = (C[i + 5] - c0) / c0 * 1e4
            f["y15"] = (C[i + 15] - c0) / c0 * 1e4
            f["sym"] = sym
            f["ts"] = B[i][0]
            feats_all.append(f)

    print(f"\n  样本数: {len(feats_all)}")
    names = [k for k in feats_all[0] if k not in ("y1", "y3", "y5", "y15", "sym", "ts")]
    print(f"  因子数: {len(names)}")

    # IC 排名（fwd 5m 主表）
    ys = {"y1": [r["y1"] for r in feats_all], "y3": [r["y3"] for r in feats_all],
          "y5": [r["y5"] for r in feats_all], "y15": [r["y15"] for r in feats_all]}
    ic_rows = []
    for name in names:
        xs = [r[name] for r in feats_all]
        row = {"factor": name}
        for yk in ("y1", "y3", "y5", "y15"):
            ic = pearson(xs, ys[yk])
            row[yk] = round(ic, 4)
        ic_rows.append(row)
    ic_rows.sort(key=lambda r: -abs(r["y5"]))
    print(f"\n  因子 IC 排名（按 |fwd5m|，前 20）:")
    print(f"  {'因子':>14} {'y1':>8} {'y3':>8} {'y5':>8} {'y15':>8}")
    for r in ic_rows[:20]:
        print(f"  {r['factor']:>14} {r['y1']:>+8.4f} {r['y3']:>+8.4f} {r['y5']:>+8.4f} {r['y15']:>+8.4f}")

    # 前向逐步多因子（fwd5m 标签，window 内部 5/7 切分）
    feats_all.sort(key=lambda r: r["ts"])
    cut = int(len(feats_all) * 5.0 / 7.0)
    X_all = np.array([[r[nm] for nm in names] for r in feats_all])
    Y = np.array([r["y5"] for r in feats_all])
    mu, sd = X_all[:cut].mean(0), X_all[:cut].std(0) + 1e-9
    Xs = (X_all - mu) / sd
    Xtr, Xva = Xs[:cut], Xs[cut:]
    Ytr, Yva = Y[:cut], Y[cut:]

    def ridge_ic(idx, lam=5.0):
        A = Xtr[:, idx].T @ Xtr[:, idx] + lam * np.eye(len(idx))
        b = Xtr[:, idx].T @ Ytr
        beta = np.linalg.solve(A, b)
        tr = pearson(list(Xtr[:, idx] @ beta), list(Ytr))
        va = pearson(list(Xva[:, idx] @ beta), list(Yva))
        return tr, va

    selected = []
    best_va = -1.0
    print(f"\n  前向逐步（fwd5m，λ=5）:")
    while True:
        best_add, best_new_va = None, best_va
        for j in range(len(names)):
            if j in selected:
                continue
            _, va = ridge_ic(selected + [j])
            if va > best_new_va:
                best_new_va, best_add = va, j
        if best_add is None or best_new_va - best_va < 0.005:
            break
        selected.append(best_add)
        best_va = best_new_va
        tr, va = ridge_ic(selected)
        print(f"    + {names[best_add]:>14}  训练 IC {tr:+.4f}  验证 IC {va:+.4f}")

    tr, va = ridge_ic(selected)
    print(f"\n  选定 {len(selected)} 因子: { [names[j] for j in selected] }")
    print(f"  最终: 训练 IC {tr:+.4f}  验证 IC {va:+.4f}")

    OUT.write_text(json.dumps({"n": len(feats_all), "factors": len(names),
                               "ic": ic_rows, "selected": [names[j] for j in selected],
                               "ic_train": round(tr, 5), "ic_val": round(va, 5)},
                              ensure_ascii=False, indent=2,
                              default=lambda o: (o.item() if hasattr(o, "item") else str(o))),
                   encoding="utf-8")
    print(f"\n  已存 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
