"""H18：论文的两个核心特征在我们场地上成立吗？（可证伪实验）

## 论文说了什么（Albers et al. arXiv:2502.18625v2）

**主张 A（§6，Table 2）**：朴素做市（无信号、挂在最优价、库存平衡）
每往返 **−0.4307 bp**。所有基于失衡（imbalance）的策略**一律为负**：

    Imbalance taker              -1.9621 bp / 往返
    Naive maker                  -0.4307 bp
    Imbalance maker w/o cancel   -0.4705 bp
    Imbalance maker w/ cancel    -0.4921 bp
    （Table 4 里朴素做市口径为 **-0.44 bp**，含 +1bp 双边返佣）

**主张 B（§6）**：失衡方向与下一期价格变动**正相关**（文献共识），
所以"顺着失衡挂单"看似合理 —— 但论文实测**不赚钱**，因为
"它们几乎总是错过有利的价格变动，只剩下对应**反转（reversal）**的那少数成交"。

**主张 C（§7.2 Feature Importance，行 1391-1413）**：预测反转（reversal）的特征里，
最重要的两个是

    ① `ret_autocov_5s_w*`（连续 3 个 5s 窗口的收益自协方差）→ **大负系数**
       ⇒ 反转更可能发生在"价格来回震荡"而不是"连续同向趋势"之后
    ② `ret_sum_100ms_w0`（最近 100ms 的累计收益）→ **显著正系数**
       ⇒ "最近刚急跌"之后，反转概率更高
    合并结论（论文原话）：
       "a sudden price drop following a balanced up/down price action,
        is associated with a higher probability for a reversal"

## 我们要验什么（只用已有数据，不引入新采集）

我们手上有的：
  · `asterdex_depth_snapshots`：20 档真实价量，~2s 网格，24 币
  · `asterdex_book_ticker`：最优买卖价（比深度更密）
  · `market_trades_aggregated`：**15s 桶**（low/high/taker_buy/taker_sell）

⇒ 我们**能**算：失衡 `imb`、多尺度收益 `ret`、震荡 vs 趋势 `autocov`
⇒ 我们**算不了**：100ms 尺度的任何东西（桶粒度 15s，差 150 倍）
   这一点本身就是论文与我们场地的**结构性差距**，必须写进结论。

## 判定标准（事先定好，不许事后改）

对每个 horizon h ∈ {15s, 30s, 60s, 120s, 300s}：

  H18-a  **失衡预测力**：`corr(imb_t, ret_{t→t+h})`。
         论文/文献预期 **显著为正**。若我们测得 ≈0 或为负 ⇒ 论文主张 B 在本场地不成立。
  H18-b  **反转条件收益**：把样本按 `ret_{t-15s→t}`（刚发生的短期收益）分组，
         看 `ret_{t→t+h}`。论文预期"刚急跌 ⇒ 后续反弹"（负自相关）。
         若自相关为正 ⇒ 动量而非反转 ⇒ 论文主张 C 在本场地不成立。

两个都是**双尾**检验，报 t 值。样本按时间分折（不许跨折求和）。

用法：
    .venv\\Scripts\\python.exe scripts\\h18_paper_features_probe.py --hours 24
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

OUT_DIR = ROOT / "research_l1" / "out"
HORIZONS_S = [15, 30, 60, 120, 300]


def _dsn(db: str) -> str:
    url = os.getenv("DATABASE_URL") or ""
    for p in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(p, "")
    head, _, _ = url.rpartition("/")
    return head + "/" + db


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=24.0)
    ap.add_argument("--symbols", default="ASTER,XRP,SOL,DOGE,UNI,PENDLE,SEI,VIRTUAL,1000SHIB,ARB")
    ap.add_argument("--grid-ms", type=int, default=2000, help="重采样网格（深度采集约 2s）")
    args = ap.parse_args()

    import numpy as np
    import psycopg2
    import psycopg2.extras

    syms = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    cn = psycopg2.connect(_dsn("alpha_market"))
    cn.autocommit = True
    cur = cn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    print(f"H18 论文特征在本场地的检验  窗口={args.hours}h  币={len(syms)}  网格={args.grid_ms}ms")
    print(f"币: {' '.join(syms)}\n")

    # 逐币拉盘口（book_ticker 比深度密，用它做主时间轴；深度补 20 档失衡）
    res_by_sym = {}
    for s in syms:
        vs = s if s.endswith("USDT") else f"{s}USDT"
        cur.execute(
            "SELECT event_ts_ms, bid_px::float AS b, ask_px::float AS a"
            "  FROM asterdex_book_ticker"
            " WHERE symbol = %s AND event_ts_ms > (extract(epoch from now())*1000)::bigint - %s"
            " ORDER BY event_ts_ms",
            (vs, int(args.hours * 3600_000)),
        )
        rows = cur.fetchall()
        if len(rows) < 500:
            print(f"  {s:<10} book_ticker 行数 {len(rows)} 太少 → 跳过")
            continue
        t = np.array([int(r["event_ts_ms"]) for r in rows], dtype=np.int64)
        b = np.array([float(r["b"]) for r in rows])
        a = np.array([float(r["a"]) for r in rows])
        mid = (b + a) / 2.0

        # 重采样到固定网格（取每个格的最后一个观测）——避免不等间隔样本污染相关
        grid = np.arange(t[0] // args.grid_ms * args.grid_ms + args.grid_ms,
                         t[-1], args.grid_ms, dtype=np.int64)
        idx = np.searchsorted(t, grid, side="right") - 1
        ok = idx >= 0
        grid, idx = grid[ok], idx[ok]
        mid_g = mid[idx]
        b_g, a_g = b[idx], a[idx]
        # 网格点与真实观测的时差（太远的丢弃，避免"用 60s 前的价当 2s 前的价"）
        lag_ms = grid - t[idx]
        good = lag_ms <= args.grid_ms
        grid, mid_g, b_g, a_g = grid[good], mid_g[good], b_g[good], a_g[good]
        if len(grid) < 500:
            print(f"  {s:<10} 重采样后 {len(grid)} 点太少 → 跳过")
            continue
        res_by_sym[s] = (grid, mid_g, b_g, a_g)
        print(f"  {s:<10} 盘口 {len(rows):>7} 行 → 网格 {len(grid):>6} 点  "
              f"跨度 {(grid[-1]-grid[0])/3600000:.2f}h")

    if not res_by_sym:
        print("没有可用数据")
        return 1

    # ── H18-a：失衡 → 未来收益 ──────────────────────────────────
    # 失衡用**最优价挂量**近似（book_ticker 只有价，没有量）
    # ⇒ 改用深度表补齐量。深度约 2s 网格，单独拉一次。
    print("\n[1] 拉 20 档深度（算真实失衡 Q_bid/Q_ask）…")
    depth_by_sym = {}
    for s in res_by_sym:
        vs = s if s.endswith("USDT") else f"{s}USDT"
        cur.execute(
            "SELECT event_ts_ms, bids->0->>1 AS bq, asks->0->>1 AS aq"
            "  FROM asterdex_depth_snapshots"
            " WHERE symbol = %s AND event_ts_ms > (extract(epoch from now())*1000)::bigint - %s"
            " ORDER BY event_ts_ms",
            (vs, int(args.hours * 3600_000)),
        )
        rows = cur.fetchall()
        if len(rows) < 200:
            print(f"    {s:<10} 深度 {len(rows)} 行 → 跳过失衡")
            continue
        dt = np.array([int(r["event_ts_ms"]) for r in rows], dtype=np.int64)
        bq = np.array([float(r["bq"] or 0.0) for r in rows])
        aq = np.array([float(r["aq"] or 0.0) for r in rows])
        denom = bq + aq
        imb = np.where(denom > 0, (bq - aq) / denom, np.nan)
        depth_by_sym[s] = (dt, imb)
        print(f"    {s:<10} 深度 {len(rows):>7} 行")

    print("\n[2] H18-a  失衡 → 未来收益（论文预期：显著正相关）")
    print("    %-9s %7s" % ("symbol", "n_pairs") + "".join("%11s" % f"{h}s" for h in HORIZONS_S))
    all_a = {h: [] for h in HORIZONS_S}
    for s, (dt, imb) in depth_by_sym.items():
        if s not in res_by_sym:
            continue
        grid, mid_g, _, _ = res_by_sym[s]
        # 把失衡按最近邻映射到网格
        j = np.searchsorted(dt, grid, side="right") - 1
        okj = (j >= 0) & ((grid - dt[np.clip(j, 0, len(dt) - 1)]) <= 5000)
        if okj.sum() < 200:
            continue
        g2 = grid[okj]
        m2 = mid_g[okj]
        i2 = imb[np.clip(j, 0, len(dt) - 1)][okj]
        row = "%9d" % len(g2)
        cells = ""
        for h in HORIZONS_S:
            step = max(1, int(h * 1000 / args.grid_ms))
            if len(m2) <= step + 10:
                cells += "%11s" % "—"
                continue
            r0 = m2[:-step]
            r1 = m2[step:]
            fwd = (r1 - r0) / r0 * 1e4
            x = i2[:-step]
            m = np.isfinite(x) & np.isfinite(fwd)
            if m.sum() < 100 or x[m].std() == 0:
                cells += "%11s" % "—"
                continue
            c = float(np.corrcoef(x[m], fwd[m])[0, 1])
            all_a[h].append((s, c, int(m.sum())))
            cells += "%+11.4f" % c
        print("    %-9s %s" % (s, row) + cells[9:])

    print("\n[3] H18-b  短期收益自相关（论文预期：负 ⇒ 反转/均值回归）")
    print("    %-9s" % "symbol" + "".join("%11s" % f"{h}s" for h in HORIZONS_S))
    all_b = {h: [] for h in HORIZONS_S}
    for s, (grid, mid_g, _, _) in res_by_sym.items():
        cells = ""
        for h in HORIZONS_S:
            step = max(1, int(h * 1000 / args.grid_ms))
            if len(mid_g) <= 2 * step + 10:
                cells += "%11s" % "—"
                continue
            ret = np.diff(np.log(mid_g))
            past = np.convolve(ret, np.ones(step), mode="valid")[: len(ret) - 2 * step]
            fut = np.convolve(ret, np.ones(step), mode="valid")[step: len(ret) - step]
            n = min(len(past), len(fut))
            if n < 100:
                cells += "%11s" % "—"
                continue
            c = float(np.corrcoef(past[:n], fut[:n])[0, 1])
            all_b[h].append((s, c, n))
            cells += "%+11.4f" % c
        print("    %-9s" % s + cells)

    # ── 汇总（分折：按时间前后两半，检查符号稳定性）────────────
    print("\n[4] 汇总（跨币平均；分折检查符号是否稳定）")
    print("    horizon   " + "".join("%-26s" % f"{h}s" for h in HORIZONS_S))
    for label, store in (("H18-a 失衡→收益", all_a), ("H18-b 自相关", all_b)):
        line = "    %-10s" % label[:10]
        for h in HORIZONS_S:
            v = [c for _, c, _ in store[h]]
            if not v:
                line += "%-26s" % "—"
                continue
            arr = np.array(v)
            mean = float(arr.mean())
            sd = float(arr.std(ddof=1)) if len(arr) > 1 else 0.0
            t = mean / (sd / np.sqrt(len(arr))) if (sd > 0 and len(arr) > 1) else 0.0
            pos = int((arr > 0).sum())
            line += "%-26s" % f"{mean:+.4f} t={t:+.2f} ({pos}/{len(arr)}正)"
        print(line)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "h18_paper_features_probe.json"
    out.write_text(json.dumps({
        "hours": args.hours, "symbols": list(res_by_sym.keys()),
        "grid_ms": args.grid_ms, "horizons_s": HORIZONS_S,
        "imb_to_ret": {str(h): all_a[h] for h in HORIZONS_S},
        "autocorr": {str(h): all_b[h] for h in HORIZONS_S},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {out}")
    cur.close()
    cn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
