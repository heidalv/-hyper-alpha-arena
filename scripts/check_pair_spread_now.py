# -*- coding: utf-8 -*-
"""当前 asterdex vs binance 资金费价差（近 7 天分位）。"""
import sys

sys.path.insert(0, "D:/001Alpha/Hyper-Alpha-Arena")
from backend.services.rebate_arb.funding_rate_provider import latest_funding_by_venue

venues = latest_funding_by_venue(use_cache=False)
print("当前最新费率:")
for sym in ("BTC", "ETH", "SOL", "BNB", "DOGE", "XRP"):
    want = f"{sym}/USDT"
    adx = (venues.get("asterdex") or {}).get(want)
    bnb = (venues.get("binance") or {}).get(want)
    if adx is None or bnb is None:
        print(f"  {sym}: 数据缺失 adx={adx} bin={bnb}")
        continue
    spread = adx - bnb
    annual = abs(spread) * 3 * 365
    print(f"  {sym}: asterdex={adx:.6f} binance={bnb:.6f} spread={spread:.6f} "
          f"annual={annual*100:.2f}% {'★机会' if annual >= 0.15 else ''}")
print("DONE")
