"""诊断：`lane_ledger` 的 `net_usd` 与模拟账户已实现盈亏为何差 137 倍。

现象（2026-09-20 18:58）：
  /api/hft/fills  -> summary.net_usd_sum = -15.8292   (67 行，24h)
  /api/hft/account -> paper.realized_usd = -0.1160    (同一车道同一时段)
两者答的是同一个问题，必然有一个口径错。

本脚本只做**事实核对**，不做修正：
  ① `lane_ledger` 六维原始行 -> 用 `net_bp/1e4*notional` 复算 net_usd
  ② `notional` 到底等于什么：单笔腿量($30) 还是 持仓名义(可变)？
     —— 若 > $30 的行存在，则 `notional` 是**持仓口径**（position-level），
        而 `net_bp` 是**本次腿**的口径 ⇒ 两者相乘无意义（量纲不一致）。
  ③ 按 `position_id` 分组看六维 sum，判断"持仓配对"口径下的真实净额。

用法：
    .venv\\Scripts\\python.exe scripts\\diag_hft_netusd_mismatch.py [--hours 24]
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:  # Windows 计划任务/schtasks 的控制台是 GBK，非 GBK 字符会直接抛异常中断脚本
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

DSN = (os.getenv("DATABASE_URL") or "")
for _p in ("+psycopg2", "+psycopg", "+asyncpg"):
    DSN = DSN.replace(_p, "")
if not DSN:
    raise SystemExit("DATABASE_URL 缺失（检查 .env）")

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
        SELECT id, symbol, event, ts, notional, spread_bp, funding_bp, price_bp,
               fee_bp, slippage_bp, net_bp, position_id, position_id_state
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

    # ① 复算 net_usd
    rec_net_usd = sum(float(r["net_bp"] or 0.0) / 1e4 * float(r["notional"] or 0.0) for r in rows)
    print("\n[1] 用 net_bp/1e4*notional 复算 net_usd_sum = %.6f" % rec_net_usd)

    # ② notional 的分布 —— 判断它是"腿量"还是"持仓名义"
    nots = [float(r["notional"] or 0.0) for r in rows]
    n_gt30 = sum(1 for v in nots if v > 31.0)
    print("\n[2] notional 分布（判断量纲）")
    print("    min=%.4f  max=%.4f  mean=%.4f  n=%d" % (min(nots), max(nots), sum(nots) / len(nots), len(nots)))
    print("    > $31 的行数 = %d / %d  (%.1f%%)" % (n_gt30, len(nots), 100.0 * n_gt30 / len(nots)))
    hist = defaultdict(int)
    for v in nots:
        if v < 5:
            hist["<5"] += 1
        elif v < 31:
            hist["5-31 (单腿)"] += 1
        elif v < 61:
            hist["31-61 (双腿)"] += 1
        elif v < 91:
            hist["61-91 (三腿)"] += 1
        else:
            hist[">91"] += 1
    for k in ["<5", "5-31 (单腿)", "31-61 (双腿)", "61-91 (三腿)", ">91"]:
        if hist.get(k):
            print("      %-16s %d" % (k, hist[k]))

    # ③ 六维 sum（bp 是无量纲比率，可加）
    six = ["spread_bp", "funding_bp", "price_bp", "fee_bp", "slippage_bp", "net_bp"]
    print("\n[3] 六维 bp 求和（bp 无量纲，可跨行相加）")
    for k in six:
        s = sum(float(r[k] or 0.0) for r in rows)
        print("    %-13s sum=%12.4f bp   avg=%9.4f bp" % (k, s, s / len(rows)))

    # ④ 用**单腿固定名义**折算：$30 腿量下的净额（这才是可比于账户的数字）
    leg = 30.0
    net_bp_sum = sum(float(r["net_bp"] or 0.0) for r in rows)
    print("\n[4] 若每行按 $%.0f 腿量折算：" % leg)
    print("    net_usd = net_bp_sum/1e4*leg = %.6f" % (net_bp_sum / 1e4 * leg))

    # ⑤ 按 position_id 分组（持仓配对口径）
    by_pos: dict[str, list] = defaultdict(list)
    for r in rows:
        by_pos[str(r["position_id"])].append(r)
    print("\n[5] 按 position_id 配对（同一 position_id 内的净额）")
    pos_net = 0.0
    for pid, rs in sorted(by_pos.items()):
        s = sum(float(r["net_bp"] or 0.0) for r in rs)
        nsum = sum(float(r["notional"] or 0.0) for r in rs)
        usd = s / 1e4 * leg
        pos_net += usd
        print("    %-18s rows=%2d  notional=%9.2f  net_bp=%9.4f  @$%.0f腿量=$%+.6f"
              % (pid, len(rs), nsum, s, leg, usd))
    print("    ---- 合计 $%+.6f ----" % pos_net)

    cur.close()
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
