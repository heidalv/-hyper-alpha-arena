# -*- coding: utf-8 -*-
"""H263 验证 3 收尾：候选币的逆势−顺势差（bp），不是 corr。

# 为什么 corr 不够

H262 测了候选池反转 corr，PENDLE −0.1357 最强。但 corr 是**线性**信号，
最终要交易的是**符号条件期望**：

    逆势策略收益 = −sign(trend_120s) × fwd_60s   （涨了做空、跌了做多，bp）
    顺势策略收益 = +sign(trend_120s) × fwd_60s
    逆势−顺势差 = 逆势均值 − 顺势均值

这个差值才是"逆势 vs 顺势"的可交易期望，与 H256/H257（账本腿）同口径，
只是这里用**纯 tick 模拟**（不需要账本腿，PENDLE 等候选币没有账本腿）。

# 本脚本

对每个候选币（PENDLE + 当前四币 + 几个反转强的）：
    1s 网格、lookback=120s、fwd=60s、30s 去重叠
    算逆势策略收益的均值/中位/为正占比，跨小时

# 判据

  · 逆势均值 > 0 且跨小时稳定 ⇒ 反转可交易（比 corr 更有力）
  · 与当前四币同口径对比，看 PENDLE 是否真的更强
  · 扣 taker 成本（若逆势单是 taker，往返 8bp）后是否仍为正

# 用法

    python scripts/h263_reversal_expectancy.py --hours 12
"""
from __future__ import annotations

import argparse
import json
import pathlib
import statistics as st
import sys
from datetime import datetime

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "research_l1" / "out" / "h263_reversal_expectancy.json"
SYMS = ["PENDLEUSDT", "ASTERUSDT", "XRPUSDT", "SOLUSDT", "HYPEUSDT",
        "BTCUSDT", "ETHUSDT", "XMRUSDT", "XLMUSDT", "SUIUSDT"]
K = 120.0
M = 60.0
STEP = 30.0


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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=12.0)
    a = ap.parse_args()

    import psycopg
    print("=" * 104)
    print(f"H263  逆势−顺势差（bp，纯 tick 模拟，lookback={K:g}s→fwd={M:g}s）")
    print("=" * 104)
    print(f"\n  窗口 {a.hours:g}h")

    rows_all = []
    for sym in SYMS:
        try:
            with psycopg.connect(market_dsn()) as c:
                with c.cursor() as cur:
                    cur.execute("""
                        SELECT DISTINCT ON (bucket) bucket, mid
                        FROM (SELECT (event_ts_ms/1000) AS bucket,
                                     (bid_px+ask_px)/2.0 AS mid
                              FROM asterdex_book_ticker
                              WHERE ingest_ts >= now() - (%s || ' hours')::interval
                                AND symbol = %s AND bid_px>0 AND ask_px>bid_px) t
                        ORDER BY bucket, mid
                    """, (str(float(a.hours) + 0.2), sym))
                    recs = cur.fetchall()
        except Exception as e:
            print(f"  ⚠️ {sym}: {str(e)[:50]}")
            continue
        d = {int(b): float(mid) for b, mid in recs}
        ks = sorted(d)
        mids = [d[k] for k in ks]
        n = len(ks)
        starts, last = [], -1e18
        for i in range(n):
            if ks[i] - last >= STEP:
                starts.append(i)
                last = ks[i]
        ct, mt = [], []
        hrs = {}
        for i in starts:
            j = i
            while j >= 0 and ks[i] - ks[j] < K:
                j -= 1
            if j < 0 or ks[i] - ks[j] < K * 0.9 or mids[j] <= 0:
                continue
            f = i
            while f + 1 < n and ks[f + 1] - ks[i] < M:
                f += 1
            if f == i or ks[f] - ks[i] < M * 0.9 or mids[i] <= 0:
                continue
            trend = (mids[i] - mids[j]) / mids[j] * 1e4
            fwd = (mids[f] - mids[i]) / mids[i] * 1e4
            # 逆势收益：涨了做空、跌了做多
            ct_ret = -1.0 * (1 if trend > 0 else -1) * fwd
            mt_ret = 1.0 * (1 if trend > 0 else -1) * fwd
            ct.append(ct_ret)
            mt.append(mt_ret)
            h = datetime.fromtimestamp(ks[i]).strftime("%H:00")
            hrs.setdefault(h, {"ct": [], "mt": []})
            hrs[h]["ct"].append(ct_ret)
            hrs[h]["mt"].append(mt_ret)
        bare = sym.replace("USDT", "")
        mct = st.mean(ct) if ct else 0.0
        mmt = st.mean(mt) if mt else 0.0
        pos = sum(1 for x in ct if x > 0) / len(ct) * 100 if ct else 0.0
        # 跨小时逆势为正的占比
        posh = 0
        nh = 0
        for h, v in hrs.items():
            if len(v["ct"]) >= 20:
                nh += 1
                if st.mean(v["ct"]) > 0:
                    posh += 1
        rows_all.append({"sym": bare, "ct_mean": mct, "mt_mean": mmt,
                         "diff": mct - mmt, "ct_pos": pos, "n": len(ct),
                         "pos_hours": f"{posh}/{nh}"})
        print(f"  {bare:<10} 逆势 {mct:+.4f}  顺势 {mmt:+.4f}  "
              f"差 {mct-mmt:+.4f}bp  逆势为正 {pos:.1f}%  小时 {posh}/{nh}  n={len(ct)}")

    rows_all.sort(key=lambda x: -x["diff"])
    print(f"\n{'━'*104}\n  按逆势−顺势差排序\n{'━'*104}")
    print(f"\n  {'symbol':<10}{'逆势':>9}{'顺势':>9}{'差':>9}{'逆势为正%':>12}{'跨小时':>9}")
    for r in rows_all:
        print(f"  {r['sym']:<10}{r['ct_mean']:>+9.3f}{r['mt_mean']:>+9.3f}"
              f"{r['diff']:>+9.3f}{r['ct_pos']:>11.1f}%{r['pos_hours']:>9}")

    print(f"\n{'━'*104}\n  结论\n{'━'*104}")
    cur = [r for r in rows_all if r["sym"] in ("ASTER", "SOL", "XRP", "HYPE")]
    pen = [r for r in rows_all if r["sym"] == "PENDLE"]
    if cur:
        print(f"\n  当前四币逆势均值："
              f"{', '.join(f'{r['sym']}={r['ct_mean']:+.3f}' for r in cur)}")
    if pen:
        print(f"  PENDLE 逆势均值 = {pen[0]['ct_mean']:+.3f}bp　"
              f"差 {pen[0]['diff']:+.3f}bp　跨小时 {pen[0]['pos_hours']}")
    print(f"\n  ⚠️ 逆势均值是**未扣成本**的毛利；若逆势单走 maker（0 fee）则近似净额；")
    print(f"     若走 taker 要扣 2×4bp。PENDLE tick 率低（0.92/s），绝对收益再看成交率。")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"hours": a.hours, "k": K, "m": M, "rows": rows_all},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  写出 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
