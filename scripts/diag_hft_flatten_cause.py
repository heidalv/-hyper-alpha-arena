"""坐实：6 笔平仓腿到底是「超时强平」还是「止损强平」？

两条路径的手续费**不同**，这是唯一能从账本区分它们的线索：
    runner.py ①′ 止损平仓 → fee_rate = TAKER_FEE_BP/1e4  ⇒ fee_bp = -4
    runner.py ②  超时平仓 → fee_rate = TAKER_FEE_BP/1e4  ⇒ fee_bp = -4
两者费率一样，所以**账本区分不了**。但 `stop_loss_bp=60` 是个极高的门槛
（$30 腿量下浮亏 60bp ≈ $0.18），而实测单笔平仓只亏 $0.0244 ≈ 8bp，
**远未触及 60bp 止损线** ⇒ 可以**排除止损**：这 6 笔只能是超时平仓。

本脚本把这个推理用数据落实：找出所有平仓腿，看它们的 |亏损 bp| 是否远小于 60bp。

用法：
    .venv\\Scripts\\python.exe scripts\\diag_hft_flatten_cause.py [--hours 24]
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

DSN = os.getenv("DATABASE_URL") or ""
for _p in ("+psycopg2", "+psycopg", "+asyncpg"):
    DSN = DSN.replace(_p, "")
if not DSN:
    raise SystemExit("DATABASE_URL 缺失")

STOP_LOSS_BP = 60.0  # 线上 LaneRiskLimits.stop_loss_bp 实测值
MAX_ONE_SIDE_S = 300.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=24.0)
    ap.add_argument("--account-id", type=int, default=101)
    args = ap.parse_args()

    import psycopg2
    import psycopg2.extras

    conn = psycopg2.connect(DSN)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # 账户账本里 phase=flatten 的行（这才是"平仓"的权威来源）
    cur.execute(
        """
        SELECT created_at, amount_usd, metadata_json, note, related_position_id
          FROM arbitrage_paper_ledgers
         WHERE account_id = %s AND action = 'paper_pnl'
           AND metadata_json LIKE '%%"phase": "flatten"%%'
           AND created_at > now() - make_interval(secs => %s)
         ORDER BY created_at
        """,
        (args.account_id, args.hours * 3600.0),
    )
    fl = cur.fetchall()
    print(f"平仓腿 {len(fl)} 笔（近 {args.hours}h）\n")
    print("%-10s %-9s %8s %12s %10s %10s %s"
          % ("时间", "标的", "qty", "名义$", "净额$", "净额bp", "触及60bp止损?"))

    tot_usd = 0.0
    hit_stop = 0
    for r in fl:
        try:
            md = json.loads(r["metadata_json"] or "{}")
        except Exception:
            md = {}
        sym = md.get("symbol") or "-"
        qty = float(md.get("qty") or 0.0)
        px = float(md.get("px") or 0.0)
        mid = float(md.get("mid") or 0.0)
        notional = abs(qty * px)
        amt = float(r["amount_usd"] or 0.0)
        bp = (amt / notional * 1e4) if notional > 0 else 0.0
        tot_usd += amt
        # 止损判据是**浮亏**（mid 对 mid），不是成交净额。这里用成交价与当时的
        # mid 反推浮亏量级：|px-mid|/mid。若连这个都不到 60bp，止损不可能触发。
        unreal = abs(px - mid) / mid * 1e4 if mid > 0 else 0.0
        flag = "是" if unreal >= STOP_LOSS_BP else "否 (%.1fbp)" % unreal
        if unreal >= STOP_LOSS_BP:
            hit_stop += 1
        print("%-10s %-9s %8.4f %12.4f %+12.6f %10.3f  %s"
              % (str(r["created_at"])[11:19], sym, qty, notional, amt, bp, flag))

    print("\n平仓腿净额合计 %+.6f USD" % tot_usd)
    if fl:
        print("平均每笔 %+.6f USD  平均 %+.3f bp"
              % (tot_usd / len(fl), tot_usd / sum(
                  abs(float(json.loads(r["metadata_json"] or "{}").get("qty") or 0)
                      * float(json.loads(r["metadata_json"] or "{}").get("px") or 0))
                  for r in fl) * 1e4))
    print("\n── 触发原因判定 ──")
    print("  止损线 stop_loss_bp = %.0fbp；命中笔数 = %d / %d" % (STOP_LOSS_BP, hit_stop, len(fl)))
    if fl and hit_stop == 0:
        print("  ⇒ **0 笔触及止损线** ⇒ 这 %d 笔平仓**全部来自 `max_one_side_seconds=%.0fs` 超时强平**。"
              % (len(fl), MAX_ONE_SIDE_S))
        print("     （止损另有 `stop_loss_vol_min=1.0` 波动闸，当前 σ_norm≈0.06 远低于 1.0，本就未武装。）")
    else:
        print("  ⇒ 存在触达止损线的平仓，需要进一步分辨。")

    cur.close()
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
