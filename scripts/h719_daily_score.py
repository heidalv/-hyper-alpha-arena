# -*- coding: utf-8 -*-
"""[h719 阶段4 2026-10-02] 日净 USD 记分板 + 连续正净追踪。

总设计目标的验收标尺:"连续 5 交易日正净 USD"。本脚本按**北京自然日**统计
(与 lane_ledger 口径一致):每交易日 腿数/净U/每腿bp/名义,并计算当前
连续正净天数。输出 data/daily_score_last.json;计划任务每天 08:05(北京)
跑一次(交易日刚翻页)。

附加:按天记"活跃小时数"与"净/活跃小时",避免拿半天数据和全天比。
"""
from __future__ import annotations

import importlib.util
import io
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

LANE = "mm_asterdex"


def _dsn() -> str:
    _spec = importlib.util.spec_from_file_location("h425", ROOT / "scripts" / "h425_repair_trial.py")
    _h = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_h)
    return _h.read_env_dsn()


def main() -> int:
    import psycopg

    with psycopg.connect(_dsn(), autocommit=True) as c, c.cursor() as cur:
        cur.execute(
            "SELECT (ts AT TIME ZONE 'Asia/Shanghai')::date d, count(*),"
            " SUM(net_bp*notional)/10000.0, AVG(net_bp), SUM(notional),"
            " extract(epoch from (max(ts)-min(ts)))/3600.0"
            " FROM lane_ledger WHERE lane_id=%s AND event='fill'"
            " AND ts > now() - interval '30 days'"
            " GROUP BY 1 ORDER BY 1", (LANE,))
        rows = cur.fetchall()
    days = []
    for d, n, net, bp, notional, hrs in rows:
        days.append({
            "date": str(d), "legs": int(n),
            "net_usd": round(float(net or 0), 4),
            "net_bp_per_leg": round(float(bp or 0), 3),
            "notional": round(float(notional or 0), 0),
            "active_hours": round(float(hrs or 0), 1),
            "net_per_active_h": round(float(net or 0) / float(hrs or 1), 4),
        })
    # 连续正净天数(从最后一天往回数;最后一天若是今天(未结束)也计入,标注 partial)
    streak = 0
    for day in reversed(days):
        if day["net_usd"] > 0:
            streak += 1
        else:
            break
    out = {"ts": time.time(), "streak_positive_days": streak, "days": days}
    (ROOT / "data" / "daily_score_last.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  {'日期':<12}{'腿':>5}{'净U':>9}{'每腿bp':>8}{'活跃h':>7}{'净/活跃h':>9}")
    for day in days[-10:]:
        print(f"  {day['date']:<12}{day['legs']:>5}{day['net_usd']:>+9.3f}"
              f"{day['net_bp_per_leg']:>+8.2f}{day['active_hours']:>7.1f}"
              f"{day['net_per_active_h']:>+9.4f}")
    print(f"\n连续正净交易日: {streak} 天(目标 5)")
    print("✓ 已写 data/daily_score_last.json")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                      errors="replace", line_buffering=True)
    raise SystemExit(main())
