"""诊断：库存能否**被动**出掉（这决定敞口闸会不会把车道闷死）。

背景（2026-09-20 19:00）：
  总敞口 $318 已顶到上限 $300（= 权益 × max_gross_notional_ratio 1.0）
  ⇒ 10 个币的**加仓腿全部被拒**，只剩减仓腿能挂。
  若库存出不去，车道就永久卡在"满仓 + 只挂单边"的状态。

两种出库路径的成本量级完全不同：
  · **被动出库**（对手腿被吃掉）：maker 0% 手续费，成本 0
  · **超时强平**（max_one_side_seconds=300 后 taker）：4bp 手续费
    —— 在加权净边际仅 -0.85bp 的策略上，一次 4bp 强平相当于抹掉 4.7 倍的单笔盈亏

本脚本按 position_id 统计持仓时长与平仓方式，直接回答：
  ① 有多少仓位是被动出掉的、多少是超时/止损强平的？
  ② 持仓时长分布 vs max_one_side_seconds(300s)
  ③ 强平腿贡献了多少费用（fee_bp）

用法：
    .venv\\Scripts\\python.exe scripts\\diag_hft_inventory_turnover.py [--hours 24]
"""
from __future__ import annotations

import argparse
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

DSN = os.getenv("DATABASE_URL") or ""
for _p in ("+psycopg2", "+psycopg", "+asyncpg"):
    DSN = DSN.replace(_p, "")
if not DSN:
    raise SystemExit("DATABASE_URL 缺失")

LANE = os.getenv("MM_LANE_ID", "mm_asterdex")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=24.0)
    ap.add_argument("--lane", default=LANE)
    args = ap.parse_args()

    import psycopg2
    import psycopg2.extras

    conn = psycopg2.connect(DSN)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        """
        SELECT symbol, ts, event, notional, spread_bp, price_bp, fee_bp, net_bp,
               position_id, position_id_state, meta_json
          FROM lane_ledger
         WHERE lane_id = %s
           AND ts > now() - make_interval(secs => %s)
         ORDER BY ts
        """,
        (args.lane, args.hours * 3600.0),
    )
    rows = cur.fetchall()
    print(f"lane={args.lane}  hours={args.hours}  rows={len(rows)}")
    if not rows:
        return 0

    # ① 平仓腿识别：meta_json.flatten == true（runner._record_fills 写入）
    import json
    n_flat = 0
    flat_fee_bp = 0.0
    flat_net_bp = 0.0
    open_net_bp = 0.0
    open_n = 0
    for r in rows:
        try:
            meta = json.loads(r["meta_json"] or "{}")
        except Exception:
            meta = {}
        is_flat = bool(meta.get("flatten"))
        if is_flat:
            n_flat += 1
            flat_fee_bp += float(r["fee_bp"] or 0.0)
            flat_net_bp += float(r["net_bp"] or 0.0)
        else:
            open_n += 1
            open_net_bp += float(r["net_bp"] or 0.0)

    print("\n[1] 腿的构成（meta_json.flatten 判定）")
    print("    开仓腿 %d 笔   net_bp 合计 %+9.2f bp" % (open_n, open_net_bp))
    print("    平仓腿 %d 笔   net_bp 合计 %+9.2f bp   fee_bp 合计 %+9.2f bp"
          % (n_flat, flat_net_bp, flat_fee_bp))
    if n_flat:
        print("    平仓腿平均 net_bp = %+.3f bp  ← 4bp taker 费率的代价" % (flat_net_bp / n_flat))

    # ② 按 position_id 聚合：持仓时长（首腿→末腿）
    by_pos: dict[str, list] = defaultdict(list)
    for r in rows:
        by_pos[str(r["position_id"] or "-")].append(r)

    print("\n[2] 持仓周期（按 position_id，首腿→末腿）")
    print("    %-16s %4s %8s %10s %9s %9s" % ("position_id", "腿数", "时长s", "notional", "net_bp", "强平?"))
    holds = []
    for pid, rs in sorted(by_pos.items()):
        t0 = min(r["ts"] for r in rs)
        t1 = max(r["ts"] for r in rs)
        dur = (t1 - t0).total_seconds()
        nb = sum(float(r["net_bp"] or 0.0) for r in rs)
        ns = sum(float(r["notional"] or 0.0) for r in rs)
        fl = False
        for r in rs:
            try:
                if json.loads(r["meta_json"] or "{}").get("flatten"):
                    fl = True
            except Exception:
                pass
        holds.append(dur)
        print("    %-16s %4d %8.0f %10.2f %+9.3f %9s" % (pid, len(rs), dur, ns, nb, "是" if fl else ""))

    if holds:
        holds_s = sorted(holds)
        n = len(holds_s)
        print("\n[3] 持仓时长分布")
        print("    min=%.0fs  p25=%.0fs  p50=%.0fs  p75=%.0fs  max=%.0fs"
              % (holds_s[0], holds_s[n // 4], holds_s[n // 2], holds_s[3 * n // 4], holds_s[-1]))
        over = sum(1 for h in holds_s if h > 300)
        print("    超过 max_one_side_seconds(300s) 的周期数 = %d / %d (%.1f%%)" % (over, n, 100.0 * over / n))

    cur.close()
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
