# -*- coding: utf-8 -*-
"""[F306 2026-09-16] 挂宽决策的**事先登记规则**（避免看着样本临时拍脑袋）。

背景：LINK 段 13:55 上线，w=30bp。F304 实测 15s 步进中位仅 0.46bp（≥15bp 占 0%）
⇒ 30bp 挂宽下成交是小时~天级 ⇒ 实盘样本积累慢。是否要为"更快看到样本"而把挂宽
降到 25bp，必须**事先**定死判据，否则就成了对着数据调参（本项目多次强调的纪律）。

模型侧两档证据（同一套判据：正天 ≥5/7、合计 >0、markout90 ≥0，锚定基准=线上口径）
  · w=30（在位）: 7/7 天、合计 **+2.096$**、markout90 **+25.12bp**、net_bp +32.6
  · w=25       : 6/7 天、合计 **+1.947$**、markout90 **+23.03bp**、net_bp +15.6
⇒ 收窄到 25 会**降低**每日期望（+2.10 → +1.95）却把成交机会放大约一倍
（样本可观测性 ↑）。这是"统计功效 vs 期望值"的取舍，不是优劣问题。

**事先登记规则**（本脚本按它给出建议，不自动改参数）
  R1. L1 段累计成交 ≥5 笔 ⇒ **保持 w=30**（样本已足够做对拍，无需牺牲期望值）。
  R2. 上线后 ≥24 小时 且 累计成交 <3 笔 ⇒ **建议降到 w=25**（用 −7% 期望值换取
      ~2× 样本速率，让"稳定正收益"的实盘证据能在数日内成形）。
  R3. 其余情况 ⇒ **继续等待**（不动）。
任何改动仍走 `mm_apply_params.py`（prev_params 可回滚），并登记 trials。

用法：python scripts/mm_width_decision.py [--since 2026-09-16T13:55:00+08:00]
"""
from __future__ import annotations

import argparse
import io
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
CST = timezone(timedelta(hours=8))
MODEL = {
    30.0: {"days_pos": "7/7", "total": 2.096, "mk90": 25.12, "net_bp": 32.57},
    25.0: {"days_pos": "6/7", "total": 1.947, "mk90": 23.03, "net_bp": 15.62},
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-09-16T13:55:00+08:00")
    a = ap.parse_args()
    from sqlalchemy import text

    from backend.core.tenant import system_identity
    from backend.database.connection import SessionLocal

    t0 = datetime.fromisoformat(a.since)
    with system_identity():
        with SessionLocal() as db:
            row = db.execute(text(
                "SELECT COUNT(*) n, COALESCE(SUM(points_usd),0) pts FROM lane_ledger"
                " WHERE lane_id='mm_asterdex' AND ts >= :t"
                "   AND COALESCE((meta_json->>'source'),'') <> 'reconcile'"),
                {"t": t0}).mappings().first()
    n = int(row["n"] or 0)
    hours = (datetime.now(CST) - t0).total_seconds() / 3600.0
    print(f"[F306] LINK 段: 上线 {hours:.1f} 小时, 成交 {n} 笔, 已实现 {float(row['pts'] or 0):+.4f}$")
    print(f"       模型证据: w=30 -> {MODEL[30.0]} ; w=25 -> {MODEL[25.0]}")
    if n >= 5:
        verdict = "R1 保持 w=30（样本已够做对拍，不牺牲期望值）"
    elif hours >= 24 and n < 3:
        verdict = "R2 建议降到 w=25（−7% 期望值换 ~2× 样本速率，需走 mm_apply_params.py 审计）"
    else:
        verdict = f"R3 继续等待（{hours:.1f}h / {n} 笔，未到 24h 阈值）"
    print(f"       判定: {verdict}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
