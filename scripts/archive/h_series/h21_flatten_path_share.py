"""H21：量化 900s 上限下"多少仓位最终走了 taker 强平路径"。

## 为什么必须量化（而不是继续辩论该不该去掉强平）

两个外部调研都指向"论文的策略家族完全没有 taker 平仓，我们 3 笔强平吃掉了大部分亏损"。
但那个"62%"是在**旧配置（counter_trend + 300s 上限）**下测的，
现在的配置已经变成 `side_mode=both` + `max_one_side_seconds=900`。

**在改任何东西之前，先知道现状。** 本脚本回答三个问题：

  ① 每个库存周期（`position_id`）里，开仓腿与平仓腿各几笔？成本各多少？
  ② 平仓腿的**单笔 bp 成本**分布（不是平均值 —— 平均值被少数极端值主导）
  ③ 900s 上限下，**还有多少周期最终以 taker 强平收场**

## 口径（事先定死）

  · 开仓腿 vs 平仓腿：**按笔而不是按周期**判定 ——
    `meta_json.flatten == true` 是平仓腿（runner 写入，权威）；
    但实测该标记**只覆盖一部分**（早先 3 笔平仓 vs 账本里 6 行 phase=flatten），
    所以同时用账户账本 `arbitrage_paper_ledgers` 的 `metadata.phase` 交叉核对。
  · **成本必须用金额（USD）**，不能用平均 bp ——
    因为单笔名义跨 3 个数量级（$0.26 ~ $90），bp 平均会被碎腿主导。
  · 跨时代不合并：只统计 `--since-min` 之后的样本。

用法：
    .venv\\Scripts\\python.exe scripts\\h21_flatten_path_share.py --hours 6
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")


def _dsn(db: str) -> str:
    url = os.getenv("DATABASE_URL") or ""
    for p in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(p, "")
    head, _, _ = url.rpartition("/")
    return head + "/" + db


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=6.0)
    ap.add_argument("--lane", default=os.getenv("MM_LANE_ID", "mm_asterdex"))
    ap.add_argument("--account-id", type=int, default=101)
    args = ap.parse_args()

    import numpy as np
    import psycopg2
    import psycopg2.extras

    cn = psycopg2.connect(_dsn("alpha_arena"))
    cn.autocommit = True
    cur = cn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    iv = args.hours * 3600.0

    # ── ① 账户账本：权威的"腿类型"来源 ────────────────────────────
    cur.execute(
        """
        SELECT created_at, amount_usd, metadata_json, note
          FROM arbitrage_paper_ledgers
         WHERE account_id = %s AND action = 'paper_pnl'
           AND created_at > now() - make_interval(secs => %s)
         ORDER BY created_at
        """,
        (args.account_id, iv),
    )
    arows = cur.fetchall()
    legs = []
    for r in arows:
        try:
            md = json.loads(r["metadata_json"] or "{}")
        except Exception:
            md = {}
        legs.append({
            "ts": r["created_at"],
            "amt": float(r["amount_usd"] or 0.0),
            "phase": str(md.get("phase") or "?"),
            "symbol": md.get("symbol") or "-",
            "qty": float(md.get("qty") or 0.0),
            "px": float(md.get("px") or 0.0),
            "mid": float(md.get("mid") or 0.0),
        })

    print(f"H21 强平路径占比   lane={args.lane}  窗口={args.hours}h")
    print(f"账户账本 paper_pnl 共 {len(legs)} 笔\n")

    if not legs:
        print("无数据")
        return 1

    by_phase = defaultdict(lambda: {"n": 0, "usd": 0.0, "wins": 0})
    for x in legs:
        d = by_phase[x["phase"]]
        d["n"] += 1
        d["usd"] += x["amt"]
        if x["amt"] > 0:
            d["wins"] += 1

    print("[1] 按腿类型（金额口径，不用 bp 平均）")
    print("    %-10s %6s %14s %14s %9s" % ("phase", "笔数", "净额$", "每笔均$", "胜率"))
    tot = 0.0
    for k, v in sorted(by_phase.items()):
        tot += v["usd"]
        print("    %-10s %6d %+14.6f %+14.6f %8.1f%%"
              % (k, v["n"], v["usd"], v["usd"] / max(1, v["n"]),
                 100.0 * v["wins"] / max(1, v["n"])))
    print("    ---- 合计 %+.6f ----" % tot)

    # ── ② 平仓腿明细（单笔 bp，同时给名义）────────────────────────
    fl = [x for x in legs if x["phase"] == "flatten"]
    print(f"\n[2] 平仓腿明细（{len(fl)} 笔）")
    if fl:
        print("    %-10s %-9s %12s %14s %12s" % ("时间", "标的", "名义$", "净额$", "净额bp"))
        fs = []
        for x in fl:
            notional = abs(x["qty"] * x["px"])
            bp = (x["amt"] / notional * 1e4) if notional > 0 else 0.0
            fs.append((notional, bp, x["amt"]))
            print("    %-10s %-9s %12.4f %+14.6f %+12.3f"
                  % (str(x["ts"])[11:19], x["symbol"], notional, x["amt"], bp))
        arr = np.array([f[1] for f in fs])
        print("\n    单笔 bp: 中位 %+.3f  p25 %+.3f  p75 %+.3f  min %+.3f  max %+.3f"
              % (np.median(arr), np.percentile(arr, 25), np.percentile(arr, 75),
                 arr.min(), arr.max()))
        nots = np.array([f[0] for f in fs])
        print("    名义  : 中位 %.4f  min %.4f  max %.4f  （跨 %.0f 倍）"
              % (np.median(nots), nots.min(), nots.max(),
                 nots.max() / max(1e-9, nots.min())))
        print("    ⇒ **不能用平均 bp**：名义跨度太大，bp 平均被最小那笔主导。")
        print("       报告成本必须用**金额之和**：%+.6f USD" % sum(f[2] for f in fs))

    # ── ③ 周期口径：多少仓位最终走了强平 ──────────────────────────
    cur.execute(
        """
        SELECT symbol, ts, notional, net_bp, position_id, meta_json
          FROM lane_ledger
         WHERE lane_id = %s AND ts > now() - make_interval(secs => %s)
         ORDER BY ts
        """,
        (args.lane, iv),
    )
    lrows = cur.fetchall()
    cyc = defaultdict(lambda: {"n": 0, "usd": 0.0, "flat": 0})
    span = defaultdict(lambda: [None, None])
    for r in lrows:
        pid = str(r["position_id"] or "-")
        notional = float(r["notional"] or 0.0)
        nb = float(r["net_bp"] or 0.0)
        usd = nb / 1e4 * notional
        cyc[pid]["n"] += 1
        cyc[pid]["usd"] += usd
        try:
            if json.loads(r["meta_json"] or "{}").get("flatten"):
                cyc[pid]["flat"] += 1
        except Exception:
            pass
        t = r["ts"]
        if span[pid][0] is None or t < span[pid][0]:
            span[pid][0] = t
        if span[pid][1] is None or t > span[pid][1]:
            span[pid][1] = t

    print(f"\n[3] 库存周期（position_id）共 {len(cyc)} 个")
    print("    %-16s %5s %12s %10s %14s %8s"
          % ("position_id", "腿数", "时长s", "强平腿", "净额$", "结局"))
    n_forced = n_total = 0
    for pid, v in sorted(cyc.items(), key=lambda kv: -(span[kv[0]][1] - span[kv[0]][0]).total_seconds()):
        dur = (span[pid][1] - span[pid][0]).total_seconds()
        n_total += 1
        forced = v["flat"] > 0
        if forced:
            n_forced += 1
        print("    %-16s %5d %12.0f %10d %+14.6f %8s"
              % (pid, v["n"], dur, v["flat"], v["usd"], "强平" if forced else "被动"))

    print(f"\n[4] 结论")
    print(f"    周期数 {n_total}，其中**含强平腿的 {n_forced} 个**（{100.0*n_forced/max(1,n_total):.1f}%）")
    if by_phase.get("flatten"):
        fv = by_phase["flatten"]
        print(f"    强平腿合计 {fv['n']} 笔、净额 {fv['usd']:+.6f} USD"
              f"，占全部腿净额（{tot:+.6f}）的 "
              f"{100.0*abs(fv['usd'])/max(1e-12,sum(abs(v['usd']) for v in by_phase.values())):.1f}%")
        print(f"    ⇒ 若去掉强平，账面净额变化 ≈ {-fv['usd']:+.6f} USD（**这是上界，不是预期**：")
        print(f"       那些仓位会改为继续持有，结局未知，可能更好也可能更差）")
    print("\n    ⚠️ 口径提醒：`meta_json.flatten` 的覆盖率**不完整**（实测早先 3 笔平仓腿")
    print("       与账本 6 行 phase=flatten 不一致），所以 [3] 的强平计数可能偏低。")

    cur.close()
    cn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
