# -*- coding: utf-8 -*-
"""H282 特征研究：比"过去 N 秒涨跌"更强的 60s 方向特征（IC 排序）。

# 问题：固定规则只用 r120 一个特征；H280 显示其 corr 只有 −0.016。
# 本脚本评测一批微观结构特征的 IC（信息系数）：
    r60      过去 60s 中价收益（bp）        —— 基线特征
    ofi60    60s 主动买卖失衡 = (taker买额−taker卖额)/(总额)
    vwap60   ｜现价 − 60s VWAP｜偏差（bp）    —— 超买超卖度
    inten60  60s 成交笔数（活跃度）
    r30      过去 30s 收益（短尺度）
    r180     过去 180s 收益（长尺度）
# 标签：fwd 60s 中价收益（bp）。口径：1s 网格、60s 去重叠、48h、当前 4 币。
# 判据：IC 绝对值 >0.02 且 t>3 才值得进模型；与 r60 对比增量价值。

# 用法

    python scripts/h282_feature_ic.py --hours 48
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections import defaultdict

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "research_l1" / "out" / "h282_feature_ic.json"
CUR = ["SOLUSDT", "DOGEUSDT", "ETHUSDT", "BNBUSDT"]


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
    ap.add_argument("--hours", type=float, default=48.0)
    a = ap.parse_args()

    import psycopg

    mids = {}
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
        mids[sym] = (ks, [d[k] for k in ks])
        print(f"  mid {sym}: {len(ks)} 点", flush=True)

    # 成交流：逐笔 (ts_ms, price, qty, is_buyer_maker)
    trades = {}
    for sym in CUR:
        with psycopg.connect(market_dsn()) as c:
            with c.cursor() as cur:
                cur.execute("""
                    SELECT event_ts_ms, price, qty, is_buyer_maker FROM asterdex_trades
                    WHERE event_ts_ms >= (extract(epoch from now())*1000 - %s*3600*1000)::bigint
                      AND symbol = %s ORDER BY event_ts_ms
                """, (a.hours, sym))
                recs = cur.fetchall()
        trades[sym] = [(int(r[0]), float(r[1]), float(r[2]), bool(r[3])) for r in recs]
        print(f"  trades {sym}: {len(trades[sym])} 笔", flush=True)

    # 事件（60s 去重叠），逐币算特征。⚠️ 每事件的所有特征必须同一条记录配对
    # （上一版把 r30/r60/r180 单独 append 而 label 按事件 append，
    #   稀疏币的 None 洞造成配对错位 → IC 符号翻转，H282b 修复）。
    recs_all = []
    for sym in CUR:
        ks, px = mids[sym]
        n = len(ks)
        tr = trades[sym]
        tp = 0  # trades 指针
        last = -1e18
        for i in range(n):
            if ks[i] - last < 60.0:
                continue
            last = ks[i]
            # fwd60
            f = i
            while f + 1 < n and ks[f + 1] - ks[i] < 60.0:
                f += 1
            if f == i or ks[f] - ks[i] < 54 or px[i] <= 0:
                continue
            y = (px[f] - px[i]) / px[i] * 1e4
            # 过去收益 r30/r60/r180
            def past(k):
                j = i
                while j >= 0 and ks[i] - ks[j] < k:
                    j -= 1
                if j < 0 or ks[i] - ks[j] < k * 0.9 or px[j] <= 0:
                    return None
                return (px[i] - px[j]) / px[j] * 1e4
            r30, r60, r180 = past(30), past(60), past(180)
            # 成交流窗口 [ks[i]-60, ks[i]]
            t0_ms = (ks[i] - 60) * 1000
            t1_ms = ks[i] * 1000
            while tp < len(tr) and tr[tp][0] < t0_ms:
                tp += 1
            q = tp
            buy_n = sell_n = 0.0
            total_n = 0.0
            cnt = 0
            vwap_num = vwap_den = 0.0
            while q < len(tr) and tr[q][0] <= t1_ms:
                p, qt, buyer_maker = tr[q][1], tr[q][2], tr[q][3]
                notional = p * qt
                if not buyer_maker:
                    buy_n += notional
                else:
                    sell_n += notional
                total_n += notional
                vwap_num += p * notional
                vwap_den += notional
                cnt += 1
                q += 1
            ofi = (buy_n - sell_n) / total_n if total_n > 0 else 0.0
            vwap = vwap_num / vwap_den if vwap_den > 0 else px[i]
            vwap_dev = (px[i] - vwap) / vwap * 1e4
            recs_all.append({"y": y, "r30": r30, "r60": r60, "r180": r180,
                             "ofi60": ofi, "vwap60": vwap_dev, "inten60": float(cnt)})

    print(f"\n  事件数: {len(recs_all)}")
    print(f"\n  {'特征':>10} {'IC':>8} {'t':>7} {'n':>7}  含义")
    out = {}
    for name in ("r30", "r60", "r180", "ofi60", "vwap60", "inten60"):
        sub = [r for r in recs_all if r[name] is not None]
        xs = [r[name] for r in sub]
        ys = [r["y"] for r in sub]
        r, t = pearson(xs, ys)
        out[name] = {"ic": round(r, 5), "t": round(t, 2), "n": len(xs)}
        desc = {"r30": "过去30s收益", "r60": "过去60s收益（基线）", "r180": "过去180s收益",
                "ofi60": "60s主动买卖失衡", "vwap60": "现价-VWAP偏差",
                "inten60": "60s成交笔数"}[name]
        print(f"  {name:>10} {r:>8.4f} {t:>7.1f} {len(xs):>7}  {desc}")
    # 多元：r60 + vwap60（对齐子集）
    try:
        import numpy as np
        sub = [r for r in recs_all if r["r60"] is not None]
        X = np.array([[r["r60"], r["vwap60"], r["ofi60"]] for r in sub])
        Y = np.array([r["y"] for r in sub])
        Xm = X - X.mean(0)
        Ym = Y - Y.mean()
        beta = np.linalg.lstsq(Xm, Ym, rcond=None)[0]
        pred = Xm @ beta
        r_multi, _ = pearson(list(pred), list(Ym))
        print(f"\n  多元(线性 r60+vwap60+ofi60) 样本内 corr = {r_multi:.4f}")
        out["multi_in_sample"] = round(float(r_multi), 5)
        out["beta"] = [round(float(b), 5) for b in beta]
    except Exception as e:
        print(f"\n  多元拟合失败: {e}")

    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  已存 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
