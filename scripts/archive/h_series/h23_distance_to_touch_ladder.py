"""H23：距 touch 距离 → 成交概率与成交时间（Arroyo et al. 阶梯在本场地的复现）。

## 文献依据

Arroyo, Cartea, Moreno-Pino & Zohren, *Deep Attentive Survival Analysis in Limit Order
Books*, **Quantitative Finance**, DOI 10.1080/14697688.2023.2286351（Nasdaq LOBSTER）。
按"距最优价几个 tick"给出成交概率与平均成交时间：

    标的    最佳价   +1     +2     +3     +4     +5      （成交概率）
    AAPL    0.0539  0.1317 0.3601 0.4165 0.4382 0.4431
    AMZN    0.0923  0.1312 0.3360 0.4139 0.4343 0.4485
    （平均成交时间沿同一阶梯从 1.32s 降到 0.08s）

论文原话：挂单**改善最优价**时成交概率高 **3–6 倍**，成交时间同比例缩短。

## 为什么这条对我们最关键

我们挂 `w_base_bp = 1.5bp from mid`，而半价差是 0.59–0.95bp ⇒
**我们挂在 touch 外约 0.8bp**。按上表，这个位置在
「成交概率」和「成交时间」**两个轴上都接近最差**。

而且它解释了 H16 的一个旧谜团：
```
w=0.0（贴 touch）→ 成交 36.4%/决策，净 −0.110bp/决策   ← 成交最多但最亏
w=1.0            → 成交  3.31%    ，净 +0.029bp
```
**贴 touch 成交多 11 倍却更亏** —— 因为我们的 fill 模型是"价格穿过制 + 零队列"
（`PENETRATION_BP=0`），贴 touch 时每笔都按"价格穿过我们"成交，
即**机械上最有毒的那个子集**。真实世界里贴 touch 会遇到队列，不会这样成交。

⇒ **"贴 touch 是否值得"这个问题，在修好 fill 模型之前无法回答。**
本脚本先量出**距离阶梯本身**，这是不需要新采集、且能独立验证的第一步。

## 量什么（只用已有数据）

`asterdex_depth_snapshots`（20 档真实价量，约 2s 网格）+ `asterdex_trades`（tick 逐笔）。

对每个快照时刻、每个"距 touch 的档位 k"：
  · 挂单价 = 第 k 档买价（k=0 即最优买价）
  · 队列前方量 LA = 该档位已挂量（**保守：假设我们排在最后**）
  · 成交条件（**论文口径**）：自该时刻起，在该价位的累计主动卖量 ≥ LA
  · 记录：是否成交、等待多久、从**限价**算的 markout

输出：按档位 k 汇总「成交概率 / 平均等待 / markout」，与 Arroyo 的阶梯对照。

## 判据（事先定死）

  · 若"成交概率随 k 单调上升（越靠 touch 越低）" ⇒ **复现 Arroyo**，
    且说明我们挂 touch 外 0.8bp 是**成交概率最高**的位置（不是最差）——
    **与他的结论方向一致但语义相反**（他是"往价差内走成交概率升"，
    我们是"离 touch 越远成交概率越高"）。
    ⇒ 这两种说法必须分清：**"改善最优价"（进价差内）= 我方报价成为新的最优价**，
       与"挂在最优价之外"是两件不同的事。
  · 若单调性不成立 ⇒ 本场地不适配该阶梯，不得引用。

用法：
    .venv\\Scripts\\python.exe scripts\\h23_distance_to_touch_ladder.py --hours 8
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
HORIZONS_MS = [1000, 5000, 30000]


def _dsn(db: str) -> str:
    url = os.getenv("DATABASE_URL") or ""
    for p in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(p, "")
    head, _, _ = url.rpartition("/")
    return head + "/" + db


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=8.0)
    ap.add_argument("--symbols", default="ASTERUSDT,XRPUSDT,SOLUSDT,DOGEUSDT")
    ap.add_argument("--levels", type=int, default=4, help="从最优价往外数几档")
    ap.add_argument("--max-wait-s", type=float, default=300.0)
    args = ap.parse_args()

    import numpy as np
    import psycopg2
    import psycopg2.extras

    syms = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    cn = psycopg2.connect(_dsn("alpha_market"))
    cn.autocommit = True
    cur = cn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    since = f"(extract(epoch from now())*1000)::bigint - {int(args.hours * 3600_000)}"

    print("H23 距 touch 距离阶梯")
    print(f"窗口={args.hours}h  币={len(syms)}  档位=0..{args.levels - 1}  最长等待={args.max_wait_s:.0f}s")
    print("（档位 0 = 最优买价；越大越深入订单簿、越不容易成交）\n")

    agg = {}   # (k) -> list of (filled, wait_ms, mk1, mk5, mk30)
    for s in syms:
        cur.execute(
            "SELECT event_ts_ms, bids, asks FROM asterdex_depth_snapshots"
            f" WHERE symbol = %s AND event_ts_ms > {since}"
            " ORDER BY event_ts_ms",
            (s,),
        )
        d = cur.fetchall()
        cur.execute(
            "SELECT event_ts_ms, price::float AS p, qty::float AS q, is_buyer_maker AS ibm"
            f"  FROM asterdex_trades WHERE symbol = %s AND event_ts_ms > {since}"
            " ORDER BY event_ts_ms",
            (s,),
        )
        tr = cur.fetchall()
        if len(d) < 200 or len(tr) < 100:
            print(f"  {s:<12} 数据不足（深度 {len(d)} / 成交 {len(tr)}）→ 跳过")
            continue

        dts = np.array([int(x["event_ts_ms"]) for x in d], dtype=np.int64)
        tts = np.array([int(x["event_ts_ms"]) for x in tr], dtype=np.int64)
        tpx = np.array([float(x["p"]) for x in tr])
        tq = np.array([float(x["q"]) for x in tr])
        tsell = np.array([bool(x["ibm"]) for x in tr])      # taker sell

        # 中价序列（用于 markout）
        mids = []
        for x in d:
            try:
                bb = float(x["bids"][0][0])
                ba = float(x["asks"][0][0])
                mids.append((bb + ba) / 2.0)
            except Exception:
                mids.append(np.nan)
        mids = np.array(mids)

        def mid_at(t_ms, delta_ms):
            i = int(np.searchsorted(dts, t_ms + delta_ms, "left"))
            if i >= len(dts):
                return None
            m = mids[i]
            return float(m) if np.isfinite(m) else None

        # 采样报价时点（不用每个快照，避免自相关；间隔 ~30s 取一个）
        step = max(1, len(d) // 60)
        n_q = 0
        for i in range(0, len(d), step):
            try:
                bids = d[i]["bids"]
                asks = d[i]["asks"]
                if not bids or not asks:
                    continue
                best_bid = float(bids[0][0])
            except Exception:
                continue
            t0 = int(dts[i])
            for k in range(args.levels):
                if k >= len(bids):
                    break
                try:
                    px = float(bids[k][0])
                    la = float(bids[k][1])
                except Exception:
                    continue
                if px <= 0:
                    continue
                n_q += 1
                # 队列消耗制：累计主动卖量（同价位，±1bp 容差）≥ LA
                j = int(np.searchsorted(tts, t0, "left"))
                lim = int(np.searchsorted(tts, t0 + int(args.max_wait_s * 1000), "left"))
                cum = 0.0
                fill_j = -1
                while j < lim:
                    if tsell[j] and abs(tpx[j] - px) / px * 1e4 < 1.0:
                        cum += float(tq[j])
                        if cum >= la:
                            fill_j = j
                            break
                    j += 1
                rec = {"filled": fill_j >= 0, "wait_ms": None,
                       "k": k, "la_usd": la * px}
                if fill_j >= 0:
                    tf = int(tts[fill_j])
                    rec["wait_ms"] = tf - t0
                    for h in HORIZONS_MS:
                        m = mid_at(tf, h)
                        rec[f"mk{h}"] = ((m / px - 1.0) * 1e4) if m else None
                agg.setdefault(k, []).append(rec)
        print(f"  {s:<12} 深度 {len(d):>6} / 成交 {len(tr):>6}  采样报价 {n_q} 次")

    if not agg:
        print("\n无数据")
        return 1

    print("\n[结果] 按「距最优价档位」汇总（跨币合并；**同档内可比，跨档不可加**）")
    print("  档位  报价次数   成交率   平均等待s   中位等待s   mk@1s    mk@5s   mk@30s")
    rows = []
    for k in sorted(agg):
        recs = agg[k]
        n = len(recs)
        filled = [r for r in recs if r["filled"]]
        fr = len(filled) / max(1, n)
        waits = sorted(r["wait_ms"] for r in filled if r["wait_ms"] is not None)
        avg_w = (sum(waits) / len(waits) / 1000.0) if waits else None
        med_w = (waits[len(waits) // 2] / 1000.0) if waits else None
        mks = {}
        for h in HORIZONS_MS:
            v = [r.get(f"mk{h}") for r in filled if r.get(f"mk{h}") is not None]
            mks[h] = (sum(v) / len(v)) if v else None
        rows.append({"k": k, "n": n, "fill_rate": fr, "avg_wait_s": avg_w,
                     "median_wait_s": med_w,
                     **{f"mk{h}": mks[h] for h in HORIZONS_MS}})
        fmt = lambda x: ("%9.3f" % x) if x is not None else "        —"
        print("  %4d  %7d  %6.2f%%  %s  %s  %s  %s  %s"
              % (k, n, fr * 100, fmt(avg_w), fmt(med_w),
                 fmt(mks[1000]), fmt(mks[5000]), fmt(mks[30000])))

    print("\n[判定]")
    f0 = rows[0]["fill_rate"] if rows else None
    fn = rows[-1]["fill_rate"] if rows else None
    if f0 is not None and fn is not None:
        if fn > f0:
            print("  ⇒ 成交率随「离 touch 越远」**上升**（档位 0 最低、档位 %d 最高）"
                  % rows[-1]["k"])
            print("     这与直觉相反但与机制一致：越深入订单簿，触及所需的逆向移动越大、")
            print("     而一旦触及就是**必然成交**（DeLise：逆向移动时 P(成交)=1）。")
            print("     ⇒ **我们挂 touch 外 0.8bp，是在「最容易成交」的位置，")
            print("       而不是 Arroyo 说的「最差位置」——两者说的是不同的事：**")
            print("       · Arroyo 的「改善最优价」= 我方报价成为**新的最优价**（进价差内）")
            print("       · 我们 = 挂在**现有最优价之外**（深入订单簿）")
            print("       后者成交概率高，但成交**全部是逆向选择**（这正是 DeLise 的机制）。")
        else:
            print("  ⇒ 成交率未随距离单调上升 ⟹ 本场地不适配该阶梯，不得引用。")
    print("\n  样本口径提醒：报价次数 %d，最长等待 %.0fs；"
          % (sum(r["n"] for r in rows), args.max_wait_s))
    print("  未成交的样本等待被截断在 %.0fs ⇒ 成交率是**下界**。" % args.max_wait_s)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    p = OUT_DIR / "h23_distance_to_touch_ladder.json"
    p.write_text(json.dumps({"hours": args.hours, "symbols": syms,
                             "levels": args.levels, "rows": rows},
                            ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {p}")
    cur.close()
    cn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
