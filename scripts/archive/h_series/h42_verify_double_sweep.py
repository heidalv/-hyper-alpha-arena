"""H42：验证 w=0.5 的"双扫"是否真实存在（决定 +0.2737bp 是否成立）。

## 要验证什么

H40 挂宽扫描给出 w=0.5 时：
    往返 24049 / 24h（= 1002/小时）
    净/往返 +0.2737bp
    胜率 92.7%
    **持仓中位 2.1 秒，强平 0%**

**这要求**：价格在极短时间内先走到 `bid − 0.5bp`（打我们的买单），
再走到 `ask + 0.5bp`（打我们的卖单）。
在 1.18bp 价差的币上，这等于**价格在几毫秒内来回 2.4bp**。

**若这种"双扫"很稀有 ⇒ 24049 个往返是判定逻辑的产物，不是真实可捕的机会。**

## 判定方法（不猜）

用 `asterdex_trades` 逐笔，对每个挂单时点统计：
  · `t_sell` = 第一笔"价格 ≤ bid−0.5bp 的主动卖"的时刻
  · `t_buy`  = 其后第一笔"价格 ≥ ask+0.5bp 的主动买"的时刻（ask 用**成交时刻**的最优卖价）
  · 记录 `t_buy − t_sell` 的分布

**关键指标**：
  · 完成率（在 300s 内两个方向都出现）
  · `t_buy − t_sell` 的中位/分位 —— 若中位不是"毫秒级"，则 H40 的 2.1s 持仓中位不成立
  · **净额**用真实成交价重算：(t_buy 时刻的成交价 − t_sell 时刻的成交价)/... × 1e4

## 判据（事先定死）

  · 若 `t_buy − t_sell` 中位 ≥ 5s ⇒ H40 的"2.1s 持仓"是**判定逻辑造成的**，
    +0.2737bp 不可信
  · 若中位 < 1s 且完成率 ≥ 60% ⇒ 双扫真实存在，H40 的结论可继续
  · 无论哪种，**净额都按真实成交价重算**并与 H40 对照

用法：
    .venv\\Scripts\\python.exe scripts\\h42_verify_double_sweep.py --hours 24
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

OUT = ROOT / "research_l1" / "out" / "h42_double_sweep_verify.json"


def _dsn(db: str) -> str:
    url = os.getenv("DATABASE_URL") or ""
    for p in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(p, "")
    head, _, _ = url.rpartition("/")
    return head + "/" + db


def run_symbol(s, hours, w_bp, quote_every_s, max_wait_s, log):
    import numpy as np
    import psycopg2
    import psycopg2.extras

    vs = s if s.endswith("USDT") else f"{s}USDT"
    cn = psycopg2.connect(_dsn("alpha_market"))
    cn.autocommit = True
    cur = cn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    since = f"(extract(epoch from now())*1000)::bigint - {int(hours*3600_000)}"
    cur.execute(
        "SELECT event_ts_ms, (bids->0->>0)::float bp, (asks->0->>0)::float ap"
        f"  FROM asterdex_depth_snapshots WHERE symbol=%s AND event_ts_ms > {since}"
        " ORDER BY event_ts_ms", (vs,))
    dep = cur.fetchall()
    cur.execute(
        "SELECT event_ts_ms, price::float p, qty::float q, is_buyer_maker ibm"
        f"  FROM asterdex_trades WHERE symbol=%s AND event_ts_ms > {since}"
        " ORDER BY event_ts_ms", (vs,))
    bt = cur.fetchall()
    cur.execute(
        "SELECT event_ts_ms, bid_px::float b, ask_px::float a"
        f"  FROM asterdex_book_ticker WHERE symbol=%s AND event_ts_ms > {since}"
        " ORDER BY event_ts_ms", (vs,))
    tk = cur.fetchall()
    cn.close()
    if len(dep) < 2000 or len(bt) < 1000 or len(tk) < 2000:
        return None

    dts = np.array([int(x["event_ts_ms"]) for x in dep], dtype=np.int64)
    dbp = np.array([float(x["bp"] or 0) for x in dep])
    dap = np.array([float(x["ap"] or 0) for x in dep])
    tts = np.array([int(x["event_ts_ms"]) for x in bt], dtype=np.int64)
    tpx = np.array([float(x["p"]) for x in bt])
    tsell = np.array([bool(x["ibm"]) for x in bt])
    kts = np.array([int(x["event_ts_ms"]) for x in tk], dtype=np.int64)
    kask = np.array([float(x["a"] or 0) for x in tk])
    kbid = np.array([float(x["b"] or 0) for x in tk])

    def ask_at(t_ms):
        i = int(np.searchsorted(kts, t_ms, "left"))
        if i >= len(kts):
            i = len(kts) - 1
        return float(kask[i]) if i >= 0 else 0.0

    step = max(1, int(quote_every_s * 1000 / max(1, int(np.median(np.diff(dts)) or 2000))))
    qidx = np.arange(0, len(dts), step, dtype=np.int64)
    max_ms = int(max_wait_s * 1000)

    complete, incomplete = 0, 0
    gaps = []
    pnl = []
    for i in qidx:
        bid = float(dbp[i])
        if bid <= 0:
            continue
        px_buy = bid * (1.0 - w_bp / 1e4)
        t0 = int(dts[i])
        # ① 第一笔打到 px_buy 的主动卖
        j0 = int(np.searchsorted(tts, t0, "left"))
        j1 = int(np.searchsorted(tts, t0 + max_ms, "left"))
        je = -1
        for jj in range(j0, min(j1, len(tts))):
            if tsell[jj] and tpx[jj] <= px_buy * (1.0 + 1.0 / 1e4):
                je = jj
                break
        if je < 0:
            incomplete += 1
            continue
        t_entry = int(tts[je])
        entry_px = float(tpx[je])          # **真实成交价**，不是我们的报价
        # ② 其后第一笔打到 px_sell 的主动买（px_sell 用入场时刻的最优卖价 + w）
        a0 = ask_at(t_entry)
        if a0 <= 0:
            incomplete += 1
            continue
        px_sell = a0 * (1.0 + w_bp / 1e4)
        j2 = int(np.searchsorted(tts, t_entry, "left"))
        j3 = int(np.searchsorted(tts, t_entry + max_ms, "left"))
        jx = -1
        for jj in range(j2, min(j3, len(tts))):
            if (not tsell[jj]) and tpx[jj] >= px_sell * (1.0 - 1.0 / 1e4):
                jx = jj
                break
        if jx < 0:
            incomplete += 1
            continue
        complete += 1
        t_exit = int(tts[jx])
        exit_px = float(tpx[jx])           # **真实成交价**
        gaps.append(t_exit - t_entry)
        pnl.append((exit_px - entry_px) / entry_px * 1e4)

    if not gaps:
        log(f"  {s:<8} 无完整双扫（未完成 {incomplete}）")
        return None
    g = np.array(gaps)
    p = np.array(pnl)
    return {
        "symbol": s, "w_bp": w_bp,
        "n_quotes": int(len(qidx)), "n_complete": complete, "n_incomplete": incomplete,
        "complete_rate": complete / max(1, complete + incomplete),
        "gap_ms_median": float(np.median(g)),
        "gap_ms_p25": float(np.percentile(g, 25)),
        "gap_ms_p75": float(np.percentile(g, 75)),
        "gap_ms_p90": float(np.percentile(g, 90)),
        "pnl_bp_mean": float(p.mean()),
        "pnl_bp_median": float(np.median(p)),
        "win_rate": float((p > 0).mean()),
    }


def main() -> int:
    import numpy as np

    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=24.0)
    ap.add_argument("--symbols", default="BTC,ETH,SOL,XRP,ASTER")
    ap.add_argument("--w-bp", type=float, default=0.5)
    ap.add_argument("--quote-every-s", type=float, default=15.0)
    ap.add_argument("--max-wait-s", type=float, default=300.0)
    args = ap.parse_args()
    syms = [x.strip().upper() for x in args.symbols.split(",") if x.strip()]

    print("H42 双扫真实性验证（用真实逐笔成交价，不用我们的报价）")
    print(f"窗口={args.hours}h  币={len(syms)}  挂宽 w={args.w_bp}bp  "
          f"报价节奏={args.quote_every_s:.0f}s  最长等待={args.max_wait_s:.0f}s\n")

    res = []
    for s in syms:
        r = run_symbol(s, args.hours, args.w_bp, args.quote_every_s, args.max_wait_s, print)
        if r:
            res.append(r)
            print("  %-8s 完成 %5d/%5d (%5.1f%%)  间隔中位 %8.0fms "
                  "(p25 %6.0f p75 %7.0f p90 %7.0f)  净额 %+7.4fbp  胜率 %4.1f%%"
                  % (s, r["n_complete"], r["n_complete"] + r["n_incomplete"],
                     100 * r["complete_rate"], r["gap_ms_median"],
                     r["gap_ms_p25"], r["gap_ms_p75"], r["gap_ms_p90"],
                     r["pnl_bp_mean"], 100 * r["win_rate"]))
    if not res:
        print("\n无双扫样本")
        return 1

    tot = sum(r["n_complete"] for r in res)
    cr = sum(r["complete_rate"] * (r["n_complete"] + r["n_incomplete"]) for r in res) / \
        max(1, sum(r["n_complete"] + r["n_incomplete"] for r in res))
    gm = float(np.median([r["gap_ms_median"] for r in res]))
    pm = sum(r["pnl_bp_mean"] * r["n_complete"] for r in res) / max(1, tot)
    wr = sum(r["win_rate"] * r["n_complete"] for r in res) / max(1, tot)

    print("\n[汇总]")
    print("    完整双扫数        %d" % tot)
    print("    完成率            %.1f%%" % (100 * cr))
    print("    **入场→出场间隔中位 %.0f ms**" % gm)
    print("    **净额（真实成交价）%+.4f bp/往返**" % pm)
    print("    胜率              %.1f%%" % (100 * wr))
    print("    ⇒ 每 $30 腿 ≈ %+.6f 美元/往返" % (pm / 1e4 * 30))

    print("\n[对照 H40]")
    print("    H40（w=0.5）：持仓中位 2068ms，净 +0.2737bp，胜率 92.7%%，强平 0%%")
    print("    H42（真实价）：间隔中位 %.0fms，净 %+.4fbp，胜率 %.1f%%" % (gm, pm, 100 * wr))

    print("\n[判据（事先定死）]")
    if gm >= 5000:
        print("    ⇒ ✗ 间隔中位 %.0fms ≥ 5s ⇒ H40 的「2.1s 持仓」是**判定逻辑造成的**，" % gm)
        print("      +0.2737bp **不可信**。")
    elif gm < 1000 and cr >= 0.60:
        print("    ⇒ ✓ 间隔中位 %.0fms < 1s 且完成率 %.1f%% ≥ 60%% ⇒ **双扫真实存在**，"
              % (gm, 100 * cr))
        print("      H40 的结论可继续（但仍需按真实价重算净额对照）。")
    else:
        print("    ⇒ ~ 间隔中位 %.0fms，完成率 %.1f%% ⇒ 介于两者之间，需谨慎。" % (gm, 100 * cr))
    if pm > 0:
        print("    且用**真实成交价**重算的净额也为正（%+.4fbp）⇒ 结论方向一致。" % pm)
    else:
        print("    但用**真实成交价**重算的净额为负（%+.4fbp）⇒ **H40 的正数不成立**。" % pm)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "hours": args.hours, "symbols": syms, "w_bp": args.w_bp,
        "per_symbol": res,
        "summary": {"n_complete": tot, "complete_rate": cr,
                    "gap_ms_median": gm, "pnl_bp_mean": pm, "win_rate": wr},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
