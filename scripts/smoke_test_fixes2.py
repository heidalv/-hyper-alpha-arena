# -*- coding: utf-8 -*-
"""汇流权重归一化 + kline_enrichment CVD 口径验证。"""
import sys

sys.path.insert(0, "D:/001Alpha/Hyper-Alpha-Arena")
sys.path.insert(0, "D:/001Alpha/Hyper-Alpha-Arena/backend")

print("=" * 60)
print("[A] 汇流权重归一化：只有 funding 可用时，composite 应等于 funding 分数")
from backend.services.intelligence_signal_engine import (
    IntelligenceSignalEngine, TradingDirectionSignal, FundingRegime,
)
eng = IntelligenceSignalEngine()
sig = TradingDirectionSignal(symbol="BTC")
sig.funding = FundingRegime(rate=0.0012, regime="extreme_positive",
                            signal="bearish", description="x", percentile=98.0)
sig.sources_available = {
    "funding": True, "oi": False, "liquidation": False, "whale": False,
    "news": False, "sentiment": False, "ls_ratio": False, "top_trader": False,
}
eng._compute_confluence(sig)
# funding 权重 0.22 唯一参与 → composite = -1.0（bearish 满分）
print(f"  direction={sig.direction} confidence={sig.confidence}")
assert sig.direction == "bearish" and sig.confidence == 100, "归一化后 funding 应独立主导"
print("  ✓ 不可用组件不再稀释（修复前 composite 会被稀释到 ~-0.22 → neutral）")

print("=" * 60)
print("[B] 全部不可用 → neutral/0 且不崩")
sig2 = TradingDirectionSignal(symbol="ETH")
sig2.sources_available = {k: False for k in (
    "funding", "oi", "liquidation", "whale", "news", "sentiment", "ls_ratio", "top_trader")}
eng._compute_confluence(sig2)
print(f"  direction={sig2.direction} confidence={sig2.confidence}")
assert sig2.direction == "neutral" and sig2.confidence == 0
print("  ✓ 全缺失 → 诚实 neutral")

print("=" * 60)
print("[C] kline_enrichment 按激活所（binance）取 CVD，不再硬编码 hyperliquid")
import time as _t
import pandas as pd
from backend.services.kline_enrichment_service import (
    attach_flow_timeseries_to_df, _active_flow_exchanges,
)
from backend.database.connection import MarketSessionLocal
print(f"  active_flow_exchanges={_active_flow_exchanges()}")
end_ms = int(_t.time() * 1000)
# 取近 2 小时 5m K 线窗口
idx = pd.to_datetime(pd.Series(range(end_ms - 7200_000, end_ms, 300_000)), unit="ms")
df = pd.DataFrame({"timestamp": idx.astype("int64") // 10**9, "close": 1.0})
with MarketSessionLocal() as db:
    out = attach_flow_timeseries_to_df(db, "BTC", df, "5m")
ok_col = out["flow_data_ok"]
print(f"  flow_data_ok={bool(ok_col.all())} "
      f"cvd_delta 非零值 {int(out['cvd_delta'].notna().sum())}/{len(out)}")
assert bool(ok_col.all()), "激活所 CVD 数据应可用"
assert out["cvd_delta"].notna().any()
print("  ✓ CVD 口径已对齐现网激活所")

print("=" * 60)
print("ALL CHECKS PASSED")
