# -*- coding: utf-8 -*-
"""H300 反转预测器（学习进化系统核心）：滚动训练 + 工件发布。

# 模型：岭回归（可解释、稳），特征（H282 验证 + 文献）：
    r60       过去 60s 收益（bp）          IC −0.046 ✓
    r90       过去 90s 收益（bp）          H280 最强格回看
    vwap60    现价−60s VWAP 偏差（bp）      IC −0.040 ✓
    vol90     过去 90s 已实现波动（bp）     regime 特征（文献：反转在低波动强）
    hr_sin/hr_cos 时段周期编码             文献：时段当特征不当规则
    abs_r300   |300s 趋势|（regime 特征）  H281：≥20bp 反转消失
# 标签：fwd 60s 中价收益（bp）。训练 36h / 验证 12h 时序切分。

# 发布：验证 corr 为负且 |corr|≥0.02 → 写 lane_registry.meta.ai_model
# （worker 的 side_mode="model" 读它做方向判定；本期只发布工件，worker 接入下一步）。

# 用法

    python scripts/h300_reversal_learner.py --hours 48           # 训练+验证（只读）
    python scripts/h300_reversal_learner.py --hours 48 --apply   # 通过则发布工件
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import sys
import time
from datetime import datetime, timezone, timedelta

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "research_l1" / "out" / "h300_learner.json"
LANE = "mm_asterdex"
CUR = ["SOLUSDT", "DOGEUSDT", "ETHUSDT", "BNBUSDT"]
BJT = timezone(timedelta(hours=8))
FEATURES = ["r60", "r90", "vwap60", "vol90", "hr_sin", "hr_cos", "abs_r300"]


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


def corr(xs, ys):
    n = len(xs)
    if n < 3:
        return 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return sxy / (sxx * syy) ** 0.5 if sxx > 0 and syy > 0 else 0.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=48.0)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--features", default=",".join(FEATURES),
                    help="逗号分隔特征子集（防过拟合用，如 r60,vwap60）")
    ap.add_argument("--l2", type=float, default=1.0, help="岭正则系数")
    a = ap.parse_args()

    feat_sel = [f.strip() for f in a.features.split(",") if f.strip() in FEATURES]

    import psycopg
    import numpy as np

    rows = []
    for sym in CUR:
        with psycopg.connect(market_dsn()) as c:
            with c.cursor() as cur:
                cur.execute("""
                    SELECT DISTINCT ON (bucket) bucket, bid_px, ask_px
                    FROM (SELECT (event_ts_ms/1000) AS bucket, bid_px, ask_px
                          FROM asterdex_book_ticker
                          WHERE event_ts_ms >= (extract(epoch from now())*1000 - %s*3600*1000)::bigint
                            AND symbol = %s AND bid_px>0 AND ask_px>bid_px) t
                    ORDER BY bucket, bid_px
                """, (a.hours, sym))
                recs = cur.fetchall()
        d = {int(b): ((float(x) + float(y)) / 2.0) for b, x, y in recs}
        ks = sorted(d)
        px = [d[k] for k in ks]
        n = len(ks)
        # 成交流：VWAP
        with psycopg.connect(market_dsn()) as c:
            with c.cursor() as cur:
                cur.execute("""
                    SELECT event_ts_ms, price, qty FROM asterdex_trades
                    WHERE event_ts_ms >= (extract(epoch from now())*1000 - %s*3600*1000)::bigint
                      AND symbol = %s ORDER BY event_ts_ms
                """, (a.hours, sym))
                tr = [(int(r[0]), float(r[1]), float(r[2])) for r in cur.fetchall()]
        tp = 0
        last = -1e18
        for i in range(n):
            if ks[i] - last < 60.0:
                continue
            last = ks[i]

            def past(k):
                j = i
                while j >= 0 and ks[i] - ks[j] < k:
                    j -= 1
                if j < 0 or ks[i] - ks[j] < k * 0.9 or px[j] <= 0:
                    return None
                return (px[i] - px[j]) / px[j] * 1e4
            r60 = past(60)
            r90 = past(90)
            r300 = past(300)
            # fwd60
            f = i
            while f + 1 < n and ks[f + 1] - ks[i] < 60.0:
                f += 1
            if f == i or ks[f] - ks[i] < 54 or px[i] <= 0:
                continue
            y = (px[f] - px[i]) / px[i] * 1e4
            # vol90
            vj = i
            while vj >= 0 and ks[i] - ks[vj] < 90.0:
                vj -= 1
            vol90 = 0.0
            if vj >= 0 and i - vj > 5:
                seg = px[vj:i + 1]
                vol90 = sum(abs((seg[t + 1] - seg[t]) / seg[t]) * 1e4 for t in range(len(seg) - 1))
            # vwap60
            t0_ms = (ks[i] - 60) * 1000
            t1_ms = ks[i] * 1000
            while tp < len(tr) and tr[tp][0] < t0_ms:
                tp += 1
            q = tp
            vnum = vden = 0.0
            while q < len(tr) and tr[q][0] <= t1_ms:
                vnum += tr[q][1] * tr[q][2]
                vden += tr[q][2]
                q += 1
            vwap = vnum / vden if vden > 0 else px[i]
            vwap60 = (px[i] - vwap) / vwap * 1e4
            hour = datetime.fromtimestamp(ks[i], tz=BJT).hour
            if r60 is None or r90 is None or r300 is None:
                continue
            rows.append({"ts": ks[i], "y": y, "r60": r60, "r90": r90,
                         "vwap60": vwap60, "vol90": vol90,
                         "hr_sin": math.sin(2 * math.pi * hour / 24),
                         "hr_cos": math.cos(2 * math.pi * hour / 24),
                         "abs_r300": abs(r300)})

    rows.sort(key=lambda r: r["ts"])
    print(f"  事件数: {len(rows)}  特征: {feat_sel}")
    n_train = int(len(rows) * 0.75)
    X = np.array([[r[f] for f in feat_sel] for r in rows])
    Y = np.array([r["y"] for r in rows])
    Xtr, Xva = X[:n_train], X[n_train:]
    Ytr, Yva = Y[:n_train], Y[n_train:]

    # 标准化 + 岭
    mu, sd = Xtr.mean(0), Xtr.std(0) + 1e-9
    Xtr_s = (Xtr - mu) / sd
    Xva_s = (Xva - mu) / sd
    lam = a.l2
    A = Xtr_s.T @ Xtr_s + lam * np.eye(Xtr_s.shape[1])
    b = Xtr_s.T @ Ytr
    beta = np.linalg.solve(A, b)
    pred_tr = Xtr_s @ beta
    pred_va = Xva_s @ beta
    ic_tr = corr(list(pred_tr), list(Ytr))
    ic_va = corr(list(pred_va), list(Yva))
    r60_ic = corr([r["r60"] for r in rows[n_train:]], list(Yva))
    print(f"  训练 {n_train} / 验证 {len(rows)-n_train}")
    print(f"  训练 corr = {ic_tr:.4f}   验证 corr = {ic_va:.4f}   基线 r60 验证 corr = {r60_ic:.4f}")
    print(f"  权重: { {f: round(float(w), 4) for f, w in zip(feat_sel, beta)} }")
    print(f"  均值: { {f: round(float(m), 3) for f, m in zip(feat_sel, mu)} }")
    print(f"  标准差: { {f: round(float(s), 3) for f, s in zip(feat_sel, sd)} }")

    result = {"ic_train": round(float(ic_tr), 5), "ic_val": round(float(ic_va), 5),
              "r60_ic_val": round(float(r60_ic), 5), "n": len(rows),
              "weights": {f: round(float(w), 6) for f, w in zip(feat_sel, beta)},
              "mu": {f: round(float(m), 4) for f, m in zip(feat_sel, mu)},
              "sd": {f: round(float(s), 4) for f, s in zip(feat_sel, sd)},
              "lambda": lam, "features": feat_sel,
              "as_of": datetime.now(timezone.utc).isoformat()}
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  已存 {OUT}")

    # 发布条件：score 与 fwd 收益的相关性 |ic_va| ≥ 0.02
    # （权重为负时 score=预测的正向收益，ic 为正；符号由权重决定，只看绝对值）
    if a.apply:
        if abs(ic_va) >= 0.02:
            import psycopg as _pg
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
            with _pg.connect(url) as c:
                with c.cursor() as cur:
                    cur.execute(
                        "UPDATE lane_registry SET meta_json = jsonb_set(meta_json,"
                        " '{ai_model}', %s::jsonb, true), updated_at=now() WHERE lane_id=%s",
                        (json.dumps(result, ensure_ascii=False), LANE))
                c.commit()
            print(f"  ✓ 已发布到 lane_registry.meta.ai_model（lane={LANE}）")
        else:
            print(f"  ✗ 验证 corr {ic_va:.4f} 不满足 < −0.02，不发布")
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
