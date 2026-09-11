# -*- coding: utf-8 -*-
"""双所价差修正验证：用生产 perp_funding 数据跑 _apply_funding_pair_spread。"""
import sys
import types

sys.path.insert(0, "D:/001Alpha/Hyper-Alpha-Arena")
sys.path.insert(0, "D:/001Alpha/Hyper-Alpha-Arena/backend")

from backend.services.arbitrage.orchestrator import arbitrage_orchestrator
from backend.services.arbitrage.opportunity_scanner import ArbitrageOpportunity, FundingRateSnapshot

# 构造模拟扫描结果（单所费率年化故意放大，验证被价差修正）
opps = []
for sym in ("BTC", "ETH", "SOL"):
    opps.append(ArbitrageOpportunity(
        opportunity_id=f"test_{sym}",
        symbol=sym,
        strategy="funding_short",
        expected_annual_yield=0.30,
        funding_snapshot=FundingRateSnapshot(
            symbol=sym, current_rate=0.0003, predicted_rate=0.0,
            rate_8h_avg=0.0003, rate_24h_avg=0.0003, annual_yield=0.30,
            oi_total=1_000_000.0, volume_24h=1_000_000_000.0,
        ),
        recommended_size=0.0, risk_score=0.3, confidence=0.7,
        timestamp=0.0,
    ))

print("修正前:", [(o.symbol, o.strategy, round(o.expected_annual_yield, 4)) for o in opps])
kept = arbitrage_orchestrator._apply_funding_pair_spread(opps)
print("修正后:")
for o in kept:
    print(f"  {o.symbol}: strategy={o.strategy} annual={o.expected_annual_yield:.4f} "
          f"spread_rate={o.funding_snapshot.current_rate:.6f}")
print("剔除数:", len(opps) - len(kept))
print("DONE")
