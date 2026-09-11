# -*- coding: utf-8 -*-
"""浮盈锁上线验证（Y17）：读生产 ExitPolicy 与未平仓位的锁定价位。"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env", override=False)

from backend.services.exit.exit_policy import ExitPolicy, describe, ExitSnapshot, evaluate  # noqa: E402

print("=== 生产 ExitPolicy（各车道）===")
for lane, p in describe()["lanes"].items():
    print(f"  {lane:<6} SL={p['sl_pct']} TP={p['tp_pct']} time={p['time_limit_sec']} "
          f"trail={p['trailing_activation_pct']}/{p['trailing_callback_pct']} "
          f"structural={p['structural_stop']} min_roi={p['min_roi']}")

mid = ExitPolicy.for_lane("mid")
print(f"\nmid 浮盈锁：激活 {mid.trailing_activation_pct}% / 回撤 {mid.trailing_callback_pct}%")
print(f"防抖步长：{os.getenv('EXIT_POLICY_TRAILING_MIN_STEP_PCT')}")

print("\n=== 行为抽样 ===")
for peak, cur in ((0.3, 0.3), (0.6, 0.5), (0.6, 0.4), (1.5, 1.4), (1.5, 1.2), (3.0, 2.8)):
    snap = ExitSnapshot(side="long", entry=100.0, current=100.0 * (1 + cur / 100),
                        elapsed_sec=3600, peak_roi_pct=peak, sl_price=94.0)
    v = evaluate(mid, snap)
    print(f"  峰值={peak:>4.1f}% 当前={cur:>4.1f}% → {v.action:<11} "
          f"{v.reason:<18} new_sl={v.new_sl}")

print("\n=== 当前未平仓（含锁定价位推算）===")
from sqlalchemy import create_engine, text  # noqa: E402

eng = create_engine(os.getenv("DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena"))
with eng.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    for r in c.execute(text("""
        select symbol, side, timeframe_tier, entry_price, mark_price, sl_price, peak_pnl_pct
        from paper_positions where status='open' and timeframe_tier in ('mid','long')
        order by opened_at
    """)).fetchall():
        d = dict(r._mapping)
        entry = float(d["entry_price"] or 0)
        mark = float(d["mark_price"] or 0)
        peak = float(d["peak_pnl_pct"] or 0) * 100
        sign = 1.0 if str(d["side"]) == "long" else -1.0
        cur = sign * (mark - entry) / entry * 100 if entry > 0 else 0
        lock = None
        if peak >= (mid.trailing_activation_pct or 1e9):
            lock = entry * (1 + sign * (peak - mid.trailing_callback_pct) / 100)
        print(f"  {d['symbol']:<8} {str(d['side']):<5} {d['timeframe_tier']:<5} "
              f"峰值={peak:>+5.2f}% 当前={cur:>+5.2f}% SL={d['sl_price']} "
              f"锁定SL={lock if lock is None else round(lock, 6)}")
