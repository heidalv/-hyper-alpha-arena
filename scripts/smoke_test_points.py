# -*- coding: utf-8 -*-
"""积分引擎冒烟测试：估算公式 + 账本写读（测试行随后删除）。"""
import sys

sys.path.insert(0, "D:/001Alpha/Hyper-Alpha-Arena")
sys.path.insert(0, "D:/001Alpha/Hyper-Alpha-Arena/backend")

from backend.services.rebate_arb.live_points_engine import live_points_engine

# 确保表存在（后端重启时也会由 _safe_create_all 建表）
from backend.database.connection import analytics_engine
from backend.database.models import AsterdexLivePointsEvent

AsterdexLivePointsEvent.__table__.create(analytics_engine, checkfirst=True)
print("表就绪: asterdex_live_points_events")

# 测试注入「积分开关开」策略（真实环境由交易所配置的 points_enabled 控制）
import time as _t
live_points_engine._policy_cache = {
    "ts": _t.time(),
    "policy": {
        "enabled": True, "maker_first": True, "maker_timeout_s": 30.0,
        "asset_points_enabled": False, "account_id": None, "reason": "smoke_test_inject",
    },
}
print("开关策略注入: enabled=True")

print("== 估算公式 ==")
fee, pts = live_points_engine.est_trade_points(10000.0, maker=True)
print(f"  maker $10k: fee=${fee} trade_points={pts}")
fee2, pts2 = live_points_engine.est_trade_points(10000.0, maker=False)
print(f"  taker $10k: fee=${fee2} trade_points={pts2}")
print(f"  hold: $10k×4h = {live_points_engine.est_hold_points(10000.0, 4.0)} 分")

print("== 账本写读 ==")
oid = f"test_points_{int(__import__('time').time())}"
e1 = live_points_engine.record_fill(
    source="funding_arb", symbol="BTC", side="sell", qty=0.1, price=100000.0,
    maker=True, order_id=oid, session_id="smoke_test",
)
print("  open event id:", e1)
e2 = live_points_engine.record_close(
    source="funding_arb", symbol="BTC", order_id=oid, hold_hours=4.0,
    notional_usd=10000.0, reason="smoke",
)
print("  close event id:", e2)
assert e1 and e2

s = live_points_engine.get_summary(days=7)
print("  summary:", {k: s.get(k) for k in ("events", "trade_points", "hold_points", "fee_usd", "est_usd", "maker_ratio")})
assert not s.get("error")

# 清理测试行
from backend.database.connection import AnalyticsSessionLocal
from backend.database.models import AsterdexLivePointsEvent
from sqlalchemy import text as _t
db = AnalyticsSessionLocal()
try:
    n = db.execute(_t(
        "DELETE FROM asterdex_live_points_events WHERE order_id = :oid"
    ), {"oid": oid}).rowcount
    db.commit()
    print("  清理测试行:", n)
finally:
    db.close()

print("== 对账（无凭证时应优雅降级）==")
r = live_points_engine.reconcile()
print("  reconcile:", r)
print("ALL SMOKE PASSED")
