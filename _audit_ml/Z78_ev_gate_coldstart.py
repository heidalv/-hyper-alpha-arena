# -*- coding: utf-8 -*-
"""Z78: mid/long EV 闸是否一直处于「冷启动影子放行」？（校准样本真实存量）"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env")

from sqlalchemy import text  # noqa: E402
from backend.database.connection import SessionLocal  # noqa: E402
from backend.services.calibration.confidence_calibrator import (  # noqa: E402
    get_calibrator_for_nature,
)
from backend.services.decision_core import midlong_ev_gate as evg  # noqa: E402

print("=== A. 配置：EV 闸与校准器开关 ===")
import os  # noqa: E402
for k in ("MIDLONG_EV_ENFORCE_REQUIRES_CALIBRATION", "MIDLONG_CALIBRATOR_ENABLED",
          "SWING_CALIBRATOR_ENABLED", "TREND_CALIBRATOR_ENABLED",
          "SWING_CALIBRATOR_MIN_SAMPLES", "TREND_CALIBRATOR_MIN_SAMPLES"):
    print(f"  {k} = {os.getenv(k)}")

print("\n=== B. 校准器当前状态（生产函数）===")
for nat in ("swing", "trend_follow", "position"):
    try:
        cal = get_calibrator_for_nature(nat)
    except Exception as e:
        print(f"  {nat}: 取校准器失败 {str(e)[:80]}")
        continue
    try:
        model = cal._get_model()
        print(f"  {nat}: signal_type={cal._signal_type} prefix={cal._prefix}")
        print(f"       model={'None' if model is None else f'n={model.n_samples} calibrated={model.is_calibrated} base={model.base_rate:.3f}'}")
        r = cal.estimate_p_win("ETH", 60.0, "long")
        print(f"       estimate_p_win(60) = p={r.p_win} source={r.source} n={r.n_samples} note={r.note}")
        print(f"       最小样本门槛 = {cal._cfg('MIN_SAMPLES', '?')}")
    except Exception as e:
        print(f"  {nat}: 评估失败 {str(e)[:120]}")

print("\n=== C. 真实数据：SignalTradeFeedback 样本存量 ===")
db = SessionLocal()
try:
    for r in db.execute(text(
        "select signal_type, count(*), count(realized_pnl) as with_pnl, "
        "count(distinct symbol) from signal_trade_feedback group by 1 order by 2 desc"
    )).fetchall():
        print("  ", [str(x) for x in r])
    print("  最近 5 行:")
    for r in db.execute(text(
        "select created_at, signal_type, symbol, signal_value, realized_pnl "
        "from signal_trade_feedback order by id desc limit 5"
    )).fetchall():
        print("     ", [str(x) for x in r])
except Exception as e:
    db.rollback()
    print("  查询失败:", str(e)[:140])
finally:
    db.close()

print("\n=== D. EV 闸影子放行的真实计数（进程内统计，若可用）===")
try:
    g = evg.midlong_ev_gate
    print("  stats:", g.stats() if hasattr(g, "stats") else "无 stats()")
except Exception as e:
    print("  取统计失败:", str(e)[:120])
